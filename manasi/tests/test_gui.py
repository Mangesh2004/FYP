"""Optional SDL dummy-driver integration tests; no desktop required."""
from importlib.util import find_spec
from pathlib import Path

import pytest

from coopai.scenario import load_scenario
from coopai.simulation import RunState


@pytest.fixture
def app(monkeypatch):
    if find_spec("pygame") is None:
        pytest.skip("install the gui extra for renderer integration tests")
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    from coopai.gui import DashboardApp
    instance = DashboardApp(load_scenario(Path(__file__).resolve().parents[1] / "scenarios/four_room_disaster.json"), with_communication=False)
    yield instance
    instance.close()


def click(app, action):
    app.handle_event(app.pg.event.Event(app.pg.MOUSEBUTTONDOWN, button=1, pos=app.buttons[action].center))


def key(app, keycode):
    app.handle_event(app.pg.event.Event(app.pg.KEYDOWN, key=keycode))


def test_actual_renderer_text_and_snapshot_updates(app, monkeypatch):
    words = []
    original = app._text
    def record(surface, value, *args, **kwargs):
        words.append(value)
        return original(surface, value, *args, **kwargs)
    monkeypatch.setattr(app, "_text", record)
    before = app.engine.world
    initial = app.draw()
    pixels = app.pg.image.tobytes(app.screen, "RGB")
    assert app.engine.world is before and app.engine.time.tick == 0
    assert "SIMULATOR VIEW — NOT AGENT KNOWLEDGE" in words
    assert "fire: present" in words and "90%" in words
    assert words.count("Owner: unassigned") == 4
    assert "Sense: thermal, visual" in words
    assert "Ops: exploration, simulated-assistance" in words
    words.clear()
    for _ in range(6):
        click(app, "step")
    updated = app.draw()
    assert "fire: severe" in words and "40%" in words
    assert any("fire-escalates" in w for w in words)
    assert any("battery-drop" in w for w in words)
    assert app.pg.image.tobytes(app.screen, "RGB") != pixels
    assert [a.marker for a in initial.agents] == [a.marker for a in updated.agents]


def test_mouse_and_keyboard_controls(app):
    click(app, "play")
    assert app.engine.state is RunState.RUNNING
    app.update(0.5)
    key(app, app.pg.K_SPACE)
    assert app.engine.state is RunState.PAUSED
    app.update(5)
    assert app.engine.time.tick == 1
    key(app, app.pg.K_RIGHT)
    assert app.engine.time.tick == 2 and app.engine.state is RunState.PAUSED
    key(app, app.pg.K_EQUALS)
    assert app.playback.speed == 2
    click(app, "slower")
    assert app.playback.speed == 1
    key(app, app.pg.K_r)
    assert app.engine.time.tick == 0 and app.engine.world == app.scenario.initial_world
    key(app, app.pg.K_ESCAPE)
    assert not app.running


def test_focus_loss_pauses_and_quit_closes_loop(app):
    click(app, "play")
    app.handle_event(app.pg.event.Event(app.pg.WINDOWFOCUSLOST))
    assert app.engine.state is RunState.PAUSED
    app.handle_event(app.pg.event.Event(app.pg.QUIT))
    assert not app.running


def test_smoke_saves_rendered_frame_and_resets(app, tmp_path):
    screenshot = tmp_path / "preview.png"
    app.smoke_test(screenshot)
    assert screenshot.exists()
    surface = app.pg.image.load(str(screenshot))
    assert surface.get_size() == (1200, 780)
    assert app.engine.time.tick == 0 and app.engine.event_trace == ()


def test_finished_controls_are_disabled_and_reset_replays(app):
    for _ in range(16):
        click(app, "step")
    assert app.engine.state is RunState.FINISHED
    for name in ("play", "step", "pause"):
        click(app, name)
    assert app.engine.time.tick == 16
    click(app, "reset")
    assert app.engine.state is RunState.READY
    app.draw()
    assert app.max_scroll == 0


