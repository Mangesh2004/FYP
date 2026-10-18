"""Small objective/attempt and peer recovery contracts. No simulator imports."""
from dataclasses import asdict, dataclass, fields
from enum import Enum
import json

from .execution_models import TaskExecutionSpec
from .messages import CoordinationPayload, parse_object, _fields
from .models import (Capabilities, ResourceLevel, Task, TaskRequirements, TaskStatus,
                     MessageEnvelope, _id, _tick)
from .priority import TaskAnnouncement

TOPIC = "recovery.v1"


@dataclass(frozen=True)
class RecoveryConfiguration:
    heartbeat_ticks: int = 2
    lease_ticks: int = 8
    retry_ticks: int = 3
    announcement_ticks: int = 3
    max_attempts: int = 4

    def __post_init__(self):
        for item in fields(self):
            _tick(getattr(self, item.name))
            if not getattr(self, item.name):
                raise ValueError("recovery intervals and attempt limit must be positive")
        if self.lease_ticks <= self.heartbeat_ticks:
            raise ValueError("lease must exceed heartbeat interval")


class RecoveryTrigger(Enum):
    FAILURE = "failure"
    SILENCE = "silence"
    RELEASE = "release"


@dataclass(frozen=True)
class ObjectiveAnnouncement:
    objective_id: str
    generation: int
    announcement: TaskAnnouncement
    spec: TaskExecutionSpec
    recoverable: bool = True
    predecessor_id: str | None = None

    def __post_init__(self):
        _id(self.objective_id)
        _tick(self.generation)
        if not self.generation or type(self.recoverable) is not bool:
            raise ValueError("invalid recovery generation/policy")
        if not isinstance(self.announcement, TaskAnnouncement) or not isinstance(self.spec, TaskExecutionSpec):
            raise ValueError("invalid objective announcement/procedure")
        if self.spec.task_id != self.announcement.task.task_id or self.spec.task_type != self.announcement.task_type:
            raise ValueError("announcement/procedure mismatch")
        if self.announcement.task.status is not TaskStatus.PENDING:
            raise ValueError("availability announcement must describe a pending attempt")
        if self.predecessor_id is not None:
            _id(self.predecessor_id)
        if (self.generation == 1) != (self.predecessor_id is None):
            raise ValueError("retry requires predecessor; initial attempt forbids it")
        expected = self.objective_id if self.generation == 1 else f"{self.objective_id}::attempt:{self.generation}"
        if self.spec.task_id != expected:
            raise ValueError("attempt identity must match objective and generation")
        if self.predecessor_id == self.spec.task_id:
            raise ValueError("retry must have a fresh attempt ID")


@dataclass(frozen=True)
class RecoveryRequest:
    agent_id: str
    task_id: str
    owner_id: str
    commit_reference: str
    issued_at: int
    trigger: RecoveryTrigger
    reason: str

    def __post_init__(self):
        for value in (self.agent_id, self.task_id, self.owner_id, self.commit_reference, self.reason):
            _id(value)
        _tick(self.issued_at)
        if not isinstance(self.trigger, RecoveryTrigger):
            raise ValueError("invalid recovery trigger")


@dataclass(frozen=True)
class RecoveryEvent:
    tick: int
    objective_id: str
    task_id: str
    agent_id: str | None
    action: str
    reason: str


@dataclass(frozen=True)
class RecoveryFeedback:
    request: RecoveryRequest
    accepted: bool
    reason: str
    availability: ObjectiveAnnouncement | None = None


@dataclass(frozen=True)
class AttemptRecord:
    task: Task
    generation: int
    previous_owner: str | None
    assigned_at: int | None
    ended_at: int | None
    progress_ticks: int
    reason: str


@dataclass(frozen=True)
class ObjectiveSnapshot:
    objective_id: str
    recoverable: bool
    state: str
    current_task_id: str
    attempts: tuple[AttemptRecord, ...]


def _task_data(task):
    data = asdict(task)
    data["status"] = task.status.value
    caps = task.requirements.capabilities
    data["requirements"]["resources"] = [asdict(r) for r in task.requirements.resources]
    data["requirements"]["capabilities"] = {"sensing": sorted(caps.sensing), "operational": sorted(caps.operational)}
    return data


def _decode_task(data):
    _fields(data, Task.__dataclass_fields__)
    req = data["requirements"]
    _fields(req, TaskRequirements.__dataclass_fields__)
    caps = req["capabilities"]
    _fields(caps, {"sensing", "operational"})
    for tags in caps.values():
        if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags) or len(tags) != len(set(tags)):
            raise ValueError("invalid capability tags")
    if not isinstance(req["resources"], list):
        raise ValueError("resources must be an array")
    for resource in req["resources"]:
        _fields(resource, ResourceLevel.__dataclass_fields__)
    requirements = TaskRequirements(Capabilities(frozenset(caps["sensing"]), frozenset(caps["operational"])),
                                   req["minimum_battery"], tuple(ResourceLevel(**r) for r in req["resources"]))
    return Task(**{**data, "requirements": requirements, "status": TaskStatus(data["status"])})


def availability_data(value):
    announcement = asdict(value.announcement)
    announcement["task"] = _task_data(value.announcement.task)
    return {"objective_id": value.objective_id, "generation": value.generation,
            "announcement": announcement, "spec": asdict(value.spec),
            "recoverable": value.recoverable, "predecessor_id": value.predecessor_id}


def decode_availability(data):
    _fields(data, ObjectiveAnnouncement.__dataclass_fields__)
    a = data["announcement"]
    _fields(a, TaskAnnouncement.__dataclass_fields__)
    _fields(data["spec"], TaskExecutionSpec.__dataclass_fields__)
    return ObjectiveAnnouncement(**{**data, "announcement": TaskAnnouncement(**{**a, "task": _decode_task(a["task"])}),
                                    "spec": TaskExecutionSpec(**data["spec"])})


def encode_recovery(kind, agent_id, issued_at, **data):
    return json.dumps({"kind": kind, "agent_id": agent_id, "issued_at": issued_at, **data},
                      sort_keys=True, separators=(",", ":"), allow_nan=False)


def decode_recovery(payload: CoordinationPayload, envelope: MessageEnvelope | None = None):
    if not isinstance(payload, CoordinationPayload) or payload.topic != TOPIC:
        raise ValueError("not a recovery message")
    data = parse_object(payload.data)
    base = {"kind", "agent_id", "issued_at"}
    try:
        kind = data.get("kind")
        if kind == "availability":
            _fields(data, base | {"availability"})
            value = decode_availability(data["availability"])
            if value.announcement.announced_at > data["issued_at"]:
                raise ValueError("future availability")
        elif kind == "heartbeat":
            _fields(data, base | {"task_id", "commit_reference"})
            _id(data["task_id"]); _id(data["commit_reference"])
        else:
            raise ValueError("unknown recovery message kind")
        _id(data["agent_id"]); _tick(data["issued_at"])
        if envelope and (data["agent_id"], data["issued_at"]) != (envelope.sender_id, envelope.sent_at):
            raise ValueError("recovery sender/timestamp mismatch")
        return data
    except (TypeError, KeyError) as exc:
        raise ValueError("invalid recovery schema") from exc
