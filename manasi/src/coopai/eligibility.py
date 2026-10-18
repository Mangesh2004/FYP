"""Pure agent-local feasibility checks and directed public-topology travel estimates."""
from dataclasses import dataclass
from decimal import Decimal
from heapq import heappop, heappush
from typing import Protocol

from .knowledge import KnowledgeEntry
from .models import AgentState, Availability, Environment, ResourceLevel, TaskStatus, _id, _number, _tick, _resources
from .priority import PriorityContext, TaskAnnouncement


def shortest_path(environment: Environment, source: str, target: str) -> tuple[str, ...] | None:
    """Directed Dijkstra; equal-cost paths use lexicographic location sequences."""
    if not isinstance(environment, Environment):
        raise ValueError("expected public Environment")
    ids = {loc.location_id for loc in environment.locations}
    if source not in ids or target not in ids:
        raise ValueError("unknown travel endpoint")
    best, queue = {source: (0, (source,))}, [(0, (source,), source)]
    edges = {loc: [] for loc in ids}
    for edge in environment.connections:
        edges[edge.source].append((edge.target, edge.travel_ticks))
    while queue:
        cost, path, node = heappop(queue)
        if (cost, path) != best[node]:
            continue
        if node == target:
            return path
        for neighbor, weight in sorted(edges[node]):
            candidate = cost + weight, (*path, neighbor)
            if neighbor not in best or candidate < best[neighbor]:
                best[neighbor] = candidate
                heappush(queue, (*candidate, neighbor))
    return None


def travel_ticks(environment: Environment, source: str, target: str) -> int | None:
    """Cost only; shares route selection with movement, never moves entities."""
    path = shortest_path(environment, source, target)
    if path is None:
        return None
    costs = {(edge.source, edge.target): edge.travel_ticks for edge in environment.connections}
    return sum(costs[a, b] for a, b in zip(path, path[1:]))


@dataclass(frozen=True)
class EnvironmentalRestriction:
    """Optional hard prohibition from fresh local reports at the task location.

    Unknown/expired evidence fails only when require_known is explicitly enabled.
    """
    subject: str
    prohibited_values: frozenset[str]
    minimum_confidence: float = 0.5
    max_age_ticks: int = 3
    require_known: bool = False

    def __post_init__(self):
        _id(self.subject)
        if not isinstance(self.prohibited_values, frozenset):
            raise ValueError("prohibited values must be a frozenset")
        for value in self.prohibited_values:
            _id(value)
        _number(self.minimum_confidence, high=1)
        _tick(self.max_age_ticks)
        if type(self.require_known) is not bool:
            raise ValueError("require_known must be boolean")


@dataclass(frozen=True)
class EligibilityConfiguration:
    minimum_battery: float = 0.1
    battery_per_travel_tick: float = 0.01
    restrictions: tuple[EnvironmentalRestriction, ...] = ()

    def __post_init__(self):
        _number(self.minimum_battery, high=1)
        _number(self.battery_per_travel_tick, high=1)
        if not isinstance(self.restrictions, tuple) or any(not isinstance(r, EnvironmentalRestriction) for r in self.restrictions):
            raise ValueError("invalid environmental restrictions")
        if len({r.subject for r in self.restrictions}) != len(self.restrictions):
            raise ValueError("duplicate restriction subject")


@dataclass(frozen=True)
class SelfEvaluationContext:
    own_state: AgentState
    tick: int
    known_tasks: tuple[TaskAnnouncement, ...]
    evidence: tuple[KnowledgeEntry, ...]
    topology: Environment
    consumed_resources: tuple[ResourceLevel, ...] = ()

    def __post_init__(self):
        if not isinstance(self.own_state, AgentState) or not isinstance(self.topology, Environment):
            raise ValueError("invalid own state or public topology")
        _resources(self.consumed_resources)
        # Reuse the established owner/receipt/announcement trust-boundary checks.
        PriorityContext(self.own_state.identity.agent_id, self.tick, self.known_tasks, self.evidence)
        if self.own_state.updated_at > self.tick or any(a.task.created_at > self.tick for a in self.known_tasks):
            raise ValueError("future state or task")
        observations = {}
        for entry in self.evidence:
            obs = entry.observation
            if obs.observation_id in observations and observations[obs.observation_id] != obs:
                raise ValueError("conflicting content for evidence ID")
            observations[obs.observation_id] = obs
        ids = {loc.location_id for loc in self.topology.locations}
        if self.own_state.position.location_id not in ids or any(a.task.location_id not in ids for a in self.known_tasks):
            raise ValueError("unknown location in self-evaluation context")

    def task(self, task_id: str) -> TaskAnnouncement:
        result = next((a for a in self.known_tasks if a.task.task_id == task_id), None)
        if result is None:
            raise ValueError("task has not been announced to this agent")
        return result


@dataclass(frozen=True)
class ConstraintResult:
    name: str
    passed: bool
    explanation: str

    def __post_init__(self):
        _id(self.name)
        _id(self.explanation)
        if type(self.passed) is not bool:
            raise ValueError("constraint outcome must be boolean")


