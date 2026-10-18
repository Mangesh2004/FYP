"""Abstract execution contracts, independent of simulator and rendering."""
from dataclasses import asdict, dataclass, fields
from enum import Enum
import json

from .allocation_messages import task_digest
from .messages import CoordinationPayload, parse_object
from .models import MessageEnvelope, Observation, Position, ResourceLevel, Task, TaskStatus, _id, _number, _tick, _resources

TOPIC = "execution.v1"


class ExecutionPhase(Enum):
    IDLE = "idle"
    ASSIGNED = "assigned"
    PLANNING = "planning"
    MOVING = "moving"
    ARRIVED = "arrived"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class ExecutionConfiguration:
    speed_units_per_tick: float = 1.5
    movement_energy_per_unit: float = 0.01
    execution_energy_per_tick: float = 0.01
    minimum_operating_battery: float = 0.05
    arrival_tolerance: float = 0.05
    evidence_max_age_ticks: int = 2
    inspection_confidence: float = 0.6
    notification_interval_ticks: int = 3

    def __post_init__(self):
        for name in ("speed_units_per_tick","arrival_tolerance"):
            _number(getattr(self,name))
            if getattr(self,name)==0:
                raise ValueError("movement speed/arrival tolerance must be positive")
        for name in ("movement_energy_per_unit","execution_energy_per_tick"):
            _number(getattr(self,name))
        _number(self.minimum_operating_battery,high=1)
        if self.minimum_operating_battery==0:
            raise ValueError("operating battery minimum must be positive")
        _number(self.inspection_confidence,high=1)
        _tick(self.evidence_max_age_ticks)
        _tick(self.notification_interval_ticks)
        if self.notification_interval_ticks==0:
            raise ValueError("notification interval must be positive")


@dataclass(frozen=True)
class TaskExecutionSpec:
    task_id: str
    task_type: str
    duration_ticks: int
    subject: str
    sensing_capability: str | None = None
    inspection_operation: str | None = None

    def __post_init__(self):
        for value in (self.task_id,self.task_type,self.subject): _id(value)
        _tick(self.duration_ticks)
        if not self.duration_ticks: raise ValueError("execution duration must be positive")
        if (self.sensing_capability is None)==(self.inspection_operation is None):
            raise ValueError("spec requires exactly one sensing or inspection method")
        for value in (self.sensing_capability,self.inspection_operation):
            if value is not None: _id(value)

    @property
    def observation_method(self):
        return self.sensing_capability or "operational:"+self.inspection_operation


def scenario_execution_specs(details) -> tuple[TaskExecutionSpec,...]:
    methods={"fire-verification":("fire","thermal",None),"gas-inspection":("gas","gas",None),
             "person-search":("person","visual",None),"routine-inspection":("hazard",None,"exploration")}
    result=[]
    for detail in details:
        if detail.task_type not in methods:
            raise ValueError(f"No execution procedure defined for {detail.task_type}")
        result.append(TaskExecutionSpec(detail.task_id,detail.task_type,detail.duration_ticks,*methods[detail.task_type]))
    return tuple(result)


@dataclass(frozen=True)
class ExecutionOutcome:
    agent_id: str
    task_id: str
    tick: int
    success: bool
    reason: str
    findings: tuple[Observation,...] = ()

    def __post_init__(self):
        for value in (self.agent_id,self.task_id,self.reason): _id(value)
        _tick(self.tick)
        if type(self.success) is not bool or not isinstance(self.findings,tuple) or any(not isinstance(o,Observation) or o.source_id!=self.agent_id or o.observed_at>self.tick for o in self.findings):
            raise ValueError("invalid execution outcome")
        if self.success and not self.findings:
            raise ValueError("success requires actual own observations")