def test_event_loop_does_not_charge_idle_time_when_play_is_clicked(app, monkeypatch):
    events = iter((
        [app.pg.event.Event(app.pg.MOUSEBUTTONDOWN, button=1, pos=app.buttons["play"].center)],
        [app.pg.event.Event(app.pg.MOUSEBUTTONDOWN, button=1, pos=app.buttons["pause"].center)],
        [app.pg.event.Event(app.pg.QUIT)],
    ))
    class FakeClock:
        def tick(self, fps):
            return 1000
    monkeypatch.setattr(app.pg.time, "Clock", FakeClock)
    monkeypatch.setattr(app.pg.event, "get", lambda: next(events))
    app.run()
    assert app.engine.time.tick == 0
    assert app.engine.state is RunState.PAUSED


def test_selected_agent_knowledge_is_local_and_unknown_is_explicit(app, monkeypatch):
    words = []
    original = app._text
    def record(surface, value, *args, **kwargs):
        words.append(value)
        return original(surface, value, *args, **kwargs)
    monkeypatch.setattr(app, "_text", record)
    for _ in range(4):
        click(app, "step")
    key(app, app.pg.K_1)
    app.draw()
    assert "AGENT-LOCAL KNOWLEDGE / A" in words
    assert "room-1 | fire: severe" in words
    assert "Confidence: 85% | Observed tick: 3" in words
    assert not any("room-2 | gas" in w for w in words)
    words.clear()
    key(app, app.pg.K_4)
    app.draw()
    assert "AGENT-LOCAL KNOWLEDGE / D" in words
    assert any("UNKNOWN" in w for w in words)
    words.clear()
    key(app, app.pg.K_r)
    key(app, app.pg.K_1)
    app.draw()
    assert any("UNKNOWN" in w for w in words)
    assert app.engine.observation_history("A") == ()
    key(app, app.pg.K_k)
    assert not app.show_knowledge


def test_agent_circle_selects_knowledge(app):
    snapshot = app.draw()
    display = next(a for a in snapshot.agents if a.state.identity.agent_id == "C")
    pos = app.transform.point(*display.marker)
    app.handle_event(app.pg.event.Event(app.pg.MOUSEBUTTONDOWN, button=1, pos=pos))
    assert app.selected_agent == "C" and app.show_knowledge


def test_stale_local_claim_is_not_replaced_by_unobserved_world_changes(app, monkeypatch):
    from dataclasses import replace
    from coopai.models import Availability
    from coopai.simulation import Phase
    def stop_sensor(sim):
        if sim.time.tick == 1:
            sim.update_agent(replace(sim.world.agents[0], availability=Availability.FAILED, updated_at=1))
    app.engine.register_system("stop-sensor", Phase.EXECUTION, stop_sensor)
    for _ in range(6):
        click(app, "step")
    words = []
    original = app._text
    def record(surface, value, *args, **kwargs):
        words.append(value)
        return original(surface, value, *args, **kwargs)
    monkeypatch.setattr(app, "_text", record)
    key(app, app.pg.K_1)
    app.draw()
    assert "room-1 | fire: present" in words
    assert "fire: severe" in words  # Separate privileged floor-plan view.
    assert any("STALE" in w for w in words)


def test_communication_inspector_remote_provenance_and_reset(app, monkeypatch):
    app.engine.enable_communication()
    words = []
    original = app._text
    def record(surface, value, *args, **kwargs):
        words.append(value)
        return original(surface, value, *args, **kwargs)
    monkeypatch.setattr(app, "_text", record)
    for _ in range(2):
        click(app, "step")
    key(app, app.pg.K_2)
    app.draw()
    assert any("Remote via A" in w and "Source: A" in w for w in words)
    assert any("Direct" in w and "Source: B" in w for w in words)
    words.clear()
    key(app, app.pg.K_m)
    app.draw()
    assert "COMMUNICATION / SIMULATOR DIAGNOSTICS" in words
    assert any("sent" in w and "A -> B" in w for w in words)
    assert any("delivered" in w for w in words)
    words.clear()
    key(app, app.pg.K_r)
    app.draw()
    assert "Sent 0 | Delivered 0 | Dropped 0" in words
    assert app.engine.communication.trace == ()


