"""Strict JSON scenario loading and trusted scripted world changes.

Scenario objects contain private ground truth. They must never be decision inputs.
"""
from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import Callable, TypeVar

from .geometry import AreaGeometry, FloorPlan, Passage, Rectangle, Segment
from .models import (
    AgentIdentity, AgentState, Availability, Capabilities, DynamicEvent, EventKind,
    Location, MissionConfiguration, Position, ResourceLevel, Task, TaskRequirements,
    TaskStatus, _id, _json_float, _number, _tick,
)
from .simulation import SimulationEngine
from .world import WorldCondition, WorldSnapshot
from .execution_models import ExecutionConfiguration, scenario_execution_specs
from .priority import PriorityConfiguration, TaskAnnouncement, configuration_from_dict
from .communication import CommunicationConfiguration
from .allocation_messages import AllocationConfiguration
from .navigation import located_position


class ScenarioError(ValueError):
    """Configuration error including the relevant field or section."""


@dataclass(frozen=True)
class TaskDetails:
    task_id: str
    task_type: str
    duration_ticks: int

    def __post_init__(self) -> None:
        _id(self.task_id)
        _id(self.task_type)
        _tick(self.duration_ticks)
        if self.duration_ticks == 0:
            raise ValueError("task duration must be positive")


@dataclass(frozen=True)
class ConditionChange:
    event: DynamicEvent
    condition: WorldCondition

    def __post_init__(self) -> None:
        if not isinstance(self.event, DynamicEvent) or not isinstance(self.condition, WorldCondition):
            raise ValueError("invalid condition event")
        if self.event.kind is not EventKind.HAZARD_CHANGED or self.event.target_id != self.condition.location_id:
            raise ValueError("condition event kind/target mismatch")

    def apply(self, sim: SimulationEngine, event: DynamicEvent) -> None:
        key = (self.condition.location_id, self.condition.subject)
        conditions = tuple(c for c in sim.world.conditions if (c.location_id, c.subject) != key)
        sim.replace_world(replace(sim.world, conditions=(*conditions, self.condition)))


@dataclass(frozen=True)
class BatteryChange:
    event: DynamicEvent
    battery: float

    def __post_init__(self) -> None:
        if not isinstance(self.event, DynamicEvent) or self.event.kind is not EventKind.RESOURCES_CHANGED:
            raise ValueError("invalid battery event")
        _number(self.battery, high=1)

    def apply(self, sim: SimulationEngine, event: DynamicEvent) -> None:
        agent = next(a for a in sim.world.agents if a.identity.agent_id == event.target_id)
        sim.update_agent(replace(agent, battery=self.battery, updated_at=sim.time.tick))


@dataclass(frozen=True)
class ConnectionChange:
    """Network connectivity only; does not change physical availability."""
    event: DynamicEvent
    connected: bool

    def __post_init__(self) -> None:
        if not isinstance(self.event, DynamicEvent) or self.event.kind is not EventKind.COMMUNICATION_CHANGED or type(self.connected) is not bool:
            raise ValueError("invalid connection change")

    def apply(self, sim: SimulationEngine, event: DynamicEvent) -> None:
        if sim.communication is None:
            raise ValueError("connection change requires communication")
        sim.communication.set_connected(event.target_id, self.connected)


@dataclass(frozen=True)
class PowerFailure:
    """An external power outage; normal physical execution detects inability to work."""
    event: DynamicEvent

    def __post_init__(self) -> None:
        if not isinstance(self.event, DynamicEvent) or self.event.kind is not EventKind.AGENT_FAILED:
            raise ValueError("invalid power failure")

    def apply(self, sim: SimulationEngine, event: DynamicEvent) -> None:
        agent = next(a for a in sim.world.agents if a.identity.agent_id == event.target_id)
        if agent.task_id is not None:
            sim.update_agent(replace(agent, battery=0, updated_at=sim.time.tick))
            if sim.execution_enabled:
                # Use the existing physical stop path even if ownership was only
                # just admitted and navigation has not initialized its snapshot.
                sim._execution_adapter._fail(sim, agent.identity.agent_id,
                    "Operational power failure: " + event.description, agent.task_id)
            else:
                failed = next(t for t in sim.world.tasks if t.task_id == agent.task_id).transition(TaskStatus.FAILED)
                offline = replace(agent, battery=0, availability=Availability.OFFLINE, task_id=None, updated_at=sim.time.tick)
                sim.replace_world(replace(sim.world,
                    agents=tuple(offline if a.identity.agent_id == agent.identity.agent_id else a for a in sim.world.agents),
                    tasks=tuple(failed if t.task_id == failed.task_id else t for t in sim.world.tasks)))
        else:
            sim.update_agent(replace(agent, battery=0, availability=Availability.OFFLINE, updated_at=sim.time.tick))