@dataclass(frozen=True)
class ExecutionSnapshot:
    agent_id: str
    task_id: str | None = None
    phase: ExecutionPhase = ExecutionPhase.IDLE
    route: tuple[Position,...] = ()
    waypoint_index: int = 0
    progress_ticks: int = 0
    duration_ticks: int = 0
    consumed_resources: tuple[ResourceLevel,...] = ()
    outcome: ExecutionOutcome | None = None
    reason: str = ""
    notice_sequence: int = 0
    commit_reference: str = ""
    task_base_digest: str = ""

    def __post_init__(self):
        _id(self.agent_id)
        if self.task_id is not None: _id(self.task_id)
        if not isinstance(self.phase,ExecutionPhase) or not isinstance(self.route,tuple) or any(not isinstance(p,Position) or p.x is None for p in self.route):
            raise ValueError("invalid execution phase/route")
        for value in (self.waypoint_index,self.progress_ticks,self.duration_ticks,self.notice_sequence): _tick(value)
        if self.phase is not ExecutionPhase.IDLE and self.task_id is None:
            raise ValueError("active/terminal execution requires a task identity")
        if self.progress_ticks>self.duration_ticks or self.waypoint_index>len(self.route):
            raise ValueError("invalid execution progress/index")
        _resources(self.consumed_resources)
        if self.outcome is not None and (not isinstance(self.outcome,ExecutionOutcome) or (self.outcome.agent_id,self.outcome.task_id)!=(self.agent_id,self.task_id)):
            raise ValueError("outcome identity mismatch")
        if self.phase is ExecutionPhase.COMPLETED and (self.outcome is None or not self.outcome.success or self.progress_ticks!=self.duration_ticks or self.duration_ticks==0):
            raise ValueError("completed execution requires full progress and a successful report")
        if self.phase is ExecutionPhase.FAILED and (self.outcome is None or self.outcome.success):
            raise ValueError("failed execution requires a failure outcome")


@dataclass(frozen=True)
class ExecutionFeedback:
    agent_id: str
    tick: int
    accepted: bool
    action: str
    reason: str
    task: Task | None = None
    snapshot: ExecutionSnapshot | None = None

    def __post_init__(self):
        for value in (self.agent_id,self.action,self.reason): _id(value)
        _tick(self.tick)
        if type(self.accepted) is not bool or (self.task is not None and not isinstance(self.task,Task)):
            raise ValueError("invalid execution feedback")
        if self.snapshot is not None and self.snapshot.agent_id!=self.agent_id:
            raise ValueError("foreign execution snapshot")


@dataclass(frozen=True)
class ExecutionEvent:
    tick: int
    agent_id: str
    task_id: str | None
    action: str
    reason: str
    position: Position
    battery: float
    progress_ticks: int


@dataclass(frozen=True)
class ExecutionNotice:
    task_id: str
    agent_id: str
    status: TaskStatus
    sequence: int
    issued_at: int
    task_base_digest: str
    commit_reference: str
    reason: str

    def __post_init__(self):
        for value in (self.task_id,self.agent_id,self.commit_reference): _id(value)
        _tick(self.sequence); _tick(self.issued_at)
        if not self.sequence or self.status not in {TaskStatus.IN_PROGRESS,TaskStatus.COMPLETED,TaskStatus.FAILED,TaskStatus.CANCELLED}:
            raise ValueError("invalid execution notice status/sequence")
        if not isinstance(self.task_base_digest,str) or len(self.task_base_digest)!=64 or any(c not in '0123456789abcdef' for c in self.task_base_digest):
            raise ValueError("invalid task fingerprint")
        if not isinstance(self.reason,str): raise ValueError("invalid notice reason")


def encode_notice(notice: ExecutionNotice) -> str:
    data=asdict(notice); data["status"]=notice.status.value
    return json.dumps(data,sort_keys=True,separators=(",",":"),allow_nan=False)


def decode_notice(payload: CoordinationPayload, envelope: MessageEnvelope | None = None) -> ExecutionNotice:
    if not isinstance(payload,CoordinationPayload) or payload.topic!=TOPIC:
        raise ValueError("not an execution notice")
    data=parse_object(payload.data)
    if set(data)!={f.name for f in fields(ExecutionNotice)}: raise ValueError("execution notice fields mismatch")
    try: notice=ExecutionNotice(**{**data,"status":TaskStatus(data["status"])})
    except (TypeError,KeyError) as exc: raise ValueError("invalid execution notice schema") from exc
    if envelope is not None and (notice.agent_id,notice.issued_at)!=(envelope.sender_id,envelope.sent_at):
        raise ValueError("execution notice sender/timestamp mismatch")
    return notice
