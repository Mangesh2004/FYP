from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from coopai.knowledge import KnowledgeEntry, LocalKnowledge
from coopai.models import Availability, Observation, Position
from coopai.perception import PerceptionConfiguration, SensorRule, install_local_perception
from coopai.scenario import load_scenario
from coopai.simulation import Phase, RunState


@pytest.fixture
def scenario():
    return load_scenario(Path(__file__).resolve().parents[1] / "scenarios/four_room_disaster.json")


def obs(identity="o", tick=0, value="possible", confidence=0.6, source="A"):
    return Observation(identity, source, "room-1", "person", value, tick, confidence, "thermal")


def test_sensor_permissions_locality_and_uncertainty(scenario):
    sim = scenario.create_engine(with_perception=True)
    assert all(sim.knowledge_snapshot(a) == () for a in "ABCD")
    sim.run(1)
    expected = {"A": [("room-1", "fire", "thermal")], "B": [("room-2", "gas", "gas")],
                "C": [("room-3", "person", "thermal"), ("room-3", "person", "visual")], "D": []}
    for agent_id in "ABCD":
        entries = sim.knowledge_snapshot(agent_id)
        assert [(e.observation.location_id, e.observation.subject, e.observation.sensing_capability) for e in entries] == expected[agent_id]
        assert all(e.observation.source_id == agent_id and e.observation.observed_at == 0 and e.received_at == 0 for e in entries)
    assert {e.observation.value for e in sim.knowledge_snapshot("C")} == {"possible"}
    assert {e.observation.confidence for e in sim.knowledge_snapshot("C")} == {0.6, 0.75}


def test_no_remote_detection_and_operations_do_not_grant_sensors(scenario):
    sim = scenario.create_engine(with_perception=True)
    d = next(a for a in sim.world.agents if a.identity.agent_id == "D")
    sim.update_agent(replace(d, position=Position("room-1")))
    a = next(a for a in sim.world.agents if a.identity.agent_id == "A")
    sim.update_agent(replace(a, position=Position("room-2")))
    sim.run(4)
    assert sim.knowledge_snapshot("D") == ()
    assert sim.knowledge_snapshot("A") == ()


@pytest.mark.parametrize("availability,battery", [(Availability.FAILED, 1), (Availability.OFFLINE, 1), (Availability.AVAILABLE, 0)])
def test_unavailable_unpowered_agents_do_not_sense(scenario, availability, battery):
    sim = scenario.create_engine(with_perception=True)
    sim.update_agent(replace(sim.world.agents[0], availability=availability, battery=battery))
    sim.run(2)
    assert sim.observation_history("A") == ()


def test_local_view_contains_only_own_evidence_and_updates_before_decisions(scenario):
    sim = scenario.create_engine(with_perception=True)
    views = {a: [] for a in "ABCD"}
    for agent_id in views:
        sim.register_controller(agent_id, lambda v, owner=agent_id: views[owner].append(v) or ())
    sim.run(4)
    for agent_id, history in views.items():
        assert len(history) == 4
        assert all(not hasattr(v, "world") and not hasattr(v, "agents") for v in history)
        assert all(e.observation.source_id == agent_id for v in history for e in v.knowledge)
        assert all(o.source_id == agent_id for v in history for o in v.observations)
        assert all(e.observation.observed_at == v.time.tick for v in history for e in v.knowledge)
    assert views["A"][-1].knowledge[0].observation.value == "severe"
    assert views["A"][0].knowledge[0].observation.value == "present"
    assert all(v.knowledge == () and v.observations == () for v in views["D"])
    with pytest.raises(FrozenInstanceError):
        views["A"][-1].knowledge[0].received_at = 9


def test_independent_stores_ordering_history_and_duplicate_integrity():
    a, b = LocalKnowledge("A"), LocalKnowledge("B")
    a.record(obs("new", 5, "present", 0.3), 5)
    a.record(obs("old", 1, "possible", 0.9), 6)
    assert a.lookup("room-1", "person", "thermal").observation.value == "present"
    assert len(a.history) == 2 and b.snapshot() == ()
    assert not a.record(obs("new", 5, "present", 0.3), 6)
    assert len(a.history) == 2
    with pytest.raises(ValueError, match="reused"):
        a.record(obs("new", 5, "absent", 0.3), 6)
    assert len(a.history) == 2
    with pytest.raises(ValueError, match="peer"):
        b.record(obs(), 6)
    assert b.history == ()
    with pytest.raises(ValueError, match="regress"):
        a.record(obs("regression", 0), 4)
    assert a.known("unknown", "person") == ()
    assert a.lookup("room-1", "fire", "thermal") is None


def test_equal_tick_tie_break_is_id_based_not_arrival_or_confidence():
    stores = [LocalKnowledge("A"), LocalKnowledge("A")]
    values = [obs("a", 1, "possible", 0.9), obs("z", 1, "present", 0.2)]
    for store, sequence in zip(stores, (values, values[::-1])):
        for value in sequence:
            store.record(value, 1)
    assert stores[0].snapshot() == stores[1].snapshot()
    assert stores[0].snapshot()[0].observation.confidence == 0.2


def test_staleness_boundary_preserves_evidence():
    entry = KnowledgeEntry(obs(tick=2), 3)
    assert not entry.is_stale(5, 3)
    assert entry.is_stale(6, 3)
    assert entry.observation.value == "possible"
    for now, age in ((1, 3), (4, -1), (True, 3)):
        with pytest.raises(ValueError):
            entry.is_stale(now, age)
    with pytest.raises(ValueError, match="receipt"):
        KnowledgeEntry(obs(tick=4), 3)


