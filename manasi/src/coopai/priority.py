"""Explainable baseline priority from announced tasks and agent-local evidence only.

No simulation, world, renderer, transport or random-number imports.
"""
from dataclasses import dataclass, fields, replace
from math import isfinite
from typing import Protocol

from .knowledge import KnowledgeEntry
from .models import Task, TaskStatus, _id, _number, _tick


@dataclass(frozen=True)
class TaskAnnouncement:
    task: Task
    task_type: str
    announced_at: int
    version: int = 1
    initial_urgency: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.task, Task):
            raise ValueError("announcement requires a validated Task")
        _id(self.task_type)
        _tick(self.announced_at)
        _tick(self.version)
        if not self.version or self.task.created_at > self.announced_at:
            raise ValueError("positive version and creation before announcement required")
        if self.initial_urgency is None:
            object.__setattr__(self, "initial_urgency", self.task.urgency)
        _number(self.initial_urgency, high=1)


@dataclass(frozen=True)
class PriorityWeights:
    urgency: float = 0.25
    deadline: float = 0.20
    environment: float = 0.35
    importance: float = 0.15
    uncertainty: float = 0.05

    def __post_init__(self) -> None:
        for item in fields(self):
            _number(getattr(self, item.name))
        total = sum(getattr(self, f.name) for f in fields(self))
        if not isfinite(total) or total <= 0:
            raise ValueError("weight sum must be positive and finite")


@dataclass(frozen=True)
class TypePriorityRule:
    task_type: str
    subject: str
    importance: float
    severities: tuple[tuple[str, float], ...]

    def __post_init__(self) -> None:
        _id(self.task_type)
        _id(self.subject)
        _number(self.importance, high=1)
        if not isinstance(self.severities, tuple):
            raise ValueError("severity mappings must be a tuple")
        names = []
        for pair in self.severities:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError("severity mapping requires (value, severity)")
            _id(pair[0])
            _number(pair[1], high=1)
            names.append(pair[0])
        if len(set(names)) != len(names):
            raise ValueError("duplicate condition value")


@dataclass(frozen=True)
class PriorityConfiguration:
    weights: PriorityWeights = PriorityWeights()
    deadline_window_ticks: int = 8
    freshness_ticks: int = 4
    unknown_risk: float = 0.5
    default_importance: float = 0.5
    rules: tuple[TypePriorityRule, ...] = (
        TypePriorityRule("fire-verification", "fire", 0.9, (("present", 0.8), ("severe", 1), ("clear", 0), ("cleared", 0), ("absent", 0))),
        TypePriorityRule("gas-inspection", "gas", 0.65, (("present", 0.7), ("severe", 1), ("clear", 0), ("cleared", 0), ("absent", 0))),
        TypePriorityRule("person-search", "person", 1, (("possible", 0.9), ("present", 1), ("absent", 0))),
        TypePriorityRule("routine-inspection", "hazard", 0.15, (("clear", 0), ("present", 0.6), ("severe", 1))),
    )

    def __post_init__(self) -> None:
        if not isinstance(self.weights, PriorityWeights):
            raise ValueError("invalid priority weights")
        _tick(self.deadline_window_ticks)
        _tick(self.freshness_ticks)
        if not self.deadline_window_ticks or not self.freshness_ticks:
            raise ValueError("deadline/freshness windows must be positive")
        _number(self.unknown_risk, high=1)
        _number(self.default_importance, high=1)
        if not isinstance(self.rules, tuple) or any(not isinstance(r, TypePriorityRule) for r in self.rules):
            raise ValueError("rules must be a tuple of TypePriorityRule")
        if len({r.task_type for r in self.rules}) != len(self.rules):
            raise ValueError("duplicate task-type rule")


