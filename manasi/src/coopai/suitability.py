"""Explainable self-suitability after a separate hard feasibility gate."""
from dataclasses import dataclass
from math import isfinite
from typing import Protocol

from .eligibility import EligibilityConfiguration, EligibilityEvaluator, EligibilityResult, HardEligibility, SelfEvaluationContext
from .knowledge import KnowledgeEntry
from .models import Environment, _id, _number, _tick
from .priority import FactorContribution


@dataclass(frozen=True)
class SuitabilityConfiguration:
    battery_weight: float = 0.45
    travel_weight: float = 0.35
    information_weight: float = 0.20
    resource_weight: float = 0.20
    travel_scale_ticks: int = 4
    freshness_ticks: int = 4
    # Public mission vocabulary: relevant subject per task type, never truth.
    task_subjects: tuple[tuple[str, str], ...] = (
        ("fire-verification", "fire"), ("gas-inspection", "gas"),
        ("person-search", "person"), ("routine-inspection", "hazard"))

    def __post_init__(self):
        weights = (self.battery_weight, self.travel_weight, self.information_weight, self.resource_weight)
        for weight in weights:
            _number(weight)
        # Resource factor may be absent, so the always-active weights must sum > 0.
        if not isfinite(sum(weights)) or sum(weights[:3]) <= 0:
            raise ValueError("active suitability weights must have positive finite sum")
        for value in (self.travel_scale_ticks, self.freshness_ticks):
            _tick(value)
            if not value:
                raise ValueError("suitability scales must be positive")
        if not isinstance(self.task_subjects, tuple):
            raise ValueError("task subjects must be a tuple")
        for pair in self.task_subjects:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError("task subject requires a pair")
            for value in pair:
                _id(value)
        if len({p[0] for p in self.task_subjects}) != len(self.task_subjects):
            raise ValueError("duplicate task type")


@dataclass(frozen=True)
class SuitabilityResult:
    agent_id: str
    task_id: str
    evaluation_tick: int
    eligibility: EligibilityResult
    score: float | None
    factors: tuple[FactorContribution, ...]
    evidence: tuple[KnowledgeEntry, ...]
    explanation: str

    def __post_init__(self):
        _id(self.agent_id)
        _id(self.task_id)
        _id(self.explanation)
        _tick(self.evaluation_tick)
        gate = self.eligibility
        if not isinstance(gate, EligibilityResult) or (gate.agent_id, gate.task_id, gate.evaluation_tick) != (self.agent_id, self.task_id, self.evaluation_tick):
            raise ValueError("eligibility identity/tick mismatch")
        if not isinstance(self.factors, tuple) or any(not isinstance(f, FactorContribution) for f in self.factors):
            raise ValueError("invalid suitability factors")
        if not isinstance(self.evidence, tuple) or any(not isinstance(e, KnowledgeEntry) for e in self.evidence):
            raise ValueError("invalid evidence references")
        if gate.eligible:
            _number(self.score, high=1)
            if not self.factors or abs(sum(f.normalized_weight for f in self.factors) - 1) > 1e-9 or abs(sum(f.contribution for f in self.factors) - self.score) > 1e-9:
                raise ValueError("inconsistent weighted suitability result")
        elif self.score is not None or self.factors or self.evidence:
            raise ValueError("ineligible task must have no suitability score/factors")

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(e.observation.observation_id for e in self.evidence)


class SuitabilityEvaluator(Protocol):
    def evaluate(self, task_id: str, context: SelfEvaluationContext,
                 eligibility_configuration: EligibilityConfiguration,
                 configuration: SuitabilityConfiguration) -> SuitabilityResult: ...