def test_communication_drop_is_displayed(app, monkeypatch):
    from coopai.communication import CommunicationConfiguration
    app.engine.enable_communication(CommunicationConfiguration(loss_probability=1))
    words = []
    original = app._text
    def record(surface, value, *args, **kwargs):
        words.append(value)
        return original(surface, value, *args, **kwargs)
    monkeypatch.setattr(app, "_text", record)
    click(app, "step")
    key(app, app.pg.K_m)
    app.draw()
    assert any("dropped" in w for w in words)
    assert "Sent 24 | Delivered 0 | Dropped 24" in words
    assert any("observation loss" in w for w in words)


def test_agent_local_priority_inspector_and_reset(app, monkeypatch):
    words = []
    original = app._text
    def record(surface,value,*args,**kwargs):
        words.append(value)
        return original(surface,value,*args,**kwargs)
    monkeypatch.setattr(app,"_text",record)
    for _ in range(4):
        click(app,"step")
    key(app,app.pg.K_p)
    app.draw()
    assert "CALCULATED AGENT-LOCAL PRIORITY / A" in words
    assert any("Scenario initial urgency != calculated priority" in w for w in words)
    assert any("Initial urgency" in w and "Eval tick 3" in w for w in words)
    a = next(r.score for r in app.engine.priority_snapshot("A") if r.task_id == "task-1")
    words.clear()
    key(app,app.pg.K_2)
    app.draw()
    assert "CALCULATED AGENT-LOCAL PRIORITY / B" in words
    assert app.show_priority and not app.show_knowledge
    b = next(r.score for r in app.engine.priority_snapshot("B") if r.task_id == "task-1")
    assert a > b
    key(app,app.pg.K_r)
    assert app.engine.priority_snapshot("B") == ()
    assert len(app.engine.known_tasks("B")) == 4


def test_eligibility_inspector_own_results_modes_battery_and_reset(app, monkeypatch):
    words = []
    original = app._text
    def record(surface, value, *args, **kwargs):
        words.append(value)
        return original(surface,value,*args,**kwargs)
    monkeypatch.setattr(app,"_text",record)
    key(app,app.pg.K_e)
    app.draw()
    assert "ELIGIBILITY / SELF-SUITABILITY / A" in words
    assert any("Play/step to evaluate" in w for w in words)
    click(app,"step")
    words.clear()
    before = app.engine.suitability_snapshot("A")
    app.draw()
    assert app.engine.suitability_snapshot("A") is before
    assert any("Eligible: False | Suitability N/A" in w for w in words)
    assert any("Missing sensing: gas" in w for w in words)
    key(app,app.pg.K_2)
    assert app.show_suitability and not app.show_knowledge
    for _ in range(5): click(app,"step")
    words.clear()
    app.draw()
    assert "ELIGIBILITY / SELF-SUITABILITY / B" in words
    assert any("Suitability 0.680" in w for w in words)
    assert any("battery 0.33" in w for w in words)
    key(app,app.pg.K_r)
    assert app.engine.suitability_snapshot("B") == ()
    click(app,"priority")
    assert app.show_priority and not app.show_suitability
    click(app,"suitability")
    assert app.show_suitability and not app.show_priority
    click(app,"knowledge")
    assert app.show_knowledge and not app.show_suitability