@dataclass(frozen=True)
class TaskDiscovery:
    event: DynamicEvent
    task: Task
    details: TaskDetails
    recipient_id: str
    recoverable: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.event, DynamicEvent) or self.event.kind is not EventKind.TASK_DISCOVERED or not isinstance(self.task, Task) or not isinstance(self.details, TaskDetails):
            raise ValueError("invalid task discovery")
        _id(self.recipient_id)
        if self.task.task_id != self.details.task_id or self.event.target_id != self.task.task_id or self.task.created_at != self.event.tick or self.task.status is not TaskStatus.PENDING or type(self.recoverable) is not bool:
            raise ValueError("discovery task identity, lifecycle or timestamp mismatch")
        scenario_execution_specs((self.details,))

    def apply(self, sim: SimulationEngine, event: DynamicEvent) -> None:
        sim.introduce_objective(self.task, scenario_execution_specs((self.details,))[0], self.recipient_id, recoverable=self.recoverable)


@dataclass(frozen=True)
class Scenario:
    mission: MissionConfiguration
    floor_plan: FloorPlan
    initial_world: WorldSnapshot
    task_details: tuple[TaskDetails, ...]
    events: tuple[ConditionChange | BatteryChange | ConnectionChange | PowerFailure | TaskDiscovery, ...]
    priority_configuration: PriorityConfiguration = PriorityConfiguration()
    allow_infeasible_objectives: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.priority_configuration, PriorityConfiguration):
            raise ValueError("invalid mission priority configuration")
        if type(self.allow_infeasible_objectives) is not bool:
            raise ValueError("allow_infeasible_objectives must be boolean")
        if not isinstance(self.mission, MissionConfiguration) or not isinstance(self.floor_plan, FloorPlan) or not isinstance(self.initial_world, WorldSnapshot):
            raise ValueError("invalid mission, floor plan or world")
        if self.floor_plan.environment() != self.initial_world.environment:
            raise ValueError("geometry and topology disagree")
        if not isinstance(self.task_details, tuple) or any(not isinstance(t, TaskDetails) for t in self.task_details):
            raise ValueError("invalid task details")
        details = {t.task_id for t in self.task_details}
        if len(details) != len(self.task_details) or details != {t.task_id for t in self.initial_world.tasks}:
            raise ValueError("task details must match unique world task IDs")
        if not isinstance(self.events, tuple) or any(not isinstance(e, (ConditionChange, BatteryChange, ConnectionChange, PowerFailure, TaskDiscovery)) for e in self.events):
            raise ValueError("invalid events")
        if len({e.event.event_id for e in self.events}) != len(self.events):
            raise ValueError("duplicate event ID")
        locations = {a.location.location_id for a in self.floor_plan.areas}
        agents = {a.identity.agent_id for a in self.initial_world.agents}
        discovered = set(details)
        for change in self.events:
            if change.event.tick >= self.mission.duration_ticks:
                raise ValueError(f"event {change.event.event_id} lies outside mission horizon")
            if isinstance(change, TaskDiscovery):
                if change.task.task_id in discovered or change.recipient_id not in agents or change.task.location_id not in locations:
                    raise ValueError("duplicate discovered task or unknown discovery recipient/location")
                discovered.add(change.task.task_id)
                continue
            targets = locations if isinstance(change, ConditionChange) else agents
            if change.event.target_id not in targets:
                raise ValueError(f"event {change.event.event_id} has unknown target")
        if any(a.updated_at != 0 for a in self.initial_world.agents):
            raise ValueError("initial agents must have timestamp 0")
        for agent in self.initial_world.agents:
            located_position(self.floor_plan, agent.position)
        if any(t.created_at != 0 or t.status is not TaskStatus.PENDING for t in self.initial_world.tasks):
            raise ValueError("initial tasks must be pending, unassigned and created at tick 0")
        # Scenario sanity check only, not a runtime eligibility/selection policy.
        adjacency = {loc: set() for loc in locations}
        for edge in self.initial_world.environment.connections:
            adjacency[edge.source].add(edge.target)
        def reachable(start: str) -> set[str]:
            seen, todo = set(), [start]
            while todo:
                node = todo.pop()
                if node not in seen:
                    seen.add(node)
                    todo.extend(sorted(adjacency[node] - seen))
            return seen
        entries = [p.source for p in self.floor_plan.passages if p.target is None]
        if not entries or any(reachable(start) != locations for start in entries):
            raise ValueError("every location must be reachable from each configured entry")
        for task in self.initial_world.tasks:
            def covers(a: AgentState) -> bool:
                supplies = {r.name: r.amount for r in a.resources}
                return (a.availability is Availability.AVAILABLE and
                        a.capabilities.meets(task.requirements.capabilities) and
                        a.battery >= task.requirements.minimum_battery and
                        all(supplies.get(r.name, 0) >= r.amount for r in task.requirements.resources) and
                        task.location_id in reachable(a.position.location_id))
            if not self.allow_infeasible_objectives and not any(covers(a) for a in self.initial_world.agents):
                raise ValueError(f"task {task.task_id}: no initial agent covers capability/resource/reachability requirements")

    def create_engine(self, *, with_perception: bool = False, with_communication: bool = False,
                      with_priority: bool = False, with_suitability: bool = False,
                      with_allocation: bool = False, with_execution: bool = False, with_recovery: bool = False,
                      communication_configuration: CommunicationConfiguration = CommunicationConfiguration(),
                      execution_configuration: ExecutionConfiguration = ExecutionConfiguration()) -> SimulationEngine:
        with_execution = with_execution or with_recovery
        engine = SimulationEngine(self.mission, self.initial_world)
        for change in self.events:
            engine.schedule(change.event, change.apply)
        if with_perception or with_communication or with_priority or with_suitability or with_allocation or with_execution:
            from .perception import install_local_perception
            install_local_perception(engine)
        if with_communication or with_allocation or with_execution:
            engine.enable_communication(communication_configuration)
        if with_priority or with_suitability or with_allocation or with_execution:
            engine.enable_task_priority(self.priority_configuration)
            details = {d.task_id: d for d in self.task_details}
            # Public mission objectives are explicitly announced, not allocated.
            for agent in self.initial_world.agents:
                for task in self.initial_world.tasks:
                    engine.announce_task(agent.identity.agent_id, TaskAnnouncement(task, details[task.task_id].task_type, 0))
        if with_suitability or with_allocation or with_execution:
            engine.enable_self_evaluation(self.initial_world.environment)
        if with_allocation or with_execution:
            engine.enable_allocation(AllocationConfiguration.for_latency(communication_configuration.latency_ticks))
        if with_execution:
            engine.enable_execution(self.floor_plan, scenario_execution_specs(self.task_details), execution_configuration)
        if with_recovery:
            engine.enable_recovery()
        return engine


