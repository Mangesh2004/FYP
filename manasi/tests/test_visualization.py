from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sys
import subprocess

import pytest

from coopai.geometry import FloorPlan, Rectangle
from coopai.scenario import load_scenario
from coopai.simulation import RunState
from coopai.visualization import PlaybackController, ScreenTransform, capture_dashboard


CONFIG = Path(__file__).resolve().parents[1] / "scenarios/four_room_disaster.json"


@pytest.fixture
def scenario():
    return load_scenario(CONFIG)


def test_geometry_mapping_preserves_scale_and_door_gaps(scenario):
    transform = ScreenTransform.fit(scenario.floor_plan, Rectangle(10, 20, 600, 400), padding=20)
    assert transform.scale == 36
    for area in scenario.floor_plan.areas:
        x, y, w, h = transform.rectangle(area.bounds)
        assert 30 <= x and 40 <= y
        assert x + w <= 590 and y + h <= 400
        assert w == round(area.bounds.width * transform.scale)
    door = scenario.floor_plan.passages[0].opening
    start, end = transform.segment(door)
    assert end[0] - start[0] == 36 and end[1] == start[1]


def test_negative_geometry_origin_fits_same_way(scenario):
    translated = FloorPlan(tuple(replace(a, bounds=replace(a.bounds, x=a.bounds.x-10, y=a.bounds.y-5)) for a in scenario.floor_plan.areas),
                           tuple(replace(p, opening=replace(p.opening, x1=p.opening.x1-10, x2=p.opening.x2-10, y1=p.opening.y1-5, y2=p.opening.y2-5)) for p in scenario.floor_plan.passages))
    viewport = Rectangle(0, 0, 500, 500)
    first = ScreenTransform.fit(scenario.floor_plan, viewport)
    second = ScreenTransform.fit(translated, viewport)
    assert first.point(0, 0) == second.point(-10, -5)
    assert first.scale == second.scale


@pytest.mark.parametrize("padding", [-1, 250, float("nan"), True])
def test_invalid_viewport_padding(scenario, padding):
    with pytest.raises(ValueError):
        ScreenTransform.fit(scenario.floor_plan, Rectangle(0, 0, 500, 500), padding)


def test_snapshot_accuracy_and_no_mutation(scenario):
    engine = scenario.create_engine()
    original = engine.world
    dashboard = capture_dashboard(scenario, engine)
    assert engine.world is original and engine.time.tick == 0 and engine.event_trace == ()
    assert [a.state for a in dashboard.agents] == list(original.agents)
    assert dashboard.agents[1].battery_text == "90%"
    assert dashboard.agents[2].sensing_text == "thermal, visual"
    assert dashboard.agents[3].operational_text == "exploration, simulated-assistance"
    assert len(dashboard.areas) == 5 and len(dashboard.tasks) == 4
    assert all(t.owner_text == "unassigned" for t in dashboard.tasks)
    assert dashboard.tasks[0].requirement_text == "Sense: thermal | Ops: exploration"
    assert [t.initial_urgency for t in dashboard.tasks] == [0.9, 0.5, 0.9, 0.1]
    assert [t.task.status.value for t in dashboard.tasks] == ["pending"] * 4
    with pytest.raises(FrozenInstanceError):
        dashboard.events = ()
    engine.run(6)
    later = capture_dashboard(scenario, engine)
    assert later.time == engine.time and later.events == engine.event_trace
    assert later.agents[1].battery_text == "40%"
    assert next(c.value for c in later.conditions if c.subject == "fire") == "severe"
    assert [a.marker for a in later.agents] == [a.marker for a in dashboard.agents]
    assert dashboard.agents[1].battery_text == "90%"  # Earlier snapshot stays unchanged.


def test_markers_are_inside_actual_locations_and_collocated_agents_separate(scenario):
    engine = scenario.create_engine()
    engine.update_agent(replace(engine.world.agents[1], position=engine.world.agents[0].position))
    snapshot = capture_dashboard(scenario, engine)
    assert snapshot.agents[0].marker != snapshot.agents[1].marker
    areas = {a.location.location_id: a.bounds for a in scenario.floor_plan.areas}
    for display, location in [(a, a.state.position.location_id) for a in snapshot.agents] + [(t, t.task.location_id) for t in snapshot.tasks]:
        x, y = display.marker
        rect = areas[location]
        assert rect.x < x < rect.x + rect.width and rect.y < y < rect.y + rect.height


