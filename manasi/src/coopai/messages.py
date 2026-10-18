"""Typed peer wire payloads; independent of simulation truth and transport."""
from dataclasses import asdict, dataclass
import json

from .models import Availability, MessageEnvelope, MessageKind, Observation, _id, _number, _tick, _json_float


@dataclass(frozen=True)
class ObservationPayload:
    observation: Observation
    path: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.observation, Observation) or not isinstance(self.path, tuple) or not self.path:
            raise ValueError("invalid observation or provenance path")
        for agent in self.path:
            _id(agent)
        if self.path[0] != self.observation.source_id or len(set(self.path)) != len(self.path):
            raise ValueError("provenance must start at source and contain no loops")


@dataclass(frozen=True)
class AgentStatus:
    agent_id: str
    location_id: str
    battery: float
    availability: Availability
    updated_at: int

    def __post_init__(self) -> None:
        _id(self.agent_id)
        _id(self.location_id)
        _number(self.battery, high=1)
        _tick(self.updated_at)
        if not isinstance(self.availability, Availability):
            raise ValueError("invalid status availability")


@dataclass(frozen=True)
class CoordinationPayload:
    topic: str
    data: str  # Immutable JSON-object text; registered allocation topic is validated.

    def __post_init__(self) -> None:
        _id(self.topic)
        object.__setattr__(self, "data", json.dumps(parse_object(self.data), sort_keys=True,
                                                 separators=(",", ":"), allow_nan=False))


@dataclass(frozen=True)
class DeliveryReceipt:
    envelope: MessageEnvelope
    delivered_at: int

    def __post_init__(self) -> None:
        if not isinstance(self.envelope, MessageEnvelope):
            raise ValueError("invalid envelope")
        _tick(self.delivered_at)
        if self.delivered_at <= self.envelope.sent_at:
            raise ValueError("delivery must occur after creation")


@dataclass(frozen=True)
class ReceivedStatus:
    status: AgentStatus
    received_at: int
    message_id: str
    sent_at: int = 0


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError("nonfinite JSON value")


def parse_object(text: str) -> dict:
    if not isinstance(text, str):
        raise ValueError("payload must be JSON text")
    result = json.loads(text, object_pairs_hook=_pairs, parse_float=_json_float, parse_constant=_nonfinite)
    if not isinstance(result, dict):
        raise ValueError("payload must be an object")
    return result


def _fields(obj, names):
    if not isinstance(obj, dict) or set(obj) != set(names):
        raise ValueError(f"payload requires exactly these fields: {sorted(names)}")


def encode_payload(payload: ObservationPayload | AgentStatus | CoordinationPayload) -> tuple[MessageKind, str]:
    if isinstance(payload, ObservationPayload):
        kind, data = MessageKind.OBSERVATION, {"observation": asdict(payload.observation), "path": payload.path}
    elif isinstance(payload, AgentStatus):
        data = asdict(payload)
        data["availability"] = payload.availability.value
        kind = MessageKind.STATE
    elif isinstance(payload, CoordinationPayload):
        kind, data = MessageKind.COORDINATION, {"topic": payload.topic, "data": parse_object(payload.data)}
    else:
        raise ValueError("unsupported message payload")
    return kind, json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)


def decode_payload(envelope: MessageEnvelope) -> ObservationPayload | AgentStatus | CoordinationPayload:
    if not isinstance(envelope, MessageEnvelope):
        raise ValueError("expected MessageEnvelope")
    data = parse_object(envelope.payload)
    try:
        if envelope.kind is MessageKind.OBSERVATION:
            _fields(data, {"observation", "path"})
            _fields(data["observation"], Observation.__dataclass_fields__)
            if not isinstance(data["path"], list):
                raise ValueError("path must be an array")
            payload = ObservationPayload(Observation(**data["observation"]), tuple(data["path"]))
            if payload.path[-1] != envelope.sender_id or envelope.recipient_id in payload.path:
                raise ValueError("forwarding sender mismatch or provenance loop")
            if payload.observation.observed_at > envelope.sent_at:
                raise ValueError("observation timestamp is after send time")
            return payload
        if envelope.kind is MessageKind.STATE:
            _fields(data, AgentStatus.__dataclass_fields__)
            payload = AgentStatus(**{**data, "availability": Availability(data["availability"])})
            if payload.agent_id != envelope.sender_id or payload.updated_at > envelope.sent_at:
                raise ValueError("status sender/timestamp mismatch")
            return payload
        if envelope.kind is MessageKind.COORDINATION:
            _fields(data, {"topic", "data"})
            if not isinstance(data["data"], dict):
                raise ValueError("coordination data must be an object")
            payload = CoordinationPayload(data["topic"], json.dumps(data["data"], sort_keys=True, allow_nan=False))
            if payload.topic == "allocation.v1":
                from .allocation_messages import decode_allocation
                decode_allocation(payload, envelope)
            elif payload.topic == "execution.v1":
                from .execution_models import decode_notice
                decode_notice(payload, envelope)
            if payload.topic == "recovery.v1":
                from .recovery_models import decode_recovery
                decode_recovery(payload, envelope)
            return payload
        raise ValueError("task messages are reserved, no schema is implemented")
    except TypeError as exc:
        raise ValueError("invalid typed payload") from exc
