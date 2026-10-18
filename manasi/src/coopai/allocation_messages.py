"""Strict, immutable Contract Net wire values and commit intent contracts."""
from dataclasses import asdict, dataclass, fields, is_dataclass
from enum import Enum
from hashlib import sha256
import json

from .messages import CoordinationPayload, parse_object
from .models import MessageEnvelope, Task, _id, _number, _tick

TOPIC = "allocation.v1"


def _canonical(value):
    if is_dataclass(value):
        return _canonical(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical(v) for v in value]
    if isinstance(value, frozenset):
        return sorted(value)
    return value


def task_digest(task: Task) -> str:
    if not isinstance(task, Task):
        raise ValueError("expected Task")
    return sha256(json.dumps(_canonical(task), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def coordinator_for(task_id: str, epoch: int, roster: tuple[str, ...]) -> str:
    _id(task_id)
    _tick(epoch)
    if not isinstance(roster, tuple) or not roster or len(set(roster)) != len(roster):
        raise ValueError("invalid public contact roster")
    for identity in roster:
        _id(identity)
    contacts = sorted(roster)
    offset = int.from_bytes(sha256(task_id.encode()).digest(), "big") % len(contacts)
    return contacts[(offset + epoch) % len(contacts)]


@dataclass(frozen=True)
class AllocationConfiguration:
    bid_window_ticks: int = 3
    award_window_ticks: int = 3
    commit_window_ticks: int = 3
    confirmation_interval_ticks: int = 3

    def __post_init__(self):
        for value in asdict(self).values():
            _tick(value)
            if value == 0:
                raise ValueError("allocation windows must be positive")

    @property
    def epoch_ticks(self) -> int:
        return self.bid_window_ticks + self.award_window_ticks + self.commit_window_ticks + 1

    @classmethod
    def for_latency(cls, latency_ticks: int) -> "AllocationConfiguration":
        """Public configured latency budget, not a guess about actual delivery."""
        _tick(latency_ticks)
        if not latency_ticks:
            raise ValueError("latency budget must be positive")
        return cls(2 * latency_ticks + 1, 2 * latency_ticks + 1, latency_ticks + 2)


@dataclass(frozen=True)
class AllocationRound:
    task_id: str
    task_version: int
    task_digest: str
    coordinator_id: str
    epoch: int
    opened_at: int
    bid_deadline: int
    accept_deadline: int
    commit_deadline: int

    def __post_init__(self):
        for value in (self.task_id, self.coordinator_id):
            _id(value)
        if not isinstance(self.task_digest, str) or len(self.task_digest) != 64 or any(c not in "0123456789abcdef" for c in self.task_digest):
            raise ValueError("invalid task digest")
        for name in ("task_version", "epoch", "opened_at", "bid_deadline", "accept_deadline", "commit_deadline"):
            _tick(getattr(self, name))
        if self.task_version == 0 or not self.opened_at < self.bid_deadline < self.accept_deadline < self.commit_deadline:
            raise ValueError("invalid round version/deadlines")

    @property
    def round_id(self) -> str:
        return "round:" + json.dumps([self.task_id, self.task_version, self.epoch, self.coordinator_id], separators=(",", ":"))


class AllocationKind(Enum):
    CALL_FOR_PROPOSALS = "CALL_FOR_PROPOSALS"
    BID = "BID"
    NO_BID = "NO_BID"
    PROVISIONAL_AWARD = "PROVISIONAL_AWARD"
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    COMMIT = "COMMIT"
    CANCEL = "CANCEL"
    ROUND_CLOSED = "ROUND_CLOSED"


@dataclass(frozen=True)
class AllocationMessage:
    kind: AllocationKind
    round: AllocationRound
    sender_id: str
    issued_at: int
    candidate_id: str | None = None
    score: float | None = None
    bid_tick: int | None = None
    references: tuple[str, ...] = ()
    reason: str = ""

    def __post_init__(self):
        if not isinstance(self.kind, AllocationKind) or not isinstance(self.round, AllocationRound):
            raise ValueError("invalid allocation message type/round")
        _id(self.sender_id)
        _tick(self.issued_at)
        if self.issued_at < self.round.opened_at:
            raise ValueError("message precedes round")
        if self.candidate_id is not None:
            _id(self.candidate_id)
        if self.score is not None:
            _number(self.score, high=1)
        if self.bid_tick is not None:
            _tick(self.bid_tick)
            if not self.round.opened_at <= self.bid_tick < self.round.bid_deadline or self.bid_tick > self.issued_at:
                raise ValueError("invalid bid timestamp")
        if not isinstance(self.references, tuple):
            raise ValueError("references must be a tuple")
        for reference in self.references:
            _id(reference)
        if not isinstance(self.reason, str):
            raise ValueError("reason must be text")
        scored = {AllocationKind.BID: 0, AllocationKind.PROVISIONAL_AWARD: 1, AllocationKind.ACCEPT: 2, AllocationKind.COMMIT: 3}
        if self.kind in scored:
            if self.candidate_id is None or self.score is None or self.bid_tick is None or len(self.references) != scored[self.kind]:
                raise ValueError("invalid bid/award/accept/commit fields")
        elif self.score is not None or self.bid_tick is not None:
            raise ValueError("non-bid message cannot carry a score")
        if self.kind in {AllocationKind.CALL_FOR_PROPOSALS, AllocationKind.NO_BID, AllocationKind.CANCEL} and self.candidate_id is not None:
            raise ValueError("non-candidate message cannot name a candidate")
        if self.kind is AllocationKind.REJECT and self.candidate_id != self.sender_id:
            raise ValueError("rejection sender/candidate mismatch")
        if self.kind is AllocationKind.ROUND_CLOSED:
            if self.candidate_id is None or len(self.references) != 1 or self.sender_id != self.candidate_id:
                raise ValueError("closure requires confirmed owner's commit reference")
        elif self.kind not in scored and self.references:
            raise ValueError("unexpected references")
        if self.kind in {AllocationKind.CALL_FOR_PROPOSALS, AllocationKind.PROVISIONAL_AWARD, AllocationKind.COMMIT, AllocationKind.CANCEL} and self.sender_id != self.round.coordinator_id:
            raise ValueError("coordinator sender mismatch")
        if self.kind in {AllocationKind.BID, AllocationKind.ACCEPT} and self.sender_id != self.candidate_id:
            raise ValueError("candidate sender mismatch")
        if self.kind is AllocationKind.BID and self.issued_at != self.bid_tick:
            raise ValueError("bid evaluation timestamp must equal issue time")
        deadline = self.round.bid_deadline if self.kind in {AllocationKind.BID, AllocationKind.NO_BID, AllocationKind.CALL_FOR_PROPOSALS} else self.round.accept_deadline if self.kind in {AllocationKind.PROVISIONAL_AWARD, AllocationKind.ACCEPT, AllocationKind.REJECT} else self.round.commit_deadline
        if self.kind not in {AllocationKind.ROUND_CLOSED, AllocationKind.CANCEL} and self.issued_at >= deadline:
            raise ValueError("message issued after phase deadline")


def encode_allocation(message: AllocationMessage) -> str:
    if not isinstance(message, AllocationMessage):
        raise ValueError("expected AllocationMessage")
    return json.dumps(_canonical(message), sort_keys=True, separators=(",", ":"), allow_nan=False)


def decode_allocation(payload: CoordinationPayload, envelope: MessageEnvelope | None = None) -> AllocationMessage:
    if not isinstance(payload, CoordinationPayload) or payload.topic != TOPIC:
        raise ValueError("not an allocation payload")
    data = parse_object(payload.data)
    if set(data) != {f.name for f in fields(AllocationMessage)} or not isinstance(data["round"], dict) or set(data["round"]) != {f.name for f in fields(AllocationRound)}:
        raise ValueError("allocation fields mismatch")
    if not isinstance(data["references"], list):
        raise ValueError("wire references must be an array")
    try:
        message = AllocationMessage(**{**data, "kind": AllocationKind(data["kind"]), "round": AllocationRound(**data["round"]), "references": tuple(data["references"])})
    except (TypeError, KeyError) as exc:
        raise ValueError("invalid allocation schema") from exc
    if envelope and (message.sender_id != envelope.sender_id or message.issued_at != envelope.sent_at):
        raise ValueError("allocation sender/timestamp mismatch")
    return message


@dataclass(frozen=True)
class AllocationEvidence:
    record_id: str
    message: AllocationMessage
    recipient_id: str
    envelope: MessageEnvelope | None = None

    def __post_init__(self):
        _id(self.record_id)
        _id(self.recipient_id)
        if not isinstance(self.message, AllocationMessage):
            raise ValueError("invalid protocol evidence")
        if self.envelope is None:
            if self.recipient_id != self.message.sender_id:
                raise ValueError("only own actions can be local evidence")
        elif (self.envelope.message_id, self.envelope.sender_id, self.envelope.recipient_id) != (self.record_id, self.message.sender_id, self.recipient_id):
            raise ValueError("protocol evidence envelope mismatch")


@dataclass(frozen=True)
class CommitRequest:
    agent_id: str
    task_id: str
    round_id: str
    issued_at: int
    evidence_ids: tuple[str, str, str, str]

    def __post_init__(self):
        for value in (self.agent_id, self.task_id, self.round_id):
            _id(value)
        _tick(self.issued_at)
        if not isinstance(self.evidence_ids, tuple) or len(self.evidence_ids) != 4 or len(set(self.evidence_ids)) != 4:
            raise ValueError("commit requires distinct bid/award/accept/commit evidence")
        for identity in self.evidence_ids:
            _id(identity)

    @property
    def request_id(self) -> str:
        return "commit-request:" + json.dumps([self.agent_id, self.round_id], separators=(",", ":"))


@dataclass(frozen=True)
class CommitFeedback:
    request: CommitRequest
    applied_at: int
    accepted: bool
    reason: str
    task: Task | None = None

    def __post_init__(self):
        if not isinstance(self.request, CommitRequest) or type(self.accepted) is not bool:
            raise ValueError("invalid commit feedback")
        _tick(self.applied_at)
        if self.applied_at < self.request.issued_at or not isinstance(self.reason, str):
            raise ValueError("invalid feedback time/reason")
        if self.accepted != (self.task is not None):
            raise ValueError("accepted feedback requires own assigned task")
        if self.task is not None and (self.task.task_id != self.request.task_id or self.task.owner_id != self.request.agent_id):
            raise ValueError("feedback task identity mismatch")