@dataclass(frozen=True)
class EligibilityResult:
    agent_id: str
    task_id: str
    eligible: bool
    evaluation_tick: int
    constraints: tuple[ConstraintResult, ...]
    travel_ticks: int | None
    required_battery: float
    explanation: str

    def __post_init__(self):
        _id(self.agent_id)
        _id(self.task_id)
        _id(self.explanation)
        _tick(self.evaluation_tick)
        if self.travel_ticks is not None:
            _tick(self.travel_ticks)
        _number(self.required_battery)
        if not isinstance(self.constraints, tuple) or not self.constraints or any(not isinstance(c, ConstraintResult) for c in self.constraints):
            raise ValueError("invalid constraints")
        if len({c.name for c in self.constraints}) != len(self.constraints):
            raise ValueError("duplicate constraint")
        if type(self.eligible) is not bool or self.eligible != all(c.passed for c in self.constraints):
            raise ValueError("eligibility disagrees with constraints")

    @property
    def failed_constraints(self) -> tuple[ConstraintResult, ...]:
        return tuple(c for c in self.constraints if not c.passed)

    @property
    def passed_constraints(self) -> tuple[ConstraintResult, ...]:
        return tuple(c for c in self.constraints if c.passed)


class EligibilityEvaluator(Protocol):
    def check(self, task_id: str, context: SelfEvaluationContext,
              configuration: EligibilityConfiguration) -> EligibilityResult: ...


class HardEligibility:
    def check(self, task_id: str, context: SelfEvaluationContext,
              configuration: EligibilityConfiguration = EligibilityConfiguration()) -> EligibilityResult:
        if not isinstance(context, SelfEvaluationContext) or not isinstance(configuration, EligibilityConfiguration):
            raise ValueError("invalid eligibility inputs")
        task, agent = context.task(task_id).task, context.own_state
        continuation = task.status in {TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS} and task.owner_id == agent.identity.agent_id and agent.task_id == task_id
        distance = travel_ticks(context.topology, agent.position.location_id, task.location_id)
        required = float(Decimal(str(max(task.requirements.minimum_battery, configuration.minimum_battery)))
                         + Decimal(distance or 0) * Decimal(str(configuration.battery_per_travel_tick)))
        constraints = []
        def add(name, passed, detail):
            constraints.append(ConstraintResult(name, passed, detail))
        for category in ("sensing", "operational"):
            missing = getattr(task.requirements.capabilities, category) - getattr(agent.capabilities, category)
            add(category, not missing, f"Missing {category}: {', '.join(sorted(missing))}" if missing else f"All required {category} tags present")
        add("availability", agent.availability is Availability.AVAILABLE or (continuation and agent.availability is Availability.BUSY), f"Availability {agent.availability.value}")
        add("commitment", agent.task_id is None or continuation, f"Commitment {agent.task_id or 'none'}; same-task continuation only")
        add("lifecycle", task.status is TaskStatus.PENDING or continuation, f"Task {task.status.value}; owner {task.owner_id or 'none'}")
        add("reachability", distance is not None, f"Travel {distance} ticks" if distance is not None else "Unreachable through public directed topology")
        add("battery", agent.battery > 0 and agent.battery >= required, f"Battery {agent.battery:.3f}; required {required:.3f} (reserve + travel estimate)")
        resources = {r.name: r for r in agent.resources}
        for demand in task.requirements.resources:
            available = resources[demand.name].amount if demand.name in resources else 0
            consumed = next((r.amount for r in context.consumed_resources if r.name == demand.name), 0) if continuation else 0
            add("resource:" + demand.name, available + consumed >= demand.amount,
                f"{demand.name}: available {available:g}; already consumed for this task {consumed:g}; demand {demand.amount:g}")
        for rule in configuration.restrictions:
            # Latest per original source/modality; retain independent conflicting reports.
            latest = {}
            for entry in context.evidence:
                obs = entry.observation
                if obs.location_id == task.location_id and obs.subject == rule.subject:
                    key = obs.source_id, obs.sensing_capability
                    if key not in latest or (obs.observed_at, obs.observation_id) > (latest[key].observed_at, latest[key].observation_id):
                        latest[key] = obs
            usable = [o for o in latest.values() if context.tick - o.observed_at <= rule.max_age_ticks and o.confidence >= rule.minimum_confidence]
            prohibited = sorted(o.observation_id for o in usable if o.value in rule.prohibited_values)
            passed = not prohibited and (bool(usable) or not rule.require_known)
            add("environment:" + rule.subject, passed, f"{rule.subject}: prohibited evidence {prohibited}; usable reports {len(usable)}; require_known={rule.require_known}")
        failed = [c.explanation for c in constraints if not c.passed]
        return EligibilityResult(agent.identity.agent_id, task_id, not failed, context.tick, tuple(constraints), distance, required,
                                 "; ".join(failed) if failed else "All hard constraints passed")