def test_play_pause_step_reset_and_clock(scenario):
    engine = scenario.create_engine()
    controls = PlaybackController(engine)
    assert controls.advance(100) == 0 and engine.state is RunState.READY
    controls.play()
    assert controls.advance(0.49) == 0
    assert controls.advance(0.01) == 1 and engine.time.seconds == 0.5
    controls.pause()
    assert controls.advance(100) == 0 and engine.time.tick == 1
    controls.single_step()
    assert engine.time.tick == 2 and engine.state is RunState.PAUSED
    controls.play()
    controls.advance(2)
    assert engine.time.tick == 6
    controls.reset()
    assert engine.time.tick == 0 and engine.state is RunState.READY
    assert engine.world == scenario.initial_world and engine.event_trace == ()
    assert controls.advance(100) == 0


def test_single_step_from_ready_running_and_at_horizon(scenario):
    engine = scenario.create_engine()
    controls = PlaybackController(engine)
    controls.single_step()
    assert engine.time.tick == 1 and engine.state is RunState.PAUSED
    controls.play()
    controls.single_step()
    assert engine.time.tick == 2 and engine.state is RunState.PAUSED
    for _ in range(14):
        controls.single_step()
    assert engine.state is RunState.FINISHED and engine.time.tick == 16
    controls.single_step()
    controls.play()
    assert controls.advance(1) == 0 and engine.time.tick == 16


def test_frame_partitioning_and_speed_preserve_determinism(scenario):
    def run(intervals, speed):
        engine = scenario.create_engine()
        controls = PlaybackController(engine)
        controls.set_speed(speed)
        controls.play()
        for dt in intervals:
            controls.advance(dt)
        return engine.time, engine.world, engine.event_trace
    assert run([0.1] * 30, 1) == run([0.3] * 10, 1) == run([0.05] * 30, 2)
    headless = scenario.create_engine()
    headless.run(6)
    assert run([0.1] * 30, 1) == (headless.time, headless.world, headless.event_trace)


def test_backlog_is_bounded_retained_and_cleared_on_pause(scenario):
    engine = scenario.create_engine()
    controls = PlaybackController(engine)
    controls.play()
    assert controls.advance(6) == 8 and engine.time.tick == 8
    assert controls.advance(0) == 4 and engine.time.tick == 12
    controls.reset()
    controls.play()
    assert controls.advance(0.4) == 0
    controls.pause()
    controls.play()
    assert controls.advance(0.1) == 0  # Old budget is discarded.


def test_speed_changes_clear_wall_budget(scenario):
    controls = PlaybackController(scenario.create_engine())
    controls.play()
    controls.advance(0.4)
    controls.set_speed(2)
    assert controls.advance(0.25) == 1
    controls.reset()
    assert controls.speed == 2


@pytest.mark.parametrize("value", [-1, True, float("nan"), float("inf"), "1"])
def test_invalid_frame_delta(scenario, value):
    with pytest.raises(ValueError):
        PlaybackController(scenario.create_engine()).advance(value)


@pytest.mark.parametrize("speed", [0, -1, True, float("nan"), 3, "2"])
def test_invalid_speed(scenario, speed):
    with pytest.raises(ValueError):
        PlaybackController(scenario.create_engine()).set_speed(speed)


def test_dashboard_never_publishes_truth_to_agents(scenario):
    engine = scenario.create_engine()
    views = []
    engine.register_controller("A", lambda view: views.append(view) or ())
    controls = PlaybackController(engine)
    controls.play()
    for _ in range(6):
        controls.advance(0.5)
        capture_dashboard(scenario, engine)
    assert all(v.observations == () and not hasattr(v, "world") and not hasattr(v, "tasks") for v in views)
    assert all(a.task_id is None for a in engine.world.agents)
    assert all(t.owner_id is None for t in engine.world.tasks)


def test_importing_headless_package_does_not_load_pygame():
    completed = subprocess.run([sys.executable, "-c", "import sys; import coopai.simulation, coopai.visualization; assert 'pygame' not in sys.modules"], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