def test_retained_local_evidence_does_not_follow_unseen_truth(scenario):
    sim = scenario.create_engine()
    install_local_perception(sim, PerceptionConfiguration(interval_ticks=99))
    sim.run(6)
    entry = sim.knowledge_snapshot("A")[0]
    assert entry.observation.value == "present" and entry.observation.observed_at == 0
    assert entry.is_stale(sim.time.tick, 3)
    assert next(c.value for c in sim.world.conditions if c.subject == "fire") == "severe"
    assert len(sim.observation_history("A")) == 1


def test_configurable_confidence_and_sensor_interval(scenario):
    sim = scenario.create_engine()
    install_local_perception(sim, PerceptionConfiguration((SensorRule("fire", "thermal", 0),), interval_ticks=2))
    sim.run(5)
    history = sim.observation_history("A")
    assert [e.observation.observed_at for e in history] == [0, 2, 4]
    assert all(e.observation.confidence == 0 for e in history)
    assert history[-1].observation.value == "severe"
    assert sim.observation_history("C") == ()


@pytest.mark.parametrize("confidence", [-1, 1.1, float("nan"), float("inf"), True])
def test_confidence_bounds(confidence):
    with pytest.raises(ValueError):
        SensorRule("fire", "thermal", confidence)


@pytest.mark.parametrize("changes", [{"interval_ticks": 0}, {"interval_ticks": True}, {"max_age_ticks": -1}, {"rules": []}, {"rules": (SensorRule("fire", "thermal", 1),) * 2}])
def test_configuration_validation(changes):
    with pytest.raises(ValueError):
        PerceptionConfiguration(**changes)


def test_reset_clears_and_replays_all_state_without_external_hook(scenario):
    sim = scenario.create_engine(with_perception=True)
    sim.run(6)
    expected = tuple(sim.observation_history(a) for a in "ABCD")
    snapshots = tuple(sim.knowledge_snapshot(a) for a in "ABCD")
    world, events = sim.world, sim.event_trace
    assert [len(h) for h in expected] == [6, 6, 12, 0]
    sim.reset()
    assert all(sim.knowledge_snapshot(a) == sim.observation_history(a) == () for a in "ABCD")
    assert sim.state is RunState.READY
    sim.run(6)
    assert tuple(sim.observation_history(a) for a in "ABCD") == expected
    assert tuple(sim.knowledge_snapshot(a) for a in "ABCD") == snapshots
    assert (sim.world, sim.event_trace) == (world, events)
    assert tuple(scenario.create_engine(with_perception=True).observation_history(a) for a in "ABCD") == ((), (), (), ())


def test_failed_run_reset_clears_partial_knowledge(scenario):
    sim = scenario.create_engine(with_perception=True)
    def fail(s):
        raise RuntimeError("test failure")
    sim.register_system("fail", Phase.RECORD, fail)
    with pytest.raises(RuntimeError):
        sim.run(1)
    assert sim.observation_history("A")
    sim.reset()
    assert sim.knowledge_snapshot("A") == () and sim.observation_history("A") == ()


def test_local_store_registration_and_boundaries(scenario):
    sim = scenario.create_engine()
    with pytest.raises(ValueError, match="unknown"):
        sim.register_local_knowledge(LocalKnowledge("missing"))
    store = LocalKnowledge("A")
    store.record(obs(), 0)
    with pytest.raises(ValueError, match="empty"):
        sim.register_local_knowledge(store)
    store.reset()
    sim.register_local_knowledge(store)
    with pytest.raises(ValueError, match="duplicate"):
        sim.register_local_knowledge(LocalKnowledge("A"))
    with pytest.raises(ValueError, match="registered"):
        sim.replace_world(replace(sim.world, agents=tuple(a for a in sim.world.agents if a.identity.agent_id != "A")))
    sim.start()
    with pytest.raises(RuntimeError):
        sim.register_local_knowledge(LocalKnowledge("B"))


def test_seed_and_registration_order_reproduce_evidence(scenario):
    first = scenario.create_engine(with_perception=True)
    reverse = replace(scenario, initial_world=replace(scenario.initial_world, agents=scenario.initial_world.agents[::-1], conditions=scenario.initial_world.conditions[::-1]))
    second = reverse.create_engine(with_perception=True)
    first.run(6)
    second.run(6)
    assert [first.observation_history(a) for a in "ABCD"] == [second.observation_history(a) for a in "ABCD"]


def test_configured_staleness_policy_and_reset_retention(scenario):
    store = LocalKnowledge("A", max_age_ticks=0)
    store.record(obs(), 0)
    assert store.stale(0) == ()
    assert store.stale(1) == store.snapshot()
    store.reset()
    assert store.max_age_ticks == 0 and store.stale(1) == ()
    sim = scenario.create_engine()
    install_local_perception(sim, PerceptionConfiguration(max_age_ticks=0))
    assert sim.knowledge_max_age("A") == 0


def test_rejected_peer_delivery_does_not_pollute_local_inbox(scenario):
    sim = scenario.create_engine(with_perception=True)
    views = []
    sim.register_controller("A", lambda v: views.append(v) or ())
    def invalid_delivery(engine):
        with pytest.raises(ValueError, match="peer observations"):
            engine.deliver_observation("A", obs(source="B"))
    sim.register_system("invalid-peer", Phase.DELIVERY, invalid_delivery)
    sim.run(1)
    assert {o.source_id for o in views[0].observations} == {"A"}
    assert {e.observation.source_id for e in views[0].knowledge} == {"A"}
