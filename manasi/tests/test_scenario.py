from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path

import pytest

from coopai.geometry import Rectangle, Segment
from coopai.models import Availability, TaskStatus
from coopai.scenario import ScenarioError, load_scenario, scenario_from_dict
from coopai.simulation import Phase


SCENARIO_PATH = Path(__file__).resolve().parents[1] / "scenarios/four_room_disaster.json"


@pytest.fixture
def config():
    return json.loads(SCENARIO_PATH.read_text())


def test_load_layout_connectivity_and_walls():
    scenario = load_scenario(SCENARIO_PATH)
    plan = scenario.floor_plan
    assert len([a for a in plan.areas if a.area_type == "room"]) == 4
    assert len(plan.areas) == 5 and len(plan.passages) == 5
    assert len(scenario.initial_world.environment.connections) == 8
    assert [p.passage_id for p in plan.passages if p.target is None] == ["entry"]
    for area in plan.areas:
        r = area.bounds
        cx, cy = r.center
        assert r.x < cx < r.x + r.width and r.y < cy < r.y + r.height
    # Topological reachability through the corridor includes all four rooms.
    reached, pending = set(), ["corridor"]
    while pending:
        node = pending.pop()
        if node in reached:
            continue
        reached.add(node)
        pending.extend(e.target for e in scenario.initial_world.environment.connections if e.source == node)
    assert reached == {a.location.location_id for a in plan.areas}
    assert plan.walls() == plan.walls()
    for p in plan.passages:
        door = p.opening
        for wall in plan.walls():
            if door.y1 == door.y2 == wall.y1 == wall.y2:
                assert max(door.x1, wall.x1) >= min(door.x2, wall.x2)
            if door.x1 == door.x2 == wall.x1 == wall.x2:
                assert max(door.y1, wall.y1) >= min(door.y2, wall.y2)


def test_agents_tasks_and_coverage():
    scenario = load_scenario(SCENARIO_PATH)
    agents = {a.identity.agent_id: a for a in scenario.initial_world.agents}
    assert set(agents) == {"A", "B", "C", "D"}
    assert agents["A"].capabilities.sensing == frozenset({"thermal"})
    assert agents["B"].capabilities.sensing == frozenset({"gas"})
    assert agents["C"].capabilities.sensing == frozenset({"thermal", "visual"})
    assert agents["D"].capabilities.sensing == frozenset()
    assert "simulated-assistance" in agents["D"].capabilities.operational
    assert all(a.availability is Availability.AVAILABLE and a.task_id is None and a.updated_at == 0 for a in agents.values())
    assert all(0 < a.battery <= 1 for a in agents.values())
    assert agents["D"].resources[0].amount == 2
    tasks = scenario.initial_world.tasks
    assert len(tasks) == 4 and len(scenario.task_details) == 4
    assert [t.urgency for t in tasks] == [0.9, 0.5, 0.9, 0.1]
    assert all(t.status is TaskStatus.PENDING and t.owner_id is None for t in tasks)
    assert all(any(a.capabilities.meets(t.requirements.capabilities) for a in agents.values()) for t in tasks)
    assert all("firefighting" not in a.capabilities.operational for a in agents.values())
    assert {c.subject: c.value for c in scenario.initial_world.conditions} == {"fire": "present", "gas": "present", "person": "possible", "hazard": "clear"}


@pytest.mark.parametrize("section", ["locations", "passages", "agents", "tasks", "events", "conditions"])
def test_duplicate_identifiers_or_condition_keys(config, section):
    config[section].append(deepcopy(config[section][0]))
    with pytest.raises(ScenarioError, match="duplicate"):
        scenario_from_dict(config)


