"""Peer endpoints and deterministic transport; no simulation/world imports."""
from dataclasses import dataclass
import json
import random
from typing import TYPE_CHECKING, Protocol

from .knowledge import LocalKnowledge
from .messages import (AgentStatus, CoordinationPayload, DeliveryReceipt, ObservationPayload,
                       ReceivedStatus, decode_payload, encode_payload)
from .models import Availability, MessageEnvelope, _id, _number, _tick

if TYPE_CHECKING:
    from .interfaces import AgentView


@dataclass(frozen=True)
class Disconnection:
    agent_id: str
    start_tick: int
    end_tick: int

    def __post_init__(self) -> None:
        _id(self.agent_id)
        _tick(self.start_tick)
        _tick(self.end_tick)
        if self.end_tick <= self.start_tick:
            raise ValueError("disconnection interval must have positive length")


@dataclass(frozen=True)
class CommunicationConfiguration:
    latency_ticks: int = 1
    loss_probability: float = 0
    ttl_ticks: int = 6
    links: tuple[tuple[str, str], ...] | None = None
    disconnections: tuple[Disconnection, ...] = ()

    def __post_init__(self) -> None:
        _tick(self.latency_ticks)
        _tick(self.ttl_ticks)
        if self.latency_ticks == 0 or self.ttl_ticks == 0:
            raise ValueError("latency and TTL must be positive")
        _number(self.loss_probability, high=1)
        if self.links is not None:
            if not isinstance(self.links, tuple):
                raise ValueError("links must be a tuple")
            for pair in self.links:
                if not isinstance(pair, tuple) or len(pair) != 2:
                    raise ValueError("link must be a directed pair")
                _id(pair[0])
                _id(pair[1])
                if pair[0] == pair[1]:
                    raise ValueError("self links are invalid")
            if len(set(self.links)) != len(self.links):
                raise ValueError("duplicate link")
        if not isinstance(self.disconnections, tuple) or any(not isinstance(d, Disconnection) for d in self.disconnections):
            raise ValueError("invalid disconnections")


@dataclass(frozen=True)
class CommunicationRecord:
    tick: int
    action: str
    envelope: MessageEnvelope
    reason: str = ""


@dataclass(frozen=True)
class ProcessedMessage:
    receipt: DeliveryReceipt
    result: str
    reason: str = ""


