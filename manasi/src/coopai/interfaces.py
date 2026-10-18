"""Small agent-facing contracts shared by simulation and future adapters."""
from dataclasses import dataclass
from typing import Protocol

from .models import AgentState, Observation, _id, _number, _tick
from .knowledge import KnowledgeEntry
from .messages import DeliveryReceipt, ReceivedStatus
from .priority import PriorityResult, TaskAnnouncement
from .suitability import SuitabilityResult
from .allocation_messages import CommitFeedback
from .execution_models import ExecutionFeedback, ExecutionSnapshot
from .messages import parse_object
from .recovery_models import ObjectiveAnnouncement, RecoveryFeedback


@dataclass(frozen=True)
class SimulationTime:
    tick: int
    tick_seconds: float

    def __post_init__(self) -> None:
        _tick(self.tick)
        _number(self.tick_seconds)
        if self.tick_seconds == 0:
            raise ValueError("tick duration must be positive")

    @property
    def seconds(self) -> float:
        return self.tick * self.tick_seconds


@dataclass(frozen=True)
class AgentCommand:
    """Intent only; physical validation/execution belongs to a later adapter."""
    command_id: str
    agent_id: str
    issued_at: int
    action: str
    target_id: str | None = None
    payload: str = "{}"

    def __post_init__(self) -> None:
        for value in (self.command_id, self.agent_id, self.action):
            _id(value)
        _tick(self.issued_at)
        if self.target_id is not None:
            _id(self.target_id)
        parse_object(self.payload)


@dataclass(frozen=True)
class AgentView:
    time: SimulationTime
    own_state: AgentState
    observations: tuple[Observation, ...]
    knowledge: tuple[KnowledgeEntry, ...] = ()
    messages: tuple[DeliveryReceipt, ...] = ()
    peer_statuses: tuple[ReceivedStatus, ...] = ()
    known_tasks: tuple[TaskAnnouncement, ...] = ()
    evidence_history: tuple[KnowledgeEntry, ...] = ()
    priorities: tuple[PriorityResult, ...] = ()
    suitability: tuple[SuitabilityResult, ...] = ()
    allocation_feedback: tuple[CommitFeedback, ...] = ()
    execution: ExecutionSnapshot | None = None
    execution_feedback: tuple[ExecutionFeedback, ...] = ()
    recovery_feedback: tuple[RecoveryFeedback, ...] = ()
    mission_announcements: tuple[ObjectiveAnnouncement, ...] = ()


class AgentController(Protocol):
    def __call__(self, view: AgentView) -> tuple[AgentCommand, ...]:
        """Return intents using local information only."""
        ...