@pytest.mark.parametrize("section,field,value,match", [
    ("agents", "location", "missing", "unknown agent location"),
    ("tasks", "location", "missing", "unknown task"),
    ("conditions", "location_id", "missing", "unknown task or condition"),
    ("passages", "source", "missing", "unknown location"),
    ("passages", "target", "missing", "unknown location"),
    ("passages", "target", "room-1", "endpoints must differ"),
    ("passages", "travel_ticks", 0, "positive"),
    ("passages", "travel_ticks", True, "integer"),
    ("agents", "battery", -0.1, "finite number"),
    ("agents", "battery", True, "finite number"),
    ("agents", "availability", "unknown", "Availability"),
    ("agents", "availability", "busy", "commitment"),
    ("tasks", "urgency", 1.1, "finite number"),
    ("tasks", "deadline", -1, "integer"),
    ("tasks", "status", "assigned", "owner"),
    ("tasks", "status", "cancelled", "pending"),
    ("tasks", "duration_ticks", 0, "positive"),
    ("events", "target", "missing", "unknown target"),
    ("events", "tick", 16, "horizon"),
    ("events", "tick", -1, "integer"),
    ("events", "action", "allocate", "unsupported action"),
])
def test_invalid_values_references_and_connections(config, section, field, value, match):
    config[section][0][field] = value
    with pytest.raises(ScenarioError, match=match):
        scenario_from_dict(config)


@pytest.mark.parametrize("field,value", [("seed", True), ("duration_ticks", 0), ("tick_seconds", 0), ("tick_seconds", float("nan")), ("objectives", "inspect")])
def test_invalid_mission(config, field, value):
    config["mission"][field] = value
    with pytest.raises(ScenarioError, match="mission"):
        scenario_from_dict(config)


@pytest.mark.parametrize("mutation,match", [
    (lambda c: c.pop("mission"), "missing fields"),
    (lambda c: c.update(typo=1), "unknown fields"),
    (lambda c: c.update(agents={}), "expected array"),
    (lambda c: c.update(schema_version=True), "schema_version"),
    (lambda c: c["tasks"][0].update(requirements=[]), "expected object"),
    (lambda c: c["agents"][0]["capabilities"].update(sensing="thermal"), "expected array"),
    (lambda c: c["agents"][0]["capabilities"].update(sensing=["thermal", "thermal"]), "duplicate capability"),
    (lambda c: c["agents"][0]["capabilities"].update(sensing=[{}]), "nonempty string"),
    (lambda c: c["tasks"][0]["requirements"]["capabilities"].update(sensing=["firefighting"]), "no initial agent"),
    (lambda c: c["tasks"][0]["requirements"].update(resources=[dict(name="water",amount=1,capacity=1)]), "no initial agent"),
    (lambda c: c["locations"][0]["bounds"].update(width=-1), "finite number"),
    (lambda c: c["locations"][0]["bounds"].update(width=True), "finite number"),
    (lambda c: c["locations"][1]["bounds"].update(x=1), "overlap"),
    (lambda c: c["passages"][0]["opening"].update(y1=3,y2=3), "boundary"),
    (lambda c: c["passages"][0]["opening"].update(x2=20), "boundary"),
    (lambda c: c["passages"][0]["opening"].update(y2=5), "horizontal/vertical"),
    (lambda c: c["passages"].pop(0), "reachable"),
    (lambda c: c["passages"].pop(), "entry"),
    (lambda c: c["events"][1].update(battery=2), "finite number"),
    (lambda c: c["events"][1].update(target="room-1"), "unknown target"),
])
def test_invalid_structure_geometry_and_coverage(config, mutation, match):
    mutation(config)
    with pytest.raises(ScenarioError, match=match):
        scenario_from_dict(config)


@pytest.mark.parametrize("text", ['{', '[]', '{"schema_version":1,"schema_version":1}', '{"x":NaN}', '{"x":1e999}'])
def test_bad_json(tmp_path, text):
    path = tmp_path / "bad.json"
    path.write_text(text)
    with pytest.raises(ScenarioError, match="bad.json"):
        load_scenario(path)


def test_missing_file(tmp_path):
    with pytest.raises(ScenarioError, match="missing.json"):
        load_scenario(tmp_path / "missing.json")


def test_initialization_is_deterministic_immutable_and_independent(config):
    original = deepcopy(config)
    first = scenario_from_dict(config)
    assert first == load_scenario(SCENARIO_PATH)
    assert config == original
    config["agents"][0]["battery"] = 0
    assert first.initial_world.agents[0].battery == 1
    with pytest.raises(FrozenInstanceError):
        first.floor_plan.areas = ()
    sim = first.create_engine()
    other = first.create_engine()
    sim.run(6)
    assert other.world == first.initial_world and other.time.tick == 0