T = TypeVar("T")


def _build(path: str, factory: Callable[[], T]) -> T:
    try:
        return factory()
    except (ValueError, TypeError) as exc:
        raise ScenarioError(f"{path}: {exc}") from exc


def _object(value: object, path: str, required: set[str], optional: set[str] | None = None) -> dict:
    if not isinstance(value, dict):
        raise ScenarioError(f"{path}: expected object")
    if any(not isinstance(key, str) for key in value):
        raise ScenarioError(f"{path}: field names must be strings")
    missing = required - value.keys()
    unknown = value.keys() - required - (optional or set())
    if missing or unknown:
        raise ScenarioError(f"{path}: missing fields {sorted(missing)}, unknown fields {sorted(unknown)}")
    return value


def _array(value: object, path: str) -> list:
    if not isinstance(value, list):
        raise ScenarioError(f"{path}: expected array")
    return value


def _tags(value: object, path: str) -> frozenset[str]:
    values = _array(value, path)
    for tag in values:
        _build(path, lambda: _id(tag))
    if len(set(values)) != len(values):
        raise ScenarioError(f"{path}: duplicate capability tag")
    return frozenset(values)


def _capabilities(value: object, path: str) -> Capabilities:
    obj = _object(value, path, {"sensing", "operational"})
    return Capabilities(_tags(obj["sensing"], path + ".sensing"), _tags(obj["operational"], path + ".operational"))