class AgentEndpoint:
    """Own knowledge and queues only; no engine, transport or peer-store reference."""
    def __init__(self, knowledge: LocalKnowledge, peers: tuple[str, ...], ttl_ticks: int = 6):
        if not isinstance(knowledge, LocalKnowledge):
            raise ValueError("endpoint requires its own knowledge store")
        if not isinstance(peers, tuple):
            raise ValueError("peer contacts must be a tuple")
        for peer in peers:
            _id(peer)
        if knowledge.agent_id in peers or len(set(peers)) != len(peers):
            raise ValueError("invalid peer contacts")
        _tick(ttl_ticks)
        if ttl_ticks == 0:
            raise ValueError("TTL must be positive")
        self._knowledge = knowledge
        self._peers = tuple(sorted(peers))
        self._ttl = ttl_ticks
        self.reset()

    @property
    def agent_id(self) -> str:
        return self._knowledge.agent_id

    @property
    def peers(self) -> tuple[str, ...]:
        return self._peers

    def knowledge_snapshot(self):
        return self._knowledge.snapshot()

    @property
    def received_this_tick(self) -> tuple[DeliveryReceipt, ...]:
        return tuple(self._recent)

    @property
    def received_history(self) -> tuple[ProcessedMessage, ...]:
        return tuple(self._processed)

    @property
    def peer_statuses(self) -> tuple[ReceivedStatus, ...]:
        return tuple(self._statuses[k] for k in sorted(self._statuses))

    def reset(self) -> None:
        self._counter = 0
        self._outgoing: list[MessageEnvelope] = []
        self._incoming: list[DeliveryReceipt] = []
        self._sent: dict[str, MessageEnvelope] = {}
        self._received: dict[str, MessageEnvelope] = {}
        self._processed: list[ProcessedMessage] = []
        self._recent: list[DeliveryReceipt] = []
        self._statuses: dict[str, ReceivedStatus] = {}
        self._shared: set[tuple[str, str]] = set()
        self._refresh: dict[tuple[str, str, str, str], tuple[tuple[str, float], int]] = {}

    def send(self, envelope: MessageEnvelope, now: int) -> bool:
        _tick(now)
        if not isinstance(envelope, MessageEnvelope) or envelope.sender_id != self.agent_id:
            raise ValueError("endpoint cannot spoof another sender")
        if envelope.recipient_id not in self.peers or envelope.sent_at != now:
            raise ValueError("invalid recipient or creation tick")
        payload = decode_payload(envelope)
        if isinstance(payload, ObservationPayload):
            if any(peer not in (*self.peers, self.agent_id) for peer in payload.path):
                raise ValueError("unknown provenance identity")
            entry = next((e for e in self._knowledge.history if e.observation.observation_id == payload.observation.observation_id), None)
            if entry is None or entry.observation != payload.observation or (entry.path or (self.agent_id,)) != payload.path:
                raise ValueError("observation must match this endpoint's recorded evidence and provenance")
        previous = self._sent.get(envelope.message_id)
        if previous is not None:
            if previous != envelope:
                raise ValueError("message ID reused with changed content")
            return False
        self._sent[envelope.message_id] = envelope
        self._outgoing.append(envelope)
        return True

    def _create(self, payload, recipient_id: str, now: int, expires_at: int | None = None) -> MessageEnvelope:
        kind, text = encode_payload(payload)
        identity = "message:" + json.dumps([self.agent_id, self._counter], separators=(",", ":"))
        envelope = MessageEnvelope(identity, self.agent_id, recipient_id, kind, now, text,
                                   now + self._ttl if expires_at is None else expires_at)
        self.send(envelope, now)
        self._counter += 1
        return envelope

    def share_observation(self, observation_id: str, recipient_id: str, now: int, *, expires_at: int | None = None) -> MessageEnvelope | None:
        if recipient_id not in self.peers:
            raise ValueError("invalid peer recipient")
        if (observation_id, recipient_id) in self._shared:
            return None
        entry = next((e for e in self._knowledge.history if e.observation.observation_id == observation_id), None)
        if entry is None:
            raise ValueError("unknown local evidence ID")
        envelope = self._create(ObservationPayload(entry.observation, entry.path or (self.agent_id,)), recipient_id, now, expires_at)
        self._shared.add((observation_id, recipient_id))
        return envelope

    def broadcast_observation(self, observation_id: str, now: int) -> tuple[MessageEnvelope, ...]:
        return tuple(e for peer in self.peers if (e := self.share_observation(observation_id, peer, now)) is not None)

    def send_status(self, status: AgentStatus, recipient_id: str, now: int) -> MessageEnvelope:
        return self._create(status, recipient_id, now)

    def send_coordination(self, topic: str, data: str, recipient_id: str, now: int) -> MessageEnvelope:
        return self._create(CoordinationPayload(topic, data), recipient_id, now)

    def drain_outgoing(self) -> tuple[MessageEnvelope, ...]:
        result = tuple(self._outgoing)
        self._outgoing.clear()
        return result

    def receive(self, receipt: DeliveryReceipt) -> None:
        if not isinstance(receipt, DeliveryReceipt) or receipt.envelope.recipient_id != self.agent_id:
            raise ValueError("receipt addressed to another endpoint")
        self._incoming.append(receipt)

    def process(self, now: int) -> tuple[ProcessedMessage, ...]:
        _tick(now)
        self._recent = []
        result = []
        pending = sorted(self._incoming, key=lambda r: (r.delivered_at, r.envelope.message_id))
        self._incoming = []
        for receipt in pending:
            envelope = receipt.envelope
            if receipt.delivered_at > now:
                self._incoming.append(receipt)
                continue
            try:
                if envelope.expires_at is not None and now >= envelope.expires_at:
                    raise ValueError("expired message")
                if envelope.sender_id not in self.peers:
                    raise ValueError("unknown peer sender")
                if envelope.message_id in self._received:
                    if self._received[envelope.message_id] != envelope:
                        raise ValueError("message ID reused with changed content")
                    result.append(ProcessedMessage(receipt, "duplicate"))
                    continue
                payload = decode_payload(envelope)
                outcome = "processed"
                if isinstance(payload, ObservationPayload):
                    if any(peer not in (*self.peers, self.agent_id) for peer in payload.path):
                        raise ValueError("unknown provenance identity")
                    added = self._knowledge.record_peer(payload.observation, receipt.delivered_at, forwarded_by=envelope.sender_id,
                                                        message_id=envelope.message_id, path=payload.path)
                    if not added:
                        outcome = "duplicate_evidence"
                elif isinstance(payload, AgentStatus):
                    previous = self._statuses.get(payload.agent_id)
                    if previous is None or (payload.updated_at, envelope.sent_at, envelope.message_id) > (previous.status.updated_at, previous.sent_at, previous.message_id):
                        self._statuses[payload.agent_id] = ReceivedStatus(payload, receipt.delivered_at, envelope.message_id, envelope.sent_at)
                self._received[envelope.message_id] = envelope
                self._recent.append(receipt)
                result.append(ProcessedMessage(receipt, outcome))
            except ValueError as exc:
                result.append(ProcessedMessage(receipt, "rejected", str(exc)))
        self._processed.extend(result)
        return tuple(result)

    def share_changed_local(self, now: int, refresh_ticks: int) -> None:
        """Endpoint-local policy state; remote evidence is never auto-forwarded."""
        for entry in self.knowledge_snapshot():
            observation = entry.observation
            if entry.is_remote or observation.source_id != self.agent_id:
                continue
            for peer in self.peers:
                key = (peer, observation.location_id, observation.subject, observation.sensing_capability)
                signature = (observation.value, observation.confidence)
                prior = self._refresh.get(key)
                if prior is None or prior[0] != signature or now - prior[1] >= refresh_ticks:
                    if self.share_observation(observation.observation_id, peer, now) is not None:
                        self._refresh[key] = (signature, now)