def configuration_from_dict(data: object) -> PriorityConfiguration:
    """Strict optional scenario JSON priority section; reuse model validation."""
    names = {f.name for f in fields(PriorityConfiguration)}
    if not isinstance(data, dict) or set(data) != names:
        raise ValueError(f"priority configuration requires fields {sorted(names)}")
    weights = data["weights"]
    if not isinstance(weights, dict) or set(weights) != {f.name for f in fields(PriorityWeights)}:
        raise ValueError("invalid weight fields")
    if not isinstance(data["rules"], list):
        raise ValueError("rules must be an array")
    rules = []
    for rule in data["rules"]:
        if not isinstance(rule, dict) or set(rule) != {"task_type", "subject", "importance", "severities"}:
            raise ValueError("invalid task-type rule fields")
        mapping = rule["severities"]
        if not isinstance(mapping, dict):
            raise ValueError("severities must be an object")
        for value in mapping:
            _id(value)
        rules.append(TypePriorityRule(rule["task_type"], rule["subject"], rule["importance"], tuple(sorted(mapping.items()))))
    return PriorityConfiguration(**{**data, "weights": PriorityWeights(**weights), "rules": tuple(rules)})


@dataclass(frozen=True)
class PriorityContext:
    agent_id: str
    tick: int
    known_tasks: tuple[TaskAnnouncement, ...]
    evidence: tuple[KnowledgeEntry, ...]
    configuration: PriorityConfiguration = PriorityConfiguration()

    def __post_init__(self) -> None:
        _id(self.agent_id)
        _tick(self.tick)
        if not isinstance(self.known_tasks, tuple) or any(not isinstance(a, TaskAnnouncement) for a in self.known_tasks):
            raise ValueError("invalid announced tasks")
        if len({a.task.task_id for a in self.known_tasks}) != len(self.known_tasks):
            raise ValueError("duplicate known task")
        if not isinstance(self.evidence, tuple) or any(not isinstance(e, KnowledgeEntry) for e in self.evidence):
            raise ValueError("invalid local evidence")
        if not isinstance(self.configuration, PriorityConfiguration):
            raise ValueError("invalid mission priority configuration")
        if any(a.announced_at > self.tick for a in self.known_tasks) or any(e.received_at > self.tick for e in self.evidence):
            raise ValueError("future announcement or evidence")
        for entry in self.evidence:
            if entry.is_remote:
                if not entry.path or entry.path[-1] != self.agent_id:
                    raise ValueError("evidence belongs to another recipient")
            elif entry.observation.source_id != self.agent_id:
                raise ValueError("direct evidence belongs to another agent")


@dataclass(frozen=True)
class FactorContribution:
    name: str
    value: float
    normalized_weight: float
    contribution: float

    def __post_init__(self) -> None:
        _id(self.name)
        for value in (self.value, self.normalized_weight, self.contribution):
            _number(value, high=1)


@dataclass(frozen=True)
class PriorityEvidence:
    entry: KnowledgeEntry
    age_ticks: int
    freshness: float
    effective_confidence: float
    disposition: str

    def __post_init__(self) -> None:
        if not isinstance(self.entry, KnowledgeEntry):
            raise ValueError("invalid evidence reference")
        _tick(self.age_ticks)
        _number(self.freshness, high=1)
        _number(self.effective_confidence, high=self.entry.observation.confidence)
        _id(self.disposition)


@dataclass(frozen=True)
class PriorityResult:
    agent_id: str
    task_id: str
    score: float
    evaluation_tick: int
    factors: tuple[FactorContribution, ...]
    evidence: tuple[PriorityEvidence, ...]
    evidence_status: str
    explanation: str

    def __post_init__(self) -> None:
        _id(self.agent_id)
        _id(self.task_id)
        _number(self.score, high=1)
        _tick(self.evaluation_tick)
        _id(self.evidence_status)
        _id(self.explanation)
        if not isinstance(self.factors, tuple) or any(not isinstance(f, FactorContribution) for f in self.factors):
            raise ValueError("invalid priority factors")
        if not isinstance(self.evidence, tuple) or any(not isinstance(e, PriorityEvidence) for e in self.evidence):
            raise ValueError("invalid evidence references")


