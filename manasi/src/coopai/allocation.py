"""Owner-local Contract Net baseline. No world, simulator or peer-state access."""
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING

from .allocation_messages import (TOPIC, AllocationConfiguration, AllocationEvidence,
    AllocationKind as Kind, AllocationMessage, AllocationRound, CommitRequest,
    coordinator_for, decode_allocation, encode_allocation, task_digest)
from .messages import CoordinationPayload, decode_payload
from .models import Availability, TaskStatus, _id
from .priority import TaskAnnouncement

if TYPE_CHECKING:
    from .communication import AgentCommunicationPort
    from .interfaces import AgentView


class RoundPhase(Enum):
    BIDDING = "bidding"
    AWARDED = "provisional award"
    ACCEPTED = "accepted reservation"
    PROTOCOL_CONFIRMED = "protocol confirmed; awaiting simulator"
    COMMITTED = "confirmed assignment"
    CLOSED = "closed"
    EXPIRED = "expired"


@dataclass(frozen=True)
class AllocationEvent:
    tick: int
    agent_id: str
    task_id: str
    round_id: str
    action: str
    detail: str


@dataclass(frozen=True)
class RoundSnapshot:
    round: AllocationRound
    phase: RoundPhase
    bids: tuple[AllocationEvidence, ...]
    provisional_owner: str | None
    confirmed_owner: str | None
    reason: str


@dataclass(frozen=True)
class AllocationOutput:
    evidence: tuple[AllocationEvidence, ...] = ()
    commits: tuple[CommitRequest, ...] = ()
    announcements: tuple[TaskAnnouncement, ...] = ()


@dataclass
class _RoundState:
    round: AllocationRound
    phase: RoundPhase = RoundPhase.BIDDING
    bids: dict[str, AllocationEvidence] = field(default_factory=dict)
    award: AllocationEvidence | None = None
    accept: AllocationEvidence | None = None
    commit: AllocationEvidence | None = None
    confirmed_owner: str | None = None
    reason: str = ""


def bid_order(evidence: AllocationEvidence):
    """Only received valid bids: descending score, earlier issue tick, stable ID."""
    message = evidence.message
    if message.kind is not Kind.BID:
        raise ValueError("bid_order requires BID evidence")
    return -message.score, message.bid_tick, message.sender_id


