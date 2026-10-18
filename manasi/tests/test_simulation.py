from dataclasses import FrozenInstanceError, replace
import pytest

from coopai.interfaces import AgentCommand, SimulationTime
from coopai.models import (
    AgentIdentity, AgentState, Availability, Capabilities, DynamicEvent,
    Environment, EventKind, Location, MissionConfiguration, Observation,
    Position, Task, TaskStatus,
)
from coopai.simulation import Phase, RunState, SimulationEngine
from coopai.world import WorldCondition, WorldSnapshot


def agent(agent_id="A1"):
    return AgentState(AgentIdentity(agent_id, "Scout"), Capabilities(), Position("r1"), 1)


def engine(seed=42, duration=4):
    return SimulationEngine(MissionConfiguration("m", seed, duration, 0.25),
                            WorldSnapshot(Environment((Location("r1", "One"),)),
                                          agents=(agent(), agent("A2")),
                                          conditions=(WorldCondition("r1", "fire", "present"),)))


def event(event_id="e", tick=0):
    return DynamicEvent(event_id, tick, EventKind.HAZARD_CHANGED, "r1", "Test")


def noop(sim, event):
    pass


def test_clock_lifecycle_and_horizon():
    sim = engine()
    assert sim.time == SimulationTime(0, 0.25)
    with pytest.raises(RuntimeError):
        sim.step()
    sim.start()
    sim.step()
    assert sim.time.tick == 1 and sim.time.seconds == 0.25
    sim.pause()
    with pytest.raises(RuntimeError):
        sim.run(1)
    with pytest.raises(RuntimeError):
        sim.step()
    sim.resume()
    assert sim.run(20) == 3
    assert sim.time.seconds == 1 and sim.state is RunState.FINISHED
    assert sim.run(1) == 0
    with pytest.raises(RuntimeError):
        sim.step()


def test_zero_run_is_noop_and_single_tick_horizon():
    sim = engine(duration=1)
    assert sim.run(0) == 0 and sim.state is RunState.READY
    assert sim.run(5) == 1 and sim.time.tick == 1


@pytest.mark.parametrize("count", [-1, True, 1.5])
def test_invalid_run_bound(count):
    with pytest.raises(ValueError):
        engine().run(count)


def test_events_and_systems_order_independent_of_registration():
    def trace(reverse):
        sim = engine()
        seen = []
        names = ["z", "a"]
        for name in (reversed(names) if reverse else names):
            sim.schedule(event(name, 0), lambda s, e: seen.append(e.event_id))
            sim.register_system(name, Phase.EXECUTION, lambda s, n=name: seen.append(n + "-exec"))
        sim.register_system("record", Phase.RECORD, lambda s: seen.append("record"))
        sim.register_system("sense", Phase.PERCEPTION, lambda s: seen.append("sense"))
        sim.run(1)
        return seen, sim.event_trace
    first = trace(False)
    assert first == trace(True)
    assert first[0] == ["a", "z", "a-exec", "z-exec", "sense", "record"]


def test_seeded_streams_independent_and_reset_replays():
    def experiment(seed):
        sim = engine(seed)
        values = []
        sim.register_system("draw", Phase.PERCEPTION, lambda s: values.append(s.random_stream("sensors").random()))
        sim.register_system("noise", Phase.EXECUTION, lambda s: [s.random_stream("unrelated").random() for _ in range(9)])
        sim.schedule(event("update", 2), lambda s, e: s.update_agent(replace(s.world.agents[0], battery=0.5, updated_at=s.time.tick)))
        original = sim.world
        sim.run(4)
        expected = (tuple(values), sim.world, sim.event_trace)
        sim.reset()
        assert sim.world == original and sim.time.tick == 0 and sim.event_trace == ()
        assert sim.state is RunState.READY
        values.clear()  # Diagnostic closure is owned by this test, not the engine.
        sim.run(4)
        assert (tuple(values), sim.world, sim.event_trace) == expected
        return expected[0]
    assert experiment(42) == experiment(42)
    assert experiment(42) != experiment(43)
    sim = engine()
    draws = []
    sim.register_system("draw", Phase.PERCEPTION, lambda s: draws.append(s.random_stream("sensors").random()))
    sim.run(4)
    assert tuple(draws) == experiment(42)


@pytest.mark.parametrize("tick", [-1, 4, 9, True, 0.5])
def test_invalid_event_tick(tick):
    with pytest.raises(ValueError):
        engine().schedule(event(tick=tick), noop)


def test_duplicate_and_past_scheduling_and_new_future_event():
    sim = engine()
    sim.schedule(event(), noop)
    with pytest.raises(ValueError):
        sim.schedule(event(), noop)
    def schedule_future(s):
        if s.time.tick == 1:
            with pytest.raises(ValueError):
                s.schedule(event("past", 0), noop)
            with pytest.raises(ValueError):
                s.schedule(event("same", 1), noop)
            with pytest.raises(ValueError):
                s.schedule(event("e", 2), noop)
            s.schedule(event("later", 3), noop)
    sim.register_system("schedule", Phase.EXECUTION, schedule_future)
    sim.run(4)
    assert [r.event.event_id for r in sim.event_trace] == ["e", "later"]
    sim.reset()
    assert "later" not in sim._events  # Generated events are regenerated, not retained.