class WeightedSuitability:
    def __init__(self, eligibility_evaluator: EligibilityEvaluator | None = None):
        self.eligibility_evaluator = eligibility_evaluator or HardEligibility()
        if not callable(getattr(self.eligibility_evaluator, "check", None)):
            raise ValueError("eligibility evaluator requires check")

    def evaluate(self, task_id: str, context: SelfEvaluationContext,
                 eligibility_configuration: EligibilityConfiguration = EligibilityConfiguration(),
                 configuration: SuitabilityConfiguration = SuitabilityConfiguration()) -> SuitabilityResult:
        if not isinstance(configuration, SuitabilityConfiguration):
            raise ValueError("invalid suitability configuration")
        # Recheck rather than accepting a stale/forged eligible flag from callers.
        gate = self.eligibility_evaluator.check(task_id, context, eligibility_configuration)
        if not isinstance(gate, EligibilityResult) or (gate.agent_id, gate.task_id, gate.evaluation_tick) != (context.own_state.identity.agent_id, task_id, context.tick):
            raise ValueError("eligibility evaluator returned invalid identity/tick")
        if not gate.eligible:
            return SuitabilityResult(gate.agent_id, task_id, context.tick, gate, None, (), (), "Not scored: " + gate.explanation)
        announcement, agent = context.task(task_id), context.own_state
        subject = dict(configuration.task_subjects).get(announcement.task_type)
        evidence = [e for e in context.evidence if e.observation.location_id == announcement.task.location_id and e.observation.subject == subject]
        quality = lambda e: e.observation.confidence * max(0, 1 - (context.tick - e.observation.observed_at) / configuration.freshness_ticks)
        selected = max(evidence, key=lambda e: (quality(e), e.observation.observed_at, e.observation.observation_id), default=None)
        information = quality(selected) if selected else 0
        values = {
            "battery": (agent.battery - gate.required_battery) / (1 - gate.required_battery) if gate.required_battery < 1 else 0,
            "travel": max(0, 1 - gate.travel_ticks / configuration.travel_scale_ticks),
            "information": information,
        }
        demands = announcement.task.requirements.resources
        if any(d.amount > 0 for d in demands):
            resources = {r.name: r for r in agent.resources}
            consumed = {r.name: r.amount for r in context.consumed_resources} if agent.task_id == task_id else {}
            values["resource"] = min((resources[d.name].amount - max(0, d.amount - consumed.get(d.name, 0))) / resources[d.name].capacity for d in demands if d.amount > 0)
        total = sum(getattr(configuration, name + "_weight") for name in values)
        factors = tuple(FactorContribution(name, max(0, min(1, value)), getattr(configuration, name + "_weight") / total,
                                          max(0, min(1, value)) * getattr(configuration, name + "_weight") / total) for name, value in values.items())
        score = min(1, max(0, sum(f.contribution for f in factors)))
        references = (selected,) if selected and information > 0 else ()
        explanation = f"Travel {gate.travel_ticks} ticks; battery reserve {values['battery']:.3f}; information quality {information:.3f}. " + ("Unknown/stale information contributes zero. " if not references else "Original evidence timestamp retained. ") + "Priority is separate; no assignment chosen."
        return SuitabilityResult(gate.agent_id, task_id, context.tick, gate, score, factors, references, explanation)


class AgentSuitabilityState:
    """Independent owner state; recompute each tick after own inputs are updated.

    No cache: few tasks and agents; explicit evaluation timestamps aid inspection.
    """
    def __init__(self, agent_id: str, topology: Environment,
                 eligibility_configuration: EligibilityConfiguration = EligibilityConfiguration(),
                 configuration: SuitabilityConfiguration = SuitabilityConfiguration(),
                 evaluator: SuitabilityEvaluator | None = None):
        _id(agent_id)
        if not isinstance(topology, Environment) or not isinstance(eligibility_configuration, EligibilityConfiguration) or not isinstance(configuration, SuitabilityConfiguration):
            raise ValueError("invalid self-evaluation setup")
        self.agent_id = agent_id
        self.topology = topology
        self.eligibility_configuration = eligibility_configuration
        self.configuration = configuration
        self.evaluator = evaluator or WeightedSuitability()
        if not callable(getattr(self.evaluator, "evaluate", None)):
            raise ValueError("evaluator requires evaluate")
        self.results: tuple[SuitabilityResult, ...] = ()
        self._history: list[tuple[SuitabilityResult, ...]] = []
        self._baseline = None

    def update(self, context: SelfEvaluationContext) -> None:
        if context.own_state.identity.agent_id != self.agent_id or context.topology != self.topology:
            raise ValueError("context owner/topology mismatch")
        results = tuple(self.evaluator.evaluate(a.task.task_id, context, self.eligibility_configuration, self.configuration)
                        for a in sorted(context.known_tasks, key=lambda a: a.task.task_id))
        if any(not isinstance(r, SuitabilityResult) or r.agent_id != self.agent_id or r.task_id != a.task.task_id or r.evaluation_tick != context.tick
               for a, r in zip(sorted(context.known_tasks, key=lambda a: a.task.task_id), results)):
            raise ValueError("evaluator returned invalid identity/tick")
        self.results = results
        self._history.append(results)

    @property
    def history(self) -> tuple[tuple[SuitabilityResult, ...], ...]:
        return tuple(self._history)

    def start(self) -> None:
        self._baseline = self.eligibility_configuration, self.configuration

    def reset(self) -> None:
        if self._baseline is not None:
            self.eligibility_configuration, self.configuration = self._baseline
        self.results = ()
        self._history.clear()
