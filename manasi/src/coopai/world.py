"""Authoritative immutable snapshots for trusted simulation systems only."""
from dataclasses import dataclass

from .models import AgentState, Environment, Task, TaskStatus, _id


@dataclass(frozen=True)
class WorldCondition:
    location_id: str
    subject: str
    value: str

    def __post_init__(self) -> None:
        for value in (self.location_id, self.subject, self.value):
            _id(value)


@dataclass(frozen=True)
class WorldSnapshot:
    environment: Environment
    agents: tuple[AgentState, ...] = ()
    tasks: tuple[Task, ...] = ()
    conditions: tuple[WorldCondition, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.environment, Environment):
            raise ValueError("invalid environment")
        for values, cls in ((self.agents, AgentState), (self.tasks, Task), (self.conditions, WorldCondition)):
            if not isinstance(values, tuple) or any(not isinstance(x, cls) for x in values):
                raise ValueError(f"expected tuple of {cls.__name__}")
        locations = {x.location_id for x in self.environment.locations}
        agents = {x.identity.agent_id: x for x in self.agents}
        tasks = {x.task_id: x for x in self.tasks}
        if len(agents) != len(self.agents) or len(tasks) != len(self.tasks):
            raise ValueError("duplicate entity ID")
        keys = {(x.location_id, x.subject) for x in self.conditions}
        if len(keys) != len(self.conditions):
            raise ValueError("duplicate condition")
        if any(x.position.location_id not in locations for x in self.agents):
            raise ValueError("unknown agent location")
        if any(x.location_id not in locations for x in (*self.tasks, *self.conditions)):
            raise ValueError("unknown task or condition location")
        for task in self.tasks:
            if task.owner_id is not None and task.owner_id not in agents:
                raise ValueError("unknown owner")
            if task.status in {TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS}:
                if agents[task.owner_id].task_id != task.task_id:
                    raise ValueError("active task and agent commitment disagree")
        for agent in self.agents:
            if agent.task_id is not None:
                task = tasks.get(agent.task_id)
                if task is None or task.owner_id != agent.identity.agent_id or task.status not in {TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS}:
                    raise ValueError("agent commitment has no matching active task")