def _resources(value: object, path: str) -> tuple[ResourceLevel, ...]:
    result = []
    for i, item in enumerate(_array(value, path)):
        p = f"{path}[{i}]"
        obj = _object(item, p, {"name", "amount", "capacity"})
        result.append(_build(p, lambda: ResourceLevel(**obj)))
    return tuple(result)


def task_from_dict(value: object, path: str, *, created_at: int = 0) -> tuple[Task, TaskDetails]:
    """One schema for initial objectives and explicit runtime discoveries."""
    obj = _object(value, path, {"id", "type", "location", "requirements", "severity", "urgency", "deadline", "status", "duration_ticks"})
    req = _object(obj["requirements"], path + ".requirements", {"capabilities", "minimum_battery", "resources"})
    caps = _capabilities(req["capabilities"], path + ".requirements.capabilities")
    resources = _resources(req["resources"], path + ".requirements.resources")
    requirements = _build(path + ".requirements", lambda: TaskRequirements(caps, req["minimum_battery"], resources))
    task = _build(path, lambda: Task(obj["id"], obj["location"], requirements, obj["severity"], obj["urgency"], created_at, obj["deadline"], TaskStatus(obj["status"])))
    details = _build(path, lambda: TaskDetails(obj["id"], obj["type"], obj["duration_ticks"]))
    return task, details


