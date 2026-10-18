"""Actual SDL input/rendering for review missions; no display server required."""
from importlib.util import find_spec

import pytest

from coopai.mission_scenarios import get_scenario
from coopai.simulation import RunState


@pytest.fixture
def app(monkeypatch):
    if find_spec("pygame") is None: pytest.skip("install the gui extra")
    monkeypatch.setenv("SDL_VIDEODRIVER","dummy")
    monkeypatch.setenv("PYGAME_HIDE_SUPPORT_PROMPT","1")
    from coopai.gui import DashboardApp
    case=get_scenario("corridor")
    instance=DashboardApp(case.scenario,mission_case=case)
    yield instance
    instance.close()


def key(app,code): app.handle_event(app.pg.event.Event(app.pg.KEYDOWN,key=code))
def click(app,position): app.handle_event(app.pg.event.Event(app.pg.MOUSEBUTTONDOWN,button=1,pos=position))


def capture_words(app,monkeypatch):
    words=[];original=app._text
    def record(surface,value,*args,**kwargs):
        words.append(value)
        return original(surface,value,*args,**kwargs)
    monkeypatch.setattr(app,"_text",record)
    return words


def test_menu_mouse_and_keyboard_switch_complete_sessions_without_state_leaks(app):
    app.engine.run(5)
    old=app.engine
    assert old.communication.trace
    key(app,app.pg.K_s);app.draw()
    assert app.show_scenario_menu and old.state is RunState.PAUSED
    click(app,app._menu_rects["distributed"].center)
    assert app.mission_case.scenario_id=="distributed" and app.engine is not old
    assert app.engine.time.tick==0 and app.engine.communication.trace==()
    assert app.engine.state is RunState.READY and app.playback.speed==1
    key(app,app.pg.K_s);key(app,app.pg.K_3)
    assert app.mission_case.scenario_id=="emergency"
    app.engine.run(13)
    assert any(t.task_id=="emergency-verification" for t in app.engine.world.tasks)
    key(app,app.pg.K_s);key(app,app.pg.K_4)
    assert app.mission_case.scenario_id=="failure-disruption"
    assert not any(t.task_id=="emergency-verification" for t in app.engine.world.tasks)
    assert app.engine.world==app.initial_world and len(app.engine.recovery_snapshot())==4
    assert app.engine.recovery_trace==() and app.selected_task is None
    assert all(len(app.engine.known_tasks(a))==4 for a in "ABCD")
    key(app,app.pg.K_s);key(app,app.pg.K_5)
    assert app.mission_case.scenario_id=="constrained"
    key(app,app.pg.K_s);key(app,app.pg.K_1)
    assert {a.position.location_id for a in app.engine.world.agents}=={"corridor"}
    assert old.time.tick==5


def test_menu_pauses_and_escape_closes_menu_without_quitting(app):
    key(app,app.pg.K_SPACE);app.update(.5)
    key(app,app.pg.K_s);app.draw()
    before=app.engine.world
    app.update(10)
    assert app.engine.world is before
    key(app,app.pg.K_ESCAPE)
    assert not app.show_scenario_menu and app.running
    key(app,app.pg.K_ESCAPE)
    assert not app.running


def test_clickable_task_explanations_show_stored_bid_and_preserve_agent_selection(app,monkeypatch):
    words=capture_words(app,monkeypatch)
    app.draw()
    task_id,rect=app.task_hitboxes[0]
    click(app,rect.center)
    assert app.selected_task==task_id=="task-1"
    before=app.engine.world;app.draw()
    assert app.engine.world is before and app.engine.time.tick==0
    assert "TASK / DECISION EXPLANATION" in words
    app.engine.run(20)
    words.clear();key(app,app.pg.K_2);app.draw()
    assert app.selected_task=="task-1" and app.selected_agent=="B"
    assert not app.show_knowledge
    assert any("NO_BID" in w for w in words)
    words.clear();key(app,app.pg.K_1);app.draw()
    assert any("At bid:" in w and "suitability" in w for w in words)
    assert any("At bid travel:" in w for w in words)
    key(app,app.pg.K_TAB)
    assert app.selected_task=="task-2"
    key(app,app.pg.K_p)
    assert app.show_priority and app.selected_task is None
    key(app,app.pg.K_j);app.draw()
    assert app.show_events and not app.show_priority
    key(app,app.pg.K_j)
    assert not app.show_events