class PriorityEvaluator(Protocol):
    def evaluate(self, task_id: str, context: PriorityContext) -> PriorityResult: ...


class WeightedPriority:
    def evaluate(self, task_id: str, context: PriorityContext) -> PriorityResult:
        if not isinstance(context, PriorityContext):
            raise ValueError("expected agent-local PriorityContext")
        announcement = next((a for a in context.known_tasks if a.task.task_id == task_id), None)
        if announcement is None:
            raise ValueError("task has not been announced to this agent")
        task, cfg = announcement.task, context.configuration
        rule = next((r for r in cfg.rules if r.task_type == announcement.task_type), None)
        importance = rule.importance if rule else cfg.default_importance
        mapping = dict(rule.severities) if rule else {}
        entries = [e for e in context.evidence if rule and e.observation.location_id == task.location_id and e.observation.subject == rule.subject]
        # Evidence IDs represent one sample even if supplied through multiple routes.
        unique = {}
        for entry in sorted(entries, key=lambda e: (e.received_at, e.observation.observation_id)):
            prior = unique.get(entry.observation.observation_id)
            if prior and prior.observation != entry.observation:
                raise ValueError("evidence ID has conflicting content")
            unique.setdefault(entry.observation.observation_id, entry)
        references, eligible = [], []
        for entry in unique.values():
            observation = entry.observation
            age = context.tick - observation.observed_at
            freshness = max(0.0, 1 - age / cfg.freshness_ticks)
            effective = observation.confidence * freshness
            disposition = "unmapped" if observation.value not in mapping else "stale" if freshness == 0 else "zero_confidence" if effective == 0 else "candidate"
            reference = PriorityEvidence(entry, age, freshness, effective, disposition)
            references.append(reference)
            if disposition == "candidate":
                eligible.append(reference)
        frontier = []
        for candidate in eligible:
            c = candidate.entry.observation.confidence
            age = candidate.age_ticks
            dominated = any(other.entry.observation.confidence >= c and other.age_ticks <= age and
                            (other.entry.observation.confidence > c or other.age_ticks < age) for other in eligible)
            if not dominated:
                frontier.append(candidate)
        risk, uncertainty, status = cfg.unknown_risk, 1.0, "unknown"
        if frontier:
            adjusted = lambda e: cfg.unknown_risk + e.effective_confidence * (mapping[e.entry.observation.value] - cfg.unknown_risk)
            # Conservative maximum on the confidence/freshness frontier, not a certainty claim.
            selected = max(frontier, key=lambda e: (adjusted(e), e.effective_confidence, e.entry.observation.observation_id))
            risk = adjusted(selected)
            uncertainty = 1 - selected.effective_confidence
            values = {mapping[e.entry.observation.value] for e in frontier}
            status = "conflict" if len(values) > 1 else "safe_evidence" if values == {0} else "evidence"
            ids = {e.entry.observation.observation_id for e in frontier}
            references = [PriorityEvidence(e.entry, e.age_ticks, e.freshness, e.effective_confidence,
                                           "selected" if e == selected else "frontier" if e.entry.observation.observation_id in ids else "dominated" if e.disposition == "candidate" else e.disposition)
                          for e in references]
        deadline = 0.0 if task.deadline is None else max(0.0, min(1.0, 1 - (task.deadline - context.tick) / cfg.deadline_window_ticks))
        values = {"urgency": task.urgency, "deadline": deadline, "environment": risk,
                  "importance": importance, "uncertainty": uncertainty}
        total = sum(getattr(cfg.weights, name) for name in values)
        contributions = tuple(FactorContribution(name, value, getattr(cfg.weights, name) / total,
                                                value * getattr(cfg.weights, name) / total) for name, value in values.items())
        terminal = task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
        score = 0.0 if terminal else max(0.0, min(1.0, sum(c.contribution for c in contributions)))
        explanation = (f"{'Terminal task; score zero. ' if terminal else ''}{status}: risk {risk:.3f}, uncertainty {uncertainty:.3f}; "
                       f"urgency {task.urgency:.2f}, deadline {deadline:.2f}, mission importance {importance:.2f}. "
                       "Confidence/freshness frontier; conservative maximum for conflicts.")
        return PriorityResult(context.agent_id, task_id, score, context.tick, contributions,
                              tuple(sorted(references, key=lambda e: e.entry.observation.observation_id)), status, explanation)