class SharingPolicy(Protocol):
    def __call__(self, view: "AgentView", endpoint: "AgentCommunicationPort") -> None: ...
    def reset(self) -> None: ...


class AgentCommunicationPort:
    """Tick-bound agent API. Queue delivery/processing/reset stay simulation-owned."""
    __slots__ = ("__endpoint", "__tick")

    def __init__(self, endpoint: AgentEndpoint, tick: int):
        _tick(tick)
        self.__endpoint, self.__tick = endpoint, tick

    @property
    def agent_id(self) -> str:
        return self.__endpoint.agent_id

    @property
    def peers(self) -> tuple[str, ...]:
        return self.__endpoint.peers

    @property
    def peer_statuses(self) -> tuple[ReceivedStatus, ...]:
        return self.__endpoint.peer_statuses

    def knowledge_snapshot(self):
        return self.__endpoint.knowledge_snapshot()

    def _check_tick(self, now: int) -> None:
        _tick(now)
        if now != self.__tick:
            raise ValueError("communication port is bound to the current decision tick")

    def send(self, envelope: MessageEnvelope, now: int) -> bool:
        self._check_tick(now)
        return self.__endpoint.send(envelope, now)

    def share_observation(self, observation_id: str, recipient_id: str, now: int, *, expires_at: int | None = None):
        self._check_tick(now)
        return self.__endpoint.share_observation(observation_id, recipient_id, now, expires_at=expires_at)

    def broadcast_observation(self, observation_id: str, now: int):
        self._check_tick(now)
        return self.__endpoint.broadcast_observation(observation_id, now)

    def send_status(self, status: AgentStatus, recipient_id: str, now: int):
        self._check_tick(now)
        return self.__endpoint.send_status(status, recipient_id, now)

    def send_coordination(self, topic: str, data: str, recipient_id: str, now: int):
        self._check_tick(now)
        return self.__endpoint.send_coordination(topic, data, recipient_id, now)

    def share_changed_local(self, now: int, refresh_ticks: int) -> None:
        self._check_tick(now)
        self.__endpoint.share_changed_local(now, refresh_ticks)


@dataclass(frozen=True)
class ShareLocalChanges:
    refresh_ticks: int = 3
    status_interval_ticks: int = 4

    def __post_init__(self) -> None:
        _tick(self.refresh_ticks)
        _tick(self.status_interval_ticks)
        if not self.refresh_ticks or not self.status_interval_ticks:
            raise ValueError("policy intervals must be positive")

    def __call__(self, view: "AgentView", endpoint: AgentCommunicationPort) -> None:
        if view.own_state.availability not in {Availability.AVAILABLE, Availability.BUSY}:
            return
        endpoint.share_changed_local(view.time.tick, self.refresh_ticks)
        if view.time.tick % self.status_interval_ticks == 0:
            state = view.own_state
            status = AgentStatus(state.identity.agent_id, state.position.location_id, state.battery, state.availability, view.time.tick)
            for peer in endpoint.peers:
                endpoint.send_status(status, peer, view.time.tick)

    def reset(self) -> None:
        pass  # Policy is stateless; send/refresh caches are endpoint-owned.