def test_allocation_inspector_provisional_confirmed_and_reset(app, monkeypatch):
    app.engine.enable_communication()
    app.engine.enable_allocation()
    words=[]
    original=app._text
    def record(surface,value,*args,**kwargs):
        words.append(value)
        return original(surface,value,*args,**kwargs)
    monkeypatch.setattr(app,"_text",record)
    for _ in range(4): click(app,"step")
    key(app,app.pg.K_2)
    key(app,app.pg.K_l)
    before=app.engine.world
    app.draw()
    assert app.engine.world is before
    assert "ALLOCATION / SIMULATOR DIAGNOSTICS" in words
    assert any("task-1: pending | confirmed owner none" in w for w in words)
    assert any("task-2: assigned | confirmed owner B" in w for w in words)
    assert any("Provisional A | confirmed -" in w for w in words)
    assert any("Received bids: A 0.970" in w for w in words)
    key(app,app.pg.K_1)
    assert app.show_allocation and not app.show_knowledge
    for _ in range(5): click(app,"step")
    words.clear();app.draw()
    assert any("task-1: assigned | confirmed owner A" in w for w in words)
    assert any("Phase: confirmed assignment" in w for w in words)
    click(app,"suitability")
    assert app.show_suitability and not app.show_allocation
    click(app,"allocation")
    assert app.show_allocation and not app.show_suitability
    key(app,app.pg.K_r)
    assert app.engine.commit_trace==() and app.engine.allocation_snapshot("A")==()
    assert all(t.owner_id is None for t in app.engine.world.tasks)


def test_allocation_inspector_disabled_is_explicit(app, monkeypatch):
    words=[]
    original=app._text
    def record(surface,value,*args,**kwargs):
        words.append(value)
        return original(surface,value,*args,**kwargs)
    monkeypatch.setattr(app,"_text",record)
    key(app,app.pg.K_l);app.draw()
    assert "Allocation disabled; requires peer communication." in words


def enable_gui_execution(app):
    from coopai.execution_models import scenario_execution_specs
    app.engine.enable_communication()
    app.engine.enable_allocation()
    app.engine.enable_execution(app.scenario.floor_plan,scenario_execution_specs(app.scenario.task_details))
    app.initial_world=app.engine.world


def test_execution_gui_real_positions_routes_progress_and_completed_hazards(app,monkeypatch):
    enable_gui_execution(app)
    words=[]
    original=app._text
    def record(surface,value,*args,**kwargs):
        words.append(value)
        return original(surface,value,*args,**kwargs)
    monkeypatch.setattr(app,"_text",record)
    for _ in range(9): click(app,"step")
    key(app,app.pg.K_4);key(app,app.pg.K_x)
    snapshot=app.draw()
    assert "EXECUTION / SIMULATOR DIAGNOSTICS / D" in words
    assert "Phase MOVING | Task task-4" in words
    display=next(a for a in snapshot.agents if a.state.identity.agent_id=="D")
    assert display.marker==(display.state.position.x,display.state.position.y)
    assert any("Accepted waypoints 4" in w for w in words)
    before=app.engine.world;app.update(100)
    assert app.engine.world is before
    for _ in range(7): click(app,"step")
    words.clear();app.draw()
    assert "Phase COMPLETED | Task task-4" in words and "Progress 2/2 valid execution ticks" in words
    assert any("hazard=clear" in w and "confidence 60%" in w for w in words)
    assert "fire: severe" in words
    assert all(t.status.value=="completed" for t in app.engine.world.tasks)
    key(app,app.pg.K_1)
    assert app.show_execution and not app.show_knowledge
    click(app,"allocation")
    assert app.show_allocation and not app.show_execution
    click(app,"execution")
    assert app.show_execution and not app.show_allocation
    key(app,app.pg.K_r)
    assert app.engine.world==app.initial_world and app.engine.execution_trace==()