def test_atomic_world_validation_and_explicit_registration():
    sim = engine()
    before = sim.world
    with pytest.raises(ValueError):
        sim.register_agent(agent())
    with pytest.raises(ValueError):
        sim.update_agent(agent("missing"))
    with pytest.raises(ValueError):
        sim.update_agent(replace(agent(), position=Position("missing")))
    with pytest.raises(ValueError):
        sim.update_agent(replace(agent(), updated_at=1))
    assert sim.world == before
    sim.register_agent(agent("A3"))
    assert len(sim.world.agents) == 3
    with pytest.raises(FrozenInstanceError):
        sim.world.agents = ()
    sim.run(1)
    with pytest.raises(RuntimeError):
        sim.update_agent(agent())


def test_coupled_task_commitment_update_is_atomic_without_owner_selection():
    sim = engine()
    task = Task("t", "r1").transition(TaskStatus.ASSIGNED, "A1")
    busy = replace(agent(), availability=Availability.BUSY, task_id="t")
    with pytest.raises(ValueError):
        replace(sim.world, tasks=(task,))
    sim.replace_world(replace(sim.world, tasks=(task,), agents=(busy, agent("A2"))))
    sim.run(4)
    assert sim.world.tasks[0].owner_id == "A1"  # Engine leaves externally committed ownership unchanged.


@pytest.mark.parametrize("changes", [
    {"agents": (agent(), agent())}, {"tasks": (Task("t", "missing"),)},
    {"conditions": (WorldCondition("missing", "fire", "present"),)},
    {"conditions": (WorldCondition("r1", "fire", "present"), WorldCondition("r1", "fire", "absent"))},
    {"agents": []},
])
def test_world_consistency(changes):
    with pytest.raises(ValueError):
        replace(engine().world, **changes)


def test_local_view_no_truth_leak_and_commands_deferred():
    sim = engine()
    views = []
    consumed = []
    observation = Observation("o", "A1", "r1", "fire", "uncertain", 0, 0.4, "thermal")
    sim.register_system("evidence", Phase.PERCEPTION, lambda s: s.deliver_observation("A1", observation) if s.time.tick == 0 else None)
    sim.register_system("commands", Phase.EXECUTION, lambda s: consumed.append((s.time.tick, s.take_commands())))
    def controller(view):
        views.append(view)
        assert not hasattr(view, "world") and not hasattr(view, "tasks")
        assert not hasattr(view, "conditions") and not hasattr(view, "random_stream")
        assert view.own_state.identity.agent_id == "A1"
        return (AgentCommand(f"c-{view.time.tick}", "A1", view.time.tick, "inspect", "r1"),)
    other = []
    sim.register_controller("A2", lambda view: other.append(view) or ())
    sim.register_controller("A1", controller)
    sim.run(2)
    assert views[0].observations == (observation,)
    assert views[1].observations == ()
    assert all(v.observations == () for v in other)
    assert consumed[0] == (0, ())
    assert consumed[1][1][0].command_id == "c-0"
    assert sim.world.conditions[0].value == "present"


@pytest.mark.parametrize("bad_command", [
    AgentCommand("c", "A2", 0, "inspect"),
    AgentCommand("c", "A1", 1, "inspect"), "not-command",
])
def test_spoofed_or_invalid_command_fails_closed(bad_command):
    sim = engine()
    sim.register_controller("A1", lambda view: (bad_command,))
    with pytest.raises(ValueError):
        sim.run(1)
    assert sim.state is RunState.FAILED and sim.time.tick == 0
    with pytest.raises(RuntimeError):
        sim.step()
    sim.reset()
    assert sim.state is RunState.READY


def test_duplicate_command_and_non_tuple_response():
    for callback in (lambda v: [AgentCommand("c", "A1", 0, "inspect")],
                     lambda v: (AgentCommand("c", "A1", v.time.tick, "inspect"),)):
        sim = engine()
        sim.register_controller("A1", callback)
        with pytest.raises(ValueError):
            sim.run(2)


def test_invalid_observation_delivery():
    sim = engine()
    base = Observation("o", "A1", "r1", "fire", "present", 0, 1, "thermal")
    with pytest.raises(RuntimeError):
        sim.deliver_observation("A1", base)
    def validate(s):
        for recipient, obs in (("missing", base), ("A1", replace(base, source_id="missing")),
                               ("A1", replace(base, location_id="missing")), ("A1", replace(base, observed_at=1))):
            with pytest.raises(ValueError):
                s.deliver_observation(recipient, obs)
    sim.register_system("validate", Phase.PERCEPTION, validate)
    sim.run(1)


def test_callback_failure_and_reentrancy_require_reset():
    sim = engine()
    def broken(s, e):
        s.step()
    sim.schedule(event(), broken)
    with pytest.raises(RuntimeError):
        sim.run(1)
    assert sim.state is RunState.FAILED
    sim.reset()
    assert sim.world == engine().world


def test_registration_validation_and_lifecycle():
    sim = engine()
    with pytest.raises(ValueError):
        sim.register_system("allocator", Phase.DECISION, lambda s: None)
    with pytest.raises(ValueError):
        sim.register_controller("missing", lambda v: ())
    sim.register_controller("A1", lambda v: ())
    with pytest.raises(ValueError):
        sim.replace_world(replace(sim.world, agents=(agent("A2"),)))
    sim.start()
    for action in (sim.start, sim.resume, lambda: sim.register_system("new", Phase.RECORD, lambda s: None)):
        with pytest.raises(RuntimeError):
            action()


def test_demo_is_replayable(capsys):
    from coopai.demo import build_demo
    sim = build_demo()
    sim.run(5)
    first = capsys.readouterr().out
    snapshot = sim.world
    sim.reset()
    sim.run(5)
    assert capsys.readouterr().out == first
    assert sim.world == snapshot