class SimulatedTransport:
    def __init__(self, endpoints: tuple[AgentEndpoint, ...], configuration: CommunicationConfiguration = CommunicationConfiguration()):
        if not isinstance(endpoints, tuple) or any(not isinstance(e, AgentEndpoint) for e in endpoints):
            raise ValueError("invalid endpoints")
        if not isinstance(configuration, CommunicationConfiguration):
            raise ValueError("invalid communication configuration")
        self._endpoints = {e.agent_id: e for e in endpoints}
        if len(self._endpoints) != len(endpoints):
            raise ValueError("duplicate endpoint")
        ids = set(self._endpoints)
        if any(set(e.peers) != ids - {e.agent_id} for e in endpoints):
            raise ValueError("endpoint contact roster mismatch")
        if configuration.links is not None and any(a not in ids or b not in ids for a, b in configuration.links):
            raise ValueError("unknown link endpoint")
        if any(d.agent_id not in ids for d in configuration.disconnections):
            raise ValueError("unknown disconnected endpoint")
        self.configuration = configuration
        self.reset()

    @property
    def agent_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._endpoints))

    def endpoint(self, agent_id: str) -> AgentEndpoint:
        if agent_id not in self._endpoints:
            raise ValueError("unknown endpoint")
        return self._endpoints[agent_id]

    @property
    def trace(self) -> tuple[CommunicationRecord, ...]:
        return tuple(self._trace)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def reset(self) -> None:
        self._pending: list[tuple[int, MessageEnvelope]] = []
        self._trace: list[CommunicationRecord] = []
        self._submitted: dict[str, MessageEnvelope] = {}
        self._connected: dict[str, bool] = {}
        self._loss = self.configuration.loss_probability
        for endpoint in self._endpoints.values():
            endpoint.reset()

    def set_connected(self, agent_id: str, connected: bool) -> None:
        if agent_id not in self._endpoints or type(connected) is not bool:
            raise ValueError("invalid connection update")
        self._connected[agent_id] = connected

    def set_loss_probability(self, probability: float) -> None:
        _number(probability, high=1)
        self._loss = probability

    def _connected_at(self, agent_id: str, now: int) -> bool:
        return self._connected.get(agent_id, True) and not any(d.agent_id == agent_id and d.start_tick <= now < d.end_tick for d in self.configuration.disconnections)

    def _route_failure(self, envelope: MessageEnvelope, now: int) -> str:
        if not self._connected_at(envelope.sender_id, now) or not self._connected_at(envelope.recipient_id, now):
            return "disconnected"
        if self.configuration.links is not None and (envelope.sender_id, envelope.recipient_id) not in self.configuration.links:
            return "unreachable"
        return ""

    def submit(self, now: int, rng: random.Random) -> None:
        _tick(now)
        outgoing = [(endpoint.agent_id, envelope) for endpoint in self._endpoints.values() for envelope in endpoint.drain_outgoing()]
        for owner, envelope in sorted(outgoing, key=lambda item: item[1].message_id):
            if envelope.sender_id != owner or envelope.recipient_id not in self._endpoints or envelope.sent_at != now:
                raise ValueError("invalid transport routing identity/tick")
            decode_payload(envelope)
            previous = self._submitted.get(envelope.message_id)
            if previous is not None:
                if previous != envelope:
                    raise ValueError("global message ID collision")
                continue
            self._submitted[envelope.message_id] = envelope
            self._trace.append(CommunicationRecord(now, "sent", envelope))
            reason = self._route_failure(envelope, now)
            if not reason and self._loss > 0 and rng.random() < self._loss:
                reason = "loss"
            if reason:
                self._trace.append(CommunicationRecord(now, "dropped", envelope, reason))
            else:
                self._pending.append((now + self.configuration.latency_ticks, envelope))

    def deliver(self, now: int) -> None:
        _tick(now)
        future = []
        for due, envelope in sorted(self._pending, key=lambda item: (item[0], item[1].message_id)):
            if due > now:
                future.append((due, envelope))
                continue
            reason = "expired" if envelope.expires_at is not None and now >= envelope.expires_at else self._route_failure(envelope, now)
            if reason:
                self._trace.append(CommunicationRecord(now, "dropped", envelope, reason))
            else:
                self._endpoints[envelope.recipient_id].receive(DeliveryReceipt(envelope, now))
                self._trace.append(CommunicationRecord(now, "delivered", envelope))
        self._pending = future

    def process(self, now: int) -> None:
        for agent_id in self.agent_ids:
            for processed in self.endpoint(agent_id).process(now):
                self._trace.append(CommunicationRecord(now, processed.result, processed.receipt.envelope, processed.reason))
