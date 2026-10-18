"""Immutable domain values. IDs are opaque nonempty strings; time is integer ticks.

These models express data invariants, not allocation or simulation policies.
"""
from dataclasses import dataclass, field, replace
from enum import Enum
from math import isfinite


def _id(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("identifier must be a nonempty string")


def _number(value: float, low: float = 0, high: float = float("inf")) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or not low <= value <= high:
        raise ValueError(f"expected finite number in [{low}, {high}]")


def _tick(value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("tick must be a nonnegative integer")


def _tags(values: frozenset[str]) -> None:
    if not isinstance(values, frozenset):
        raise ValueError("capability tags must be a frozenset")
    for value in values:
        _id(value)


def _enum(value: Enum, cls: type[Enum]) -> None:
    if not isinstance(value, cls):
        raise ValueError(f"expected {cls.__name__}")


def _json_float(value: str) -> float:
    parsed = float(value)
    if not isfinite(parsed):
        raise ValueError("nonfinite JSON number")
    return parsed


@dataclass(frozen=True)
class AgentIdentity:
    agent_id: str
    label: str

    def __post_init__(self) -> None:
        _id(self.agent_id)
        _id(self.label)


@dataclass(frozen=True)
class Capabilities:
    sensing: frozenset[str] = frozenset()
    operational: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        _tags(self.sensing)
        _tags(self.operational)

    def meets(self, required: "Capabilities") -> bool:
        """All-of matching primitive only; not full agent eligibility."""
        return required.sensing <= self.sensing and required.operational <= self.operational


@dataclass(frozen=True)
class ResourceLevel:
    name: str
    amount: float
    capacity: float

    def __post_init__(self) -> None:
        _id(self.name)
        _number(self.capacity)
        _number(self.amount, high=self.capacity)


class Availability(Enum):
    AVAILABLE = "available"
    BUSY = "busy"
    OFFLINE = "offline"
    FAILED = "failed"


@dataclass(frozen=True)
class Position:
    location_id: str
    x: float | None = None
    y: float | None = None

    def __post_init__(self) -> None:
        _id(self.location_id)
        if (self.x is None) != (self.y is None):
            raise ValueError("position coordinates must be supplied together")
        if self.x is not None:
            _number(self.x, low=-float("inf"))
            _number(self.y, low=-float("inf"))


@dataclass(frozen=True)
class AgentState:
    identity: AgentIdentity
    capabilities: Capabilities
    position: Position
    battery: float
    availability: Availability = Availability.AVAILABLE
    resources: tuple[ResourceLevel, ...] = ()
    task_id: str | None = None
    updated_at: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.identity, AgentIdentity) or not isinstance(self.capabilities, Capabilities) or not isinstance(self.position, Position):
            raise ValueError("invalid agent identity, capabilities or position")
        _number(self.battery, high=1)
        _enum(self.availability, Availability)
        _tick(self.updated_at)
        _resources(self.resources)
        if self.task_id is not None:
            _id(self.task_id)
        if (self.availability is Availability.BUSY) != (self.task_id is not None):
            raise ValueError("exactly busy agents must have a task commitment")


def _resources(resources: tuple[ResourceLevel, ...]) -> None:
    if not isinstance(resources, tuple) or any(not isinstance(r, ResourceLevel) for r in resources):
        raise ValueError("resources must be a tuple of ResourceLevel")
    if len({r.name for r in resources}) != len(resources):
        raise ValueError("duplicate resource names")


@dataclass(frozen=True)
class TaskRequirements:
    capabilities: Capabilities = field(default_factory=Capabilities)
    minimum_battery: float = 0
    resources: tuple[ResourceLevel, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.capabilities, Capabilities):
            raise ValueError("invalid required capabilities")
        _number(self.minimum_battery, high=1)
        _resources(self.resources)


class TaskStatus(Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TRANSITIONS = {
    TaskStatus.PENDING: {TaskStatus.ASSIGNED, TaskStatus.CANCELLED},
    TaskStatus.ASSIGNED: {TaskStatus.IN_PROGRESS, TaskStatus.PENDING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.IN_PROGRESS: {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.PENDING, TaskStatus.CANCELLED},
    TaskStatus.COMPLETED: set(), TaskStatus.FAILED: set(), TaskStatus.CANCELLED: set(),
}


@dataclass(frozen=True)
class Task:
    task_id: str
    location_id: str
    requirements: TaskRequirements = field(default_factory=TaskRequirements)
    severity: float = 0
    urgency: float = 0
    created_at: int = 0
    deadline: int | None = None
    status: TaskStatus = TaskStatus.PENDING
    owner_id: str | None = None

    def __post_init__(self) -> None:
        _id(self.task_id)
        _id(self.location_id)
        if not isinstance(self.requirements, TaskRequirements):
            raise ValueError("invalid task requirements")
        _number(self.severity, high=1)
        _number(self.urgency, high=1)
        _tick(self.created_at)
        if self.deadline is not None:
            _tick(self.deadline)
            if self.deadline < self.created_at:
                raise ValueError("deadline precedes creation")
        _enum(self.status, TaskStatus)
        if self.owner_id is not None:
            _id(self.owner_id)
        if (self.status in {TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED}) != (self.owner_id is not None):
            raise ValueError("assigned, active and completed tasks require an owner; other states forbid one")

    def transition(self, status: TaskStatus, owner_id: str | None = None) -> "Task":
        """Return validated lifecycle value; caller provides committed ownership."""
        _enum(status, TaskStatus)
        if status not in _TRANSITIONS[self.status]:
            raise ValueError("illegal task transition")
        owner = owner_id if self.status is TaskStatus.PENDING else self.owner_id
        if self.status is not TaskStatus.PENDING and owner_id is not None and owner_id != self.owner_id:
            raise ValueError("ownership change requires release to pending first")
        if status in {TaskStatus.PENDING, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            owner = None
        return replace(self, status=status, owner_id=owner)


@dataclass(frozen=True)
class Location:
    location_id: str
    label: str

    def __post_init__(self) -> None:
        _id(self.location_id)
        _id(self.label)


@dataclass(frozen=True)
class Connection:
    source: str
    target: str
    travel_ticks: int = 1

    def __post_init__(self) -> None:
        _id(self.source)
        _id(self.target)
        _tick(self.travel_ticks)
        if self.source == self.target or self.travel_ticks == 0:
            raise ValueError("connections require distinct endpoints and positive travel time")


@dataclass(frozen=True)
class Environment:
    """Static topology only: this value contains no hidden hazards or task truth."""
    locations: tuple[Location, ...]
    connections: tuple[Connection, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.locations, tuple) or not self.locations or any(not isinstance(x, Location) for x in self.locations):
            raise ValueError("locations must be a nonempty tuple of Location")
        ids = {x.location_id for x in self.locations}
        if len(ids) != len(self.locations):
            raise ValueError("duplicate location IDs")
        if not isinstance(self.connections, tuple) or any(not isinstance(x, Connection) for x in self.connections):
            raise ValueError("connections must be a tuple of Connection")
        edges = set()
        for edge in self.connections:
            if edge.source not in ids or edge.target not in ids:
                raise ValueError("unknown connection endpoint")
            pair = (edge.source, edge.target)
            if pair in edges:
                raise ValueError("duplicate directed connection")
            edges.add(pair)


@dataclass(frozen=True)
class Observation:
    observation_id: str
    source_id: str
    location_id: str
    subject: str
    value: str
    observed_at: int
    confidence: float
    sensing_capability: str

    def __post_init__(self) -> None:
        for value in (self.observation_id, self.source_id, self.location_id, self.subject, self.value, self.sensing_capability):
            _id(value)
        _tick(self.observed_at)
        _number(self.confidence, high=1)


class MessageKind(Enum):
    OBSERVATION = "observation"
    STATE = "state"
    TASK = "task"
    COORDINATION = "coordination"


@dataclass(frozen=True)
class MessageEnvelope:
    """Opaque JSON text payload; routing/delivery/fusion remain future components."""
    message_id: str
    sender_id: str
    recipient_id: str
    kind: MessageKind
    sent_at: int
    payload: str
    expires_at: int | None = None
    correlation_id: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        import json
        for value in (self.message_id, self.sender_id, self.recipient_id):
            _id(value)
        if self.sender_id == self.recipient_id:
            raise ValueError("peer message must target another agent")
        _enum(self.kind, MessageKind)
        _tick(self.sent_at)
        if self.schema_version != 1 or type(self.schema_version) is not int:
            raise ValueError("unsupported message schema")
        if self.expires_at is not None:
            _tick(self.expires_at)
            if self.expires_at <= self.sent_at:
                raise ValueError("expiry must follow send time")
        if self.correlation_id is not None:
            _id(self.correlation_id)
        if not isinstance(self.payload, str):
            raise ValueError("payload must be JSON text")
        try:
            decoded = json.loads(self.payload, parse_float=_json_float,
                                 parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        except (ValueError, TypeError) as exc:
            raise ValueError("payload must contain valid finite JSON") from exc
        if not isinstance(decoded, dict):
            raise ValueError("payload must be a JSON object")


class EventKind(Enum):
    HAZARD_CHANGED = "hazard_changed"
    TASK_DISCOVERED = "task_discovered"
    AGENT_FAILED = "agent_failed"
    AGENT_RECOVERED = "agent_recovered"
    RESOURCES_CHANGED = "resources_changed"
    COMMUNICATION_CHANGED = "communication_changed"


@dataclass(frozen=True)
class DynamicEvent:
    event_id: str
    tick: int
    kind: EventKind
    target_id: str
    description: str

    def __post_init__(self) -> None:
        for value in (self.event_id, self.target_id, self.description):
            _id(value)
        _tick(self.tick)
        _enum(self.kind, EventKind)


@dataclass(frozen=True)
class MissionConfiguration:
    """Public mission settings, not hidden scenario truth or an allocation plan."""
    mission_id: str
    seed: int
    duration_ticks: int
    tick_seconds: float = 1
    objectives: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _id(self.mission_id)
        _tick(self.seed)
        _tick(self.duration_ticks)
        _number(self.tick_seconds)
        if self.duration_ticks == 0 or self.tick_seconds == 0:
            raise ValueError("mission duration and tick size must be positive")
        if not isinstance(self.objectives, tuple):
            raise ValueError("objectives must be a tuple")
        for objective in self.objectives:
            _id(objective)
