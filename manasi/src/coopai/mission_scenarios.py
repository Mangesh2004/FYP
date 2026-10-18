"""Validated data-driven review missions. Expectations never enter agent decisions."""
from dataclasses import dataclass
import json
from pathlib import Path
from types import MappingProxyType

from .communication import CommunicationConfiguration, Disconnection
from .execution_models import scenario_execution_specs
from .models import _id, _tick
from .perception import PerceptionConfiguration
from .scenario import (Scenario, ScenarioError, TaskDiscovery, _build, _object,
                       _unique_object, _reject_constant, scenario_from_dict)
from .models import _json_float

SCENARIO_DIRECTORY = Path(__file__).resolve().parents[2] / "scenarios" / "missions"
SCENARIO_FILES = MappingProxyType({name: SCENARIO_DIRECTORY / f"{name}.json" for name in
    ("corridor", "distributed", "emergency", "failure-disruption", "constrained")})


@dataclass(frozen=True)
class ExpectedBehaviors:
    completed_objectives: int
    incomplete_objectives: int
    minimum_moving_agents: int
    requires_recovery: bool

    def __post_init__(self):
        for value in (self.completed_objectives, self.incomplete_objectives, self.minimum_moving_agents):
            _tick(value)
        if type(self.requires_recovery) is not bool:
            raise ValueError("requires_recovery must be boolean")


@dataclass(frozen=True)
class MissionScenario:
    scenario_id: str
    display_name: str
    description: str
    demonstration_objective: str
    scenario: Scenario
    communication: CommunicationConfiguration
    expected: ExpectedBehaviors

    def __post_init__(self):
        for value in (self.scenario_id, self.display_name, self.description, self.demonstration_objective):
            _id(value)
        if not isinstance(self.scenario, Scenario) or not isinstance(self.communication, CommunicationConfiguration) or not isinstance(self.expected, ExpectedBehaviors):
            raise ValueError("invalid mission scenario configuration")
        initial = self.scenario.initial_world
        agents = {a.identity.agent_id for a in initial.agents}
        discoveries = [e for e in self.scenario.events if isinstance(e, TaskDiscovery)]
        total = len(initial.tasks) + len(discoveries)
        if self.expected.completed_objectives + self.expected.incomplete_objectives != total or self.expected.minimum_moving_agents > len(agents):
            raise ValueError("expected behavior counts disagree with configured mission")
        if self.communication.links is not None and any(a not in agents or b not in agents for a,b in self.communication.links):
            raise ValueError("unknown communication link endpoint")
        if any(d.agent_id not in agents or d.end_tick > self.scenario.mission.duration_ticks for d in self.communication.disconnections):
            raise ValueError("unknown disconnection agent or interval outside horizon")
        sensing = frozenset(r.sensing_capability for r in PerceptionConfiguration().rules)
        operational = frozenset({"exploration", "simulated-assistance"})
        caps = [a.capabilities for a in initial.agents] + [t.requirements.capabilities for t in initial.tasks] + [e.task.requirements.capabilities for e in discoveries]
        if any(not c.sensing <= sensing or not c.operational <= operational for c in caps):
            raise ValueError("unknown capability in autonomous mission vocabulary")
        tasks = {t.task_id: t for t in (*initial.tasks, *(e.task for e in discoveries))}
        details = (*self.scenario.task_details, *(e.details for e in discoveries))
        for spec in scenario_execution_specs(details):
            requirements = tasks[spec.task_id].requirements.capabilities
            if spec.sensing_capability and spec.sensing_capability not in requirements.sensing:
                raise ValueError("task requirements omit execution sensing method")
            if "exploration" not in requirements.operational:
                raise ValueError("inspection requirements omit exploration operation")

    def create_engine(self):
        return self.scenario.create_engine(with_recovery=True, communication_configuration=self.communication)


def mission_scenario_from_dict(data: object) -> MissionScenario:
    if not isinstance(data, dict):
        raise ScenarioError("mission scenario must be an object")
    metadata = _object(data.get("demonstration"), "demonstration",
        {"id", "name", "description", "objective", "expected"})
    expected = _object(metadata["expected"], "demonstration.expected", set(ExpectedBehaviors.__dataclass_fields__))
    settings = _object(data.get("communication"), "communication", set(CommunicationConfiguration.__dataclass_fields__))
    if not isinstance(settings["disconnections"], list):
        raise ScenarioError("communication.disconnections must be an array")
    interruptions = []
    for item in settings["disconnections"]:
        _object(item, "communication.disconnection", set(Disconnection.__dataclass_fields__))
        interruptions.append(_build("communication.disconnection", lambda: Disconnection(**item)))
    links = settings["links"]
    if links is not None:
        if not isinstance(links, list) or any(not isinstance(pair, list) or len(pair) != 2 for pair in links):
            raise ScenarioError("communication.links must be null or pairs of IDs")
        links = tuple(tuple(pair) for pair in links)
    communication = _build("communication", lambda: CommunicationConfiguration(**{**settings, "links": links, "disconnections": tuple(interruptions)}))
    scenario = scenario_from_dict({k:v for k,v in data.items() if k not in {"demonstration", "communication"}})
    return _build("demonstration", lambda: MissionScenario(metadata["id"], metadata["name"], metadata["description"],
        metadata["objective"], scenario, communication, ExpectedBehaviors(**expected)))


def load_mission_scenario(path: str | Path) -> MissionScenario:
    try:
        with Path(path).open(encoding="utf-8") as handle:
            data = json.load(handle, object_pairs_hook=_unique_object, parse_float=_json_float, parse_constant=_reject_constant)
        return mission_scenario_from_dict(data)
    except (OSError, ValueError, TypeError) as exc:
        raise ScenarioError(f"{path}: {exc}") from exc


def get_scenario(scenario_id: str) -> MissionScenario:
    if scenario_id not in SCENARIO_FILES:
        raise ScenarioError(f"unknown scenario {scenario_id!r}; choose {', '.join(SCENARIO_FILES)}")
    result = load_mission_scenario(SCENARIO_FILES[scenario_id])
    if result.scenario_id != scenario_id:
        raise ScenarioError("registry ID and configured scenario ID disagree")
    return result


def list_scenarios() -> tuple[MissionScenario, ...]:
    return tuple(get_scenario(name) for name in SCENARIO_FILES)