class AgentPriorityState:
    """One agent's explicit task awareness, mission settings, cache and result history."""
    def __init__(self, agent_id: str, configuration: PriorityConfiguration, evaluator: PriorityEvaluator | None = None):
        _id(agent_id)
        if not isinstance(configuration, PriorityConfiguration):
            raise ValueError("invalid priority configuration")
        self.agent_id = agent_id
        self.configuration = configuration
        self.evaluator = evaluator or WeightedPriority()
        if not callable(getattr(self.evaluator, "evaluate", None)):
            raise ValueError("priority evaluator requires an evaluate method")
        self._tasks: dict[str, TaskAnnouncement] = {}
        self._baseline = None
        self._clear_results()

    def _clear_results(self):
        self._signature = None
        self._results: tuple[PriorityResult, ...] = ()
        self._history: list[tuple[PriorityResult, ...]] = []

    def announce(self, announcement: TaskAnnouncement, now: int) -> bool:
        _tick(now)
        if not isinstance(announcement, TaskAnnouncement) or announcement.announced_at > now:
            raise ValueError("invalid or future task announcement")
        prior = self._tasks.get(announcement.task.task_id)
        if prior:
            announcement = replace(announcement, initial_urgency=prior.initial_urgency)
        if prior and announcement.version <= prior.version:
            if announcement.version == prior.version and announcement != prior:
                raise ValueError("announcement version reused with changed content")
            return False
        if prior and announcement.announced_at < prior.announced_at:
            raise ValueError("new announcement version has a regressing publication tick")
        self._tasks[announcement.task.task_id] = announcement
        return True

    def known_tasks(self) -> tuple[TaskAnnouncement, ...]:
        return tuple(self._tasks[k] for k in sorted(self._tasks))

    def start(self):
        self._baseline = (dict(self._tasks), self.configuration)

    def reset(self):
        if self._baseline is not None:
            tasks, self.configuration = self._baseline
            self._tasks = dict(tasks)
        self._clear_results()

    def update(self, tick: int, evidence: tuple[KnowledgeEntry, ...]) -> None:
        tasks = self.known_tasks()
        context = PriorityContext(self.agent_id, tick, tasks, evidence, self.configuration)
        # Per-task temporal signatures avoid evaluation when clamped deadline/freshness is stable.
        rules = {r.task_type: r for r in self.configuration.rules}
        relevant = tuple(e for e in evidence if any(a.task.location_id == e.observation.location_id and
                         (r := rules.get(a.task_type)) and r.subject == e.observation.subject for a in tasks))
        temporal = tuple(max(0, min(self.configuration.freshness_ticks, tick - e.observation.observed_at)) for e in relevant)
        deadlines = tuple(None if a.task.deadline is None else max(0, min(self.configuration.deadline_window_ticks, a.task.deadline - tick)) for a in tasks)
        signature = tasks, self.configuration, relevant, temporal, deadlines
        if signature == self._signature:
            return
        results = tuple(self.evaluator.evaluate(a.task.task_id, context) for a in tasks)
        if any(not isinstance(r, PriorityResult) or r.agent_id != self.agent_id or
               r.task_id != a.task.task_id or r.evaluation_tick != tick for a, r in zip(tasks, results)):
            raise ValueError("priority evaluator returned invalid identity or evaluation tick")
        self._results = tuple(sorted(results, key=lambda r: (-r.score, r.task_id)))
        self._signature = signature
        self._history.append(self._results)

    @property
    def results(self) -> tuple[PriorityResult, ...]:
        return self._results

    @property
    def history(self) -> tuple[tuple[PriorityResult, ...], ...]:
        return tuple(self._history)