class AgentAllocator:
    def __init__(self, agent_id: str, public_roster: tuple[str, ...],
                 configuration: AllocationConfiguration = AllocationConfiguration()):
        _id(agent_id)
        coordinator_for("validate-roster", 0, public_roster)
        if agent_id not in public_roster or not isinstance(configuration, AllocationConfiguration):
            raise ValueError("invalid allocator identity/configuration")
        self.agent_id = agent_id
        self.roster = tuple(sorted(public_roster))
        self.configuration = configuration
        self.reset()

    def reset(self) -> None:
        self._rounds: dict[str, _RoundState] = {}
        self._records: dict[str, AllocationEvidence] = {}
        self._seen: set[str] = set()
        self._responded: set[str] = set()
        self._requested: set[str] = set()
        self._owners: dict[str, str] = {}
        self._trace: list[AllocationEvent] = []
        self._reservation: str | None = None
        self._counter = 0
        self._outbound: list[AllocationEvidence] = []
        self._commits: list[CommitRequest] = []
        self._announcements: list[TaskAnnouncement] = []
        self._last_confirmed: dict[str, int] = {}

    @property
    def trace(self) -> tuple[AllocationEvent, ...]:
        return tuple(self._trace)

    @property
    def reservation(self) -> str | None:
        return self._reservation

    @property
    def known_owners(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._owners.items()))

    @property
    def rounds(self) -> tuple[RoundSnapshot, ...]:
        return tuple(RoundSnapshot(s.round, s.phase, tuple(sorted(s.bids.values(), key=bid_order)),
                                   s.award.message.candidate_id if s.award else None, s.confirmed_owner, s.reason)
                     for _, s in sorted(self._rounds.items()))

    def _log(self, now, spec, action, detail=""):
        self._trace.append(AllocationEvent(now, self.agent_id, spec.task_id, spec.round_id, action, detail))

    def _announcement(self, view, task_id):
        return next((a for a in view.known_tasks if a.task.task_id == task_id), None)

    def _priority(self, view, task_id):
        result = next((r for r in view.priorities if r.task_id == task_id), None)
        return result.score if result else 0

    def _self_score(self, view, task_id):
        result = next((r for r in view.suitability if r.task_id == task_id), None)
        if result is None or result.agent_id != self.agent_id or result.evaluation_tick != view.time.tick or not result.eligibility.eligible:
            return None, result.eligibility.explanation if result else "No current self-evaluation"
        if view.own_state.availability is not Availability.AVAILABLE or self._reservation is not None or self._owners.get(task_id):
            return None, "Capacity reserved/committed or task locally owned"
        return result.score, result.explanation

    def _emit(self, message, recipient, port):
        if recipient == self.agent_id:
            identity = "allocation-local:" + str([self.agent_id, self._counter])
            self._counter += 1
            evidence = AllocationEvidence(identity, message, recipient)
        else:
            envelope = port.send_coordination(TOPIC, encode_allocation(message), recipient, message.issued_at)
            evidence = AllocationEvidence(envelope.message_id, message, recipient, envelope)
        self._records[evidence.record_id] = evidence
        self._outbound.append(evidence)
        self._log(message.issued_at, message.round, "send " + message.kind.value, f"to {recipient}; candidate {message.candidate_id or '-'}; {message.reason}")
        return evidence

    def _broadcast(self, message, port):
        for peer in port.peers:
            self._emit(message, peer, port)

    def _spec_matches(self, spec):
        cfg = self.configuration
        start = spec.epoch * cfg.epoch_ticks
        return (spec.coordinator_id == coordinator_for(spec.task_id, spec.epoch, self.roster)
                and (spec.opened_at, spec.bid_deadline, spec.accept_deadline, spec.commit_deadline) ==
                (start, start + cfg.bid_window_ticks, start + cfg.bid_window_ticks + cfg.award_window_ticks,
                 start + cfg.bid_window_ticks + cfg.award_window_ticks + cfg.commit_window_ticks))

    def _known_round(self, view, spec):
        announced = self._announcement(view, spec.task_id)
        return announced is not None and announced.version == spec.task_version and task_digest(announced.task) == spec.task_digest

    def _closure_matches(self, view, message):
        announced = self._announcement(view, message.round.task_id)
        if announced is None:
            return False
        if self._known_round(view, message.round):
            return announced.task.status is TaskStatus.PENDING
        # Permit heartbeat copies only for the exact previously learned assignment,
        # never a newer requirement revision or different owner.
        return (announced.version == message.round.task_version + 1
                and announced.task.status is TaskStatus.ASSIGNED
                and announced.task.owner_id == message.candidate_id
                and task_digest(replace(announced.task, status=TaskStatus.PENDING, owner_id=None)) == message.round.task_digest)

    def _bid(self, state, view, port):
        spec, now = state.round, view.time.tick
        if spec.round_id in self._responded or now >= spec.bid_deadline:
            return
        self._responded.add(spec.round_id)
        score, explanation = self._self_score(view, spec.task_id)
        if score is None:
            message = AllocationMessage(Kind.NO_BID, spec, self.agent_id, now, reason=explanation)
        else:
            message = AllocationMessage(Kind.BID, spec, self.agent_id, now, self.agent_id, score, now)
        record = self._emit(message, spec.coordinator_id, port)
        if spec.coordinator_id == self.agent_id and score is not None:
            state.bids[self.agent_id] = record

    def _cancel(self, state, view, port, reason):
        state.phase, state.reason = RoundPhase.CLOSED, reason
        if self._reservation == state.round.round_id:
            self._reservation = None
        self._log(view.time.tick, state.round, "round closed", reason)
        self._broadcast(AllocationMessage(Kind.CANCEL, state.round, self.agent_id, view.time.tick, reason=reason), port)

    def _accept_award(self, record, state, view, port):
        msg, now = record.message, view.time.tick
        if state.phase in {RoundPhase.CLOSED, RoundPhase.EXPIRED, RoundPhase.COMMITTED, RoundPhase.PROTOCOL_CONFIRMED} or now >= state.round.accept_deadline or not self._known_round(view, state.round):
            self._log(now, state.round, "stale award", "Expired or changed task")
            return
        own_bid = self._records.get(msg.references[0])
        if own_bid is None or own_bid.message.kind is not Kind.BID or own_bid.message.round != state.round or own_bid.message.sender_id != self.agent_id or (own_bid.message.score, own_bid.message.bid_tick) != (msg.score, msg.bid_tick):
            self._log(now, state.round, "rejected award", "No matching own bid")
            return
        if state.accept is not None:
            return
        score, reason = self._self_score(view, state.round.task_id)
        if score is None:
            self._emit(AllocationMessage(Kind.REJECT, state.round, self.agent_id, now, self.agent_id, reason=reason), state.round.coordinator_id, port)
            self._log(now, state.round, "rejected award", reason)
            if state.round.coordinator_id == self.agent_id:
                self._cancel(state, view, port, reason)
            return
        self._reservation = state.round.round_id
        state.award = record
        state.phase = RoundPhase.ACCEPTED
        refs = (*msg.references, record.record_id)
        accept = self._emit(AllocationMessage(Kind.ACCEPT, state.round, self.agent_id, now, self.agent_id, msg.score, msg.bid_tick, refs), state.round.coordinator_id, port)
        state.accept = accept
        if state.round.coordinator_id == self.agent_id:
            self._confirm(accept, state, view, port)

    def _confirm(self, record, state, view, port):
        msg, now = record.message, view.time.tick
        award = state.award
        if now >= state.round.accept_deadline or award is None or state.commit is not None:
            return
        expected = (*award.message.references, award.record_id)
        if msg.references != expected or (msg.candidate_id, msg.score, msg.bid_tick) != (award.message.candidate_id, award.message.score, award.message.bid_tick):
            self._log(now, state.round, "rejected acceptance", "Award identity/score/reference mismatch")
            return
        state.accept = record
        state.phase = RoundPhase.PROTOCOL_CONFIRMED
        commit = self._emit(AllocationMessage(Kind.COMMIT, state.round, self.agent_id, now, msg.candidate_id, msg.score, msg.bid_tick,
                                             (*msg.references, record.record_id)), msg.candidate_id, port)
        state.commit = commit
        if msg.candidate_id == self.agent_id:
            self._request(commit, state, view)

    def _request(self, record, state, view):
        msg, now = record.message, view.time.tick
        if state.round.round_id in self._requested or now >= state.round.commit_deadline or self._reservation != state.round.round_id or state.accept is None:
            return
        if msg.references != (*state.accept.message.references, state.accept.record_id) or not self._known_round(view, state.round):
            self._log(now, state.round, "rejected commit", "Acceptance/task mismatch")
            return
        # Eligibility is rechecked by the existing current-tick own result. Reservation
        # itself is a protocol constraint, so this check intentionally excludes it.
        result = next((r for r in view.suitability if r.task_id == state.round.task_id), None)
        if result is None or not result.eligibility.eligible or result.evaluation_tick != now or view.own_state.availability is not Availability.AVAILABLE:
            state.phase, state.reason = RoundPhase.CLOSED, "Own eligibility changed before commit"
            self._reservation = None
            self._log(now, state.round, "rejected commit", state.reason)
            return
        state.commit = record
        state.phase = RoundPhase.PROTOCOL_CONFIRMED
        self._requested.add(state.round.round_id)
        self._commits.append(CommitRequest(self.agent_id, state.round.task_id, state.round.round_id, now,
                                         (*msg.references, record.record_id)))
        self._log(now, state.round, "commit requested", "Protocol confirmation; not authoritative ownership")

    def _learn_owner(self, state, owner, view):
        task_id = state.round.task_id
        prior = self._owners.get(task_id)
        if prior is not None and prior != owner:
            self._log(view.time.tick, state.round, "conflicting closure", "Existing confirmed local owner retained")
            return
        self._owners[task_id] = owner
        state.phase, state.confirmed_owner = RoundPhase.COMMITTED, owner
        announced = self._announcement(view, task_id)
        if announced and announced.task.status is TaskStatus.PENDING and self._known_round(view, state.round):
            assigned = announced.task.transition(TaskStatus.ASSIGNED, owner)
            self._announcements.append(replace(announced, task=assigned, announced_at=view.time.tick, version=announced.version + 1))

    def _feedback(self, view, port):
        for feedback in view.allocation_feedback:
            if feedback.request.agent_id != self.agent_id:
                raise ValueError("foreign allocation feedback")
            state = self._rounds.get(feedback.request.round_id)
            if state is None or state.commit is None:
                continue
            if self._reservation == state.round.round_id:
                self._reservation = None
            if feedback.accepted:
                self._learn_owner(state, self.agent_id, view)
                self._log(view.time.tick, state.round, "assignment confirmed", feedback.reason)
            else:
                state.phase, state.reason = RoundPhase.CLOSED, feedback.reason
                self._log(view.time.tick, state.round, "commit rejected", feedback.reason)
        # Repeat confirmed closure announcements so one lost notification need not
        # leave peers permanently unaware. These carry no other-world knowledge.
        for state in self._rounds.values():
            known = self._announcement(view, state.round.task_id)
            if state.confirmed_owner != self.agent_id or state.commit is None or (known and known.task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}):
                continue
            now = view.time.tick
            if now - self._last_confirmed.get(state.round.round_id, -self.configuration.confirmation_interval_ticks) >= self.configuration.confirmation_interval_ticks:
                self._broadcast(AllocationMessage(Kind.ROUND_CLOSED, state.round, self.agent_id, now, self.agent_id,
                                                 references=(state.commit.record_id,), reason="Simulator accepted my commit"), port)
                self._last_confirmed[state.round.round_id] = now

    def step(self, view: "AgentView", port: "AgentCommunicationPort") -> AllocationOutput:
        if view.own_state.identity.agent_id != self.agent_id or port.agent_id != self.agent_id or tuple(sorted((*port.peers, self.agent_id))) != self.roster:
            raise ValueError("allocator owner/public roster mismatch")
        self._outbound, self._commits, self._announcements = [], [], []
        now = view.time.tick
        active = view.own_state.availability in {Availability.AVAILABLE, Availability.BUSY} and view.own_state.battery > 0
        # Failed/offline agents cannot coordinate, bid, accept or confirm. Local
        # reservations still expire; a future execution failure is not reassigned.
        for state in self._rounds.values():
            if now >= state.round.commit_deadline and state.phase not in {RoundPhase.COMMITTED, RoundPhase.CLOSED, RoundPhase.EXPIRED}:
                state.phase, state.reason = RoundPhase.EXPIRED, "Round deadline elapsed"
                if self._reservation == state.round.round_id:
                    self._reservation = None
                self._log(now, state.round, "round expired", state.reason)
        if not active:
            return AllocationOutput()
        self._feedback(view, port)
        awards = []
        receipts = sorted(view.messages, key=lambda r: (r.delivered_at, r.envelope.message_id))
        for receipt in receipts:
            envelope = receipt.envelope
            if envelope.message_id in self._seen:
                continue
            payload = decode_payload(envelope)
            if not isinstance(payload, CoordinationPayload) or payload.topic != TOPIC:
                continue
            msg = decode_allocation(payload, envelope)
            spec = msg.round
            self._seen.add(envelope.message_id)
            if envelope.recipient_id != self.agent_id or msg.sender_id not in self.roster or (msg.candidate_id is not None and msg.candidate_id not in self.roster) or not self._spec_matches(spec):
                self._log(now, spec, "rejected message", "Recipient/roster/round configuration mismatch")
                continue
            if msg.kind is not Kind.ROUND_CLOSED and (now >= spec.commit_deadline or not self._known_round(view, spec)):
                self._log(now, spec, "stale message", "Expired/unknown/changed task")
                continue
            if msg.kind is Kind.ROUND_CLOSED and not self._closure_matches(view, msg):
                self._log(now, spec, "stale closure", "Unknown task, changed revision, or different known owner")
                continue
            state = self._rounds.get(spec.round_id)
            if state is None:
                if msg.kind not in {Kind.CALL_FOR_PROPOSALS, Kind.ROUND_CLOSED} or not self._known_round(view, spec):
                    self._log(now, spec, "rejected message", "No known call/round")
                    continue
                state = self._rounds[spec.round_id] = _RoundState(spec)
            if state.round != spec:
                self._log(now, spec, "rejected message", "Round identity reused with changed descriptor")
                continue
            record = AllocationEvidence(envelope.message_id, msg, self.agent_id, envelope)
            self._records[record.record_id] = record
            self._log(now, spec, "receive " + msg.kind.value, f"from {msg.sender_id}")
            if msg.kind is Kind.CALL_FOR_PROPOSALS:
                self._bid(state, view, port)
            elif msg.kind is Kind.BID and self.agent_id == spec.coordinator_id and now < spec.bid_deadline and state.phase is RoundPhase.BIDDING:
                state.bids.setdefault(msg.sender_id, record)
            elif msg.kind is Kind.BID:
                self._log(now, spec, "stale bid", "Not coordinator, closed round, or bid deadline elapsed")
            elif msg.kind is Kind.PROVISIONAL_AWARD and msg.candidate_id == self.agent_id:
                awards.append((record, state))
            elif msg.kind is Kind.ACCEPT and self.agent_id == spec.coordinator_id and state.phase is RoundPhase.AWARDED:
                self._confirm(record, state, view, port)
            elif msg.kind is Kind.COMMIT and msg.candidate_id == self.agent_id:
                self._request(record, state, view)
            elif msg.kind is Kind.REJECT and self.agent_id == spec.coordinator_id and state.award and state.award.message.candidate_id == msg.sender_id:
                self._cancel(state, view, port, "Winner rejected: " + msg.reason)
            elif msg.kind is Kind.CANCEL and state.phase is not RoundPhase.COMMITTED:
                state.phase, state.reason = RoundPhase.CLOSED, msg.reason
                if self._reservation == spec.round_id:
                    self._reservation = None
            elif msg.kind is Kind.ROUND_CLOSED:
                self._learn_owner(state, msg.candidate_id, view)
                self._log(now, spec, "owner learned", msg.candidate_id)
        # Initiate all locally known pending tasks in own priority order at the
        # public epoch boundary. Coordinator can be busy but never failed/offline.
        cfg = self.configuration
        if now % cfg.epoch_ticks == 0:
            epoch = now // cfg.epoch_ticks
            tasks = sorted(view.known_tasks, key=lambda a: (-self._priority(view, a.task.task_id), a.task.task_id))
            for announced in tasks:
                task = announced.task
                if task.status is not TaskStatus.PENDING or task.task_id in self._owners or coordinator_for(task.task_id, epoch, self.roster) != self.agent_id:
                    continue
                spec = AllocationRound(task.task_id, announced.version, task_digest(task), self.agent_id, epoch, now,
                                       now + cfg.bid_window_ticks, now + cfg.bid_window_ticks + cfg.award_window_ticks,
                                       now + cfg.bid_window_ticks + cfg.award_window_ticks + cfg.commit_window_ticks)
                if spec.round_id in self._rounds:
                    continue
                state = self._rounds[spec.round_id] = _RoundState(spec)
                self._log(now, spec, "round opened", f"Own task priority {self._priority(view, task.task_id):.3f}")
                self._broadcast(AllocationMessage(Kind.CALL_FOR_PROPOSALS, spec, self.agent_id, now), port)
                self._bid(state, view, port)
        # Only temporary coordinators compare their own received bids. No peer
        # private scores, global candidates or simulator registry is available here.
        coordinated = sorted((s for s in self._rounds.values() if s.round.coordinator_id == self.agent_id),
                             key=lambda s: (-self._priority(view, s.round.task_id), s.round.task_id, s.round.epoch))
        for state in coordinated:
            spec = state.round
            if state.phase is RoundPhase.BIDDING and now >= spec.bid_deadline:
                if not state.bids:
                    self._cancel(state, view, port, "No eligible bids received")
                    continue
                bid = min(state.bids.values(), key=bid_order)
                msg = bid.message
                state.phase = RoundPhase.AWARDED
                award = self._emit(AllocationMessage(Kind.PROVISIONAL_AWARD, spec, self.agent_id, now,
                                                    msg.candidate_id, msg.score, msg.bid_tick, (bid.record_id,)), msg.candidate_id, port)
                state.award = award
                if msg.candidate_id == self.agent_id:
                    awards.append((award, state))
            elif state.phase is RoundPhase.AWARDED and now >= spec.accept_deadline:
                self._cancel(state, view, port, "Award/acceptance timeout")
        for record, state in sorted(awards, key=lambda pair: (-self._priority(view, pair[1].round.task_id), pair[1].round.task_id)):
            self._accept_award(record, state, view, port)
        return AllocationOutput(tuple(self._outbound), tuple(self._commits), tuple(self._announcements))