def test_task_destination_ring_opens_actual_task_without_moving_world(app):
    snapshot=app.draw()
    display=next(t for t in snapshot.tasks if t.task.task_id=="task-1")
    x,y=app.transform.point(*display.marker)
    before=app.engine.world
    click(app,(x+25,y))
    assert app.selected_task=="task-1" and app.engine.world is before


def test_automatic_summary_and_dismissal_show_real_outcomes(app,monkeypatch):
    words=capture_words(app,monkeypatch)
    app.engine.run(app.engine.mission.duration_ticks)
    app.draw()
    assert "MISSION COMPLETE" in words
    assert any("Total objectives 4 | Completed 4 | Incomplete 0" in w for w in words)
    assert "Inspections report conditions; hazards have not been mitigated." in words
    assert any("task-1: completed; owner" in w for w in words)
    assert any("86.3%" in w for w in words)
    words.clear();key(app,app.pg.K_v);app.draw()
    assert "MISSION OVERVIEW" in words
    words.clear();key(app,app.pg.K_v);app.draw()
    assert "MISSION COMPLETE" in words
    key(app,app.pg.K_r);words.clear();app.draw()
    assert app.engine.time.tick==0 and "MISSION OVERVIEW" in words
    assert app.engine.execution_trace==() and app.engine.recovery_trace==()


def test_constrained_summary_keeps_pending_distinct_from_failed_attempts(app,monkeypatch):
    app.switch_scenario("constrained")
    app.engine.run(app.engine.mission.duration_ticks)
    words=capture_words(app,monkeypatch);app.draw()
    assert "MISSION INCOMPLETE" in words
    assert any("Completed 3 | Incomplete 1" in w for w in words)
    assert "Pending 1 | Permanently failed 0" in words
    assert "Failed execution attempts 0 | Confirmed reassignments 0" in words
    app._select_task("task-4");words.clear();app.draw()
    assert any("resource" in w.lower() for w in words)
    assert any("NO_BID" in w for w in words)


def test_emergency_inspector_does_not_reveal_task_to_an_uninformed_agent(app,monkeypatch):
    app.switch_scenario("emergency");app.engine.run(13)
    app.selected_agent="C";app._select_task("emergency-verification")
    words=capture_words(app,monkeypatch);app.draw()
    assert any("Task UNKNOWN to C" in w for w in words)
    assert "emergency-verification" not in app.engine._executors["C"].specs
    app.engine.run(1);words.clear();app.draw()
    assert not any("Task UNKNOWN" in w for w in words)
    assert "emergency-verification" in app.engine._executors["C"].specs
    key(app,app.pg.K_r);app.draw()
    assert app.selected_task is None
    assert "emergency-verification" not in app.engine._executors["C"].specs


def test_failure_summary_and_recovery_inspector_keep_failed_history(app,monkeypatch):
    app.switch_scenario("failure-disruption");app.engine.run(app.engine.mission.duration_ticks)
    words=capture_words(app,monkeypatch);app.draw()
    assert "Failed execution attempts 1 | Confirmed reassignments 1" in words
    assert any("Agents failed/offline 1" in w for w in words)
    key(app,app.pg.K_h);words.clear();app.draw()
    assert app.show_recovery and any("Attempt 1: failed; owner D" in w for w in words)
    assert any("Attempt 2: completed" in w for w in words)
    key(app,app.pg.K_r);words.clear();app.draw()
    assert not any("Attempt 1: failed" in w for w in words)
    assert all(len(o.attempts)==1 for o in app.engine.recovery_snapshot())


def test_scenario_smoke_saves_real_initial_motion_summary_and_replays(app,tmp_path):
    path=tmp_path/"mission.png"
    app.scenario_smoke_test(path)
    assert path.exists() and path.with_name("mission.initial.png").exists()
    assert path.with_name("mission.movement.png").exists()
    assert app.engine.state is RunState.READY and app.engine.world==app.initial_world


def test_summary_appears_after_real_completion_without_waiting_for_idle_horizon(app,monkeypatch):
    app.engine.run(19)
    assert app.engine.state is RunState.RUNNING and app.engine.time.tick<app.engine.mission.duration_ticks
    words=capture_words(app,monkeypatch);app.draw()
    assert "MISSION COMPLETE" in words
    assert any("clock can continue" in w for w in words)
    # Rendering does not stop or advance the mechanics or fabricate FINISHED.
    assert app.engine.state is RunState.RUNNING and app.engine.time.tick==19