def test_events_timing_reset_and_seeded_trace():
    scenario = load_scenario(SCENARIO_PATH)
    sim = scenario.create_engine()
    draws = []
    sim.register_system("draw", Phase.RECORD, lambda s: draws.append(s.random_stream("test").random()))
    sim.run(3)
    assert sim.world.conditions == scenario.initial_world.conditions
    sim.run(1)
    assert next(c.value for c in sim.world.conditions if c.subject == "fire") == "severe"
    assert next(a.battery for a in sim.world.agents if a.identity.agent_id == "B") == 0.9
    sim.run(2)
    assert next(a.battery for a in sim.world.agents if a.identity.agent_id == "B") == 0.4
    assert next(a.updated_at for a in sim.world.agents if a.identity.agent_id == "B") == 5
    result = (sim.world, sim.event_trace, tuple(draws))
    sim.reset()
    assert sim.world == scenario.initial_world
    draws.clear()
    sim.run(6)
    assert (sim.world, sim.event_trace, tuple(draws)) == result
    assert [r.tick for r in sim.event_trace] == [3, 5]
    assert all(t.owner_id is None for t in sim.world.tasks)


def test_new_condition_supported_without_observation_leak(config):
    config["events"][0].update(target="room-4", subject="gas", value="present")
    scenario = scenario_from_dict(config)
    sim = scenario.create_engine()
    views = []
    for agent in sim.world.agents:
        sim.register_controller(agent.identity.agent_id, lambda v: views.append(v) or ())
    sim.run(6)
    assert any(c.location_id == "room-4" and c.subject == "gas" for c in sim.world.conditions)
    assert all(v.observations == () for v in views)
    assert all(not hasattr(v, name) for v in views for name in ("world", "tasks", "conditions", "agents", "scenario", "events", "floor_plan"))
    assert {v.own_state.identity.agent_id for v in views} == {"A", "B", "C", "D"}


@pytest.mark.parametrize("args", [(0,0,0,1), (0,0,float("inf"),1)])
def test_rectangle_bounds(args):
    with pytest.raises(ValueError):
        Rectangle(*args)


@pytest.mark.parametrize("args", [(0,0,0,0), (0,0,1,1), (1,0,0,0)])
def test_segment_invariants(args):
    with pytest.raises(ValueError):
        Segment(*args)


def test_demo_reports_actual_state_and_replay(capsys):
    from coopai.scenario_demo import run_demo
    run_demo(load_scenario(SCENARIO_PATH))
    output = capsys.readouterr().out
    assert "directed_connections=8" in output
    assert "event tick=3 id=fire-escalates" in output
    assert "fire=severe B_battery=0.4" in output
    assert "replay_equal=True seed=42 processed_ticks=8" in output


def test_nonstring_config_fields_are_descriptive(config):
    config[1] = "invalid"
    with pytest.raises(ScenarioError, match="field names must be strings"):
        scenario_from_dict(config)


def test_duplicate_connections_and_overlapping_openings(config):
    duplicate = deepcopy(config["passages"][0])
    duplicate.update(id="another-door", opening=dict(x1=2.5,y1=4,x2=3.5,y2=4))
    config["passages"].append(duplicate)
    with pytest.raises(ScenarioError, match="duplicate directed connection"):
        scenario_from_dict(config)
    duplicate["opening"] = dict(x1=1.5,y1=4,x2=2.5,y2=4)
    with pytest.raises(ScenarioError, match="openings overlap"):
        scenario_from_dict(config)


def test_exterior_entry_cannot_overlap_another_area_even_partially(config):
    config["passages"][-1].update(opening=dict(x1=3,y1=4,x2=5,y2=4))
    with pytest.raises(ScenarioError, match="exterior entry"):
        scenario_from_dict(config)


def test_same_tick_scripted_events_follow_engine_id_order(config):
    config["events"][1]["tick"] = 3
    scenario = scenario_from_dict(config)
    sim = scenario.create_engine()
    sim.run(4)
    assert [r.event.event_id for r in sim.event_trace] == ["battery-drop", "fire-escalates"]
    assert next(a.battery for a in sim.world.agents if a.identity.agent_id == "B") == 0.4