def scenario_from_dict(data: object) -> Scenario:
    """Build validated immutable values without retaining mutable input dictionaries."""
    root = _object(data, "scenario", {"schema_version", "mission", "locations", "passages", "agents", "tasks", "conditions", "events"}, {"priority", "allow_infeasible_objectives"})
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise ScenarioError("schema_version: only integer version 1 is supported")
    obj = _object(root["mission"], "mission", {"mission_id", "seed", "duration_ticks", "tick_seconds", "objectives"})
    mission = _build("mission", lambda: MissionConfiguration(**{**obj, "objectives": tuple(_array(obj["objectives"], "mission.objectives"))}))
    areas = []
    for i, item in enumerate(_array(root["locations"], "locations")):
        p = f"locations[{i}]"
        obj = _object(item, p, {"id", "name", "type", "bounds"})
        bounds = _object(obj["bounds"], p + ".bounds", {"x", "y", "width", "height"})
        areas.append(_build(p, lambda: AreaGeometry(Location(obj["id"], obj["name"]), obj["type"], Rectangle(**bounds))))
    passages = []
    for i, item in enumerate(_array(root["passages"], "passages")):
        p = f"passages[{i}]"
        obj = _object(item, p, {"id", "source", "target", "opening", "travel_ticks"})
        opening = _object(obj["opening"], p + ".opening", {"x1", "y1", "x2", "y2"})
        passages.append(_build(p, lambda: Passage(obj["id"], obj["source"], obj["target"], Segment(**opening), obj["travel_ticks"])))
    floor_plan = _build("floor_plan", lambda: FloorPlan(tuple(areas), tuple(passages)))
    agents = []
    for i, item in enumerate(_array(root["agents"], "agents")):
        p = f"agents[{i}]"
        obj = _object(item, p, {"id", "name", "location", "capabilities", "battery", "availability", "resources"}, {"position"})
        caps = _capabilities(obj["capabilities"], p + ".capabilities")
        resources = _resources(obj["resources"], p + ".resources")
        coordinates = _object(obj["position"], p + ".position", {"x", "y"}) if "position" in obj else {}
        if coordinates and any(v is None for v in coordinates.values()):
            raise ScenarioError(f"{p}.position: coordinates must be numeric")
        agents.append(_build(p, lambda: AgentState(AgentIdentity(obj["id"], obj["name"]), caps, Position(obj["location"], **coordinates), obj["battery"], Availability(obj["availability"]), resources)))
    tasks, details = [], []
    for i, item in enumerate(_array(root["tasks"], "tasks")):
        p = f"tasks[{i}]"
        task, detail = task_from_dict(item, p)
        tasks.append(task)
        details.append(detail)
    conditions = []
    for i, item in enumerate(_array(root["conditions"], "conditions")):
        p = f"conditions[{i}]"
        obj = _object(item, p, {"location_id", "subject", "value"})
        conditions.append(_build(p, lambda: WorldCondition(**obj)))
    changes = []
    for i, item in enumerate(_array(root["events"], "events")):
        p = f"events[{i}]"
        if not isinstance(item, dict):
            raise ScenarioError(f"{p}: expected object")
        action = item.get("action")
        if action == "set_condition":
            obj = _object(item, p, {"id", "tick", "action", "target", "description", "subject", "value"})
            changes.append(_build(p, lambda: ConditionChange(DynamicEvent(obj["id"], obj["tick"], EventKind.HAZARD_CHANGED, obj["target"], obj["description"]), WorldCondition(obj["target"], obj["subject"], obj["value"]))))
        elif action == "set_battery":
            obj = _object(item, p, {"id", "tick", "action", "target", "description", "battery"})
            changes.append(_build(p, lambda: BatteryChange(DynamicEvent(obj["id"], obj["tick"], EventKind.RESOURCES_CHANGED, obj["target"], obj["description"]), obj["battery"])))
        elif action == "set_connected":
            obj = _object(item, p, {"id", "tick", "action", "target", "description", "connected"})
            changes.append(_build(p, lambda: ConnectionChange(DynamicEvent(obj["id"], obj["tick"], EventKind.COMMUNICATION_CHANGED, obj["target"], obj["description"]), obj["connected"])))
        elif action == "power_failure":
            obj = _object(item, p, {"id", "tick", "action", "target", "description"})
            changes.append(_build(p, lambda: PowerFailure(DynamicEvent(obj["id"], obj["tick"], EventKind.AGENT_FAILED, obj["target"], obj["description"]))))
        elif action == "discover_task":
            obj = _object(item, p, {"id", "tick", "action", "target", "description", "task", "recipient", "recoverable"})
            task, detail = task_from_dict(obj["task"], p + ".task", created_at=obj["tick"])
            changes.append(_build(p, lambda: TaskDiscovery(DynamicEvent(obj["id"], obj["tick"], EventKind.TASK_DISCOVERED, obj["target"], obj["description"]), task, detail, obj["recipient"], obj["recoverable"])))
        else:
            raise ScenarioError(f"{p}.action: unsupported action {action!r}")
    world = _build("world", lambda: WorldSnapshot(floor_plan.environment(), tuple(agents), tuple(tasks), tuple(conditions)))
    priority = _build("priority", lambda: configuration_from_dict(root["priority"])) if "priority" in root else PriorityConfiguration()
    return _build("scenario", lambda: Scenario(mission, floor_plan, world, tuple(details), tuple(changes), priority, root.get("allow_infeasible_objectives", False)))


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ScenarioError(f"JSON: duplicate object key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ScenarioError(f"JSON: nonfinite number {value}")


def load_scenario(path: str | Path) -> Scenario:
    try:
        with Path(path).open(encoding="utf-8") as handle:
            data = json.load(handle, object_pairs_hook=_unique_object,
                             parse_float=_json_float, parse_constant=_reject_constant)
        return scenario_from_dict(data)
    except (OSError, ValueError, TypeError) as exc:
        raise ScenarioError(f"{path}: {exc}") from exc