def test_execution_gui_failure_reason_and_no_motion_after_failure(app,monkeypatch):
    from dataclasses import replace
    from coopai.models import DynamicEvent,EventKind
    enable_gui_execution(app)
    def low(sim,event):
        own=next(a for a in sim.world.agents if a.identity.agent_id=="D")
        sim.update_agent(replace(own,battery=0.03,updated_at=9))
    app.engine.schedule(DynamicEvent("low",9,EventKind.RESOURCES_CHANGED,"D","low battery"),low)
    for _ in range(10): click(app,"step")
    key(app,app.pg.K_4);key(app,app.pg.K_x)
    words=[]
    original=app._text
    def record(surface,value,*args,**kwargs):
        words.append(value)
        return original(surface,value,*args,**kwargs)
    monkeypatch.setattr(app,"_text",record)
    app.draw()
    assert "Phase FAILED | Task task-4" in words
    assert any("Battery" in w and "Outcome" in w for w in words)
    own=next(a for a in app.engine.world.agents if a.identity.agent_id=="D")
    for _ in range(6): click(app,"step")
    assert next(a.position for a in app.engine.world.agents if a.identity.agent_id=="D")==own.position


def test_execution_gui_smoke_saves_movement_and_outcome_and_resets(app,tmp_path):
    enable_gui_execution(app)
    path=tmp_path/"execution.png"
    app.smoke_test(path)
    assert path.with_name("execution.movement.png").exists()
    assert path.with_name("execution.execution.png").exists()
    assert app.engine.world==app.initial_world and app.engine.execution_trace==()


def test_recovery_gui_shows_attempt_history_actual_owner_and_controls(app,monkeypatch):
    from coopai.recovery_demo import build_demo
    from coopai.visualization import PlaybackController
    scenario,engine=build_demo(horizon=45)
    app.scenario,app.engine=scenario,engine
    app.initial_world=engine.world
    app.playback=PlaybackController(engine)
    engine.run(36)
    words=[]
    original=app._text
    def record(surface,value,*args,**kwargs):
        words.append(value)
        return original(surface,value,*args,**kwargs)
    monkeypatch.setattr(app,"_text",record)
    key(app,app.pg.K_h)
    before=engine.world
    app.draw()
    assert engine.world is before and app.show_recovery
    assert "RECOVERY / SIMULATOR DIAGNOSTICS" in words
    assert "task-4: completed" in words
    assert any("Attempt 1: failed; owner D" in w for w in words)
    assert any("Attempt 2: completed; owner A" in w for w in words)
    assert any("Failure 9 | detected 15 | released 9" in w for w in words)
    assert any("New owner A at 23 | completed 35" in w for w in words)
    key(app,app.pg.K_3)
    assert app.show_recovery and not app.show_knowledge
    click(app,"execution")
    assert app.show_execution and not app.show_recovery
    click(app,"recovery")
    assert app.show_recovery and not app.show_execution
    key(app,app.pg.K_r)
    assert engine.time.tick==0 and engine.world==app.initial_world and engine.recovery_trace==()


def test_recovery_gui_disabled_and_h_does_not_reset(app,monkeypatch):
    words=[]
    original=app._text
    def record(surface,value,*args,**kwargs):
        words.append(value)
        return original(surface,value,*args,**kwargs)
    monkeypatch.setattr(app,"_text",record)
    click(app,"step")
    key(app,app.pg.K_h)
    app.draw()
    assert app.engine.time.tick==1 and app.show_recovery
    assert "Recovery disabled; requires autonomous execution." in words


def test_recovery_gui_new_task_metadata_and_local_priority(app):
    from coopai.recovery_demo import build_demo
    from coopai.visualization import PlaybackController
    scenario,engine=build_demo("new-task",horizon=45)
    app.scenario,app.engine=scenario,engine
    app.playback=PlaybackController(engine)
    engine.run(13)
    snapshot=app.draw()
    assert any(t.task.task_id=="urgent-inspection" and t.details.duration_ticks==2 for t in snapshot.tasks)
    key(app,app.pg.K_h);app.draw()
    assert app.show_recovery
    click(app,"priority");app.draw()
    assert app.show_priority and not app.show_recovery
