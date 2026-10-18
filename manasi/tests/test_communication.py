from dataclasses import replace
from pathlib import Path
import random

import pytest

from coopai.communication import (AgentEndpoint, CommunicationConfiguration, Disconnection,
                                  ShareLocalChanges, SimulatedTransport)
from coopai.knowledge import LocalKnowledge
from coopai.messages import (AgentStatus, CoordinationPayload, DeliveryReceipt, ObservationPayload,
                             decode_payload, encode_payload)
from coopai.models import Availability, MessageEnvelope, MessageKind, Observation
from coopai.scenario import load_scenario
from coopai.simulation import Phase


@pytest.fixture
def scenario():
    return load_scenario(Path(__file__).resolve().parents[1] / "scenarios/four_room_disaster.json")


def observation(identity="o", tick=0, value="present", confidence=0.7):
    return Observation(identity, "A", "room-1", "fire", value, tick, confidence, "thermal")


def network(configuration=CommunicationConfiguration()):
    stores = {a: LocalKnowledge(a) for a in "ABC"}
    endpoints = {a: AgentEndpoint(stores[a], tuple(p for p in "ABC" if p != a)) for a in stores}
    transport = SimulatedTransport(tuple(endpoints.values()), configuration)
    return stores, endpoints, transport


def envelope(payload, message_id="m", sender="A", recipient="B", sent=0, expires=9):
    kind, data = encode_payload(payload)
    return MessageEnvelope(message_id, sender, recipient, kind, sent, data, expires)


def test_typed_payload_round_trips_and_status_sender():
    values = [ObservationPayload(observation(), ("A",)), AgentStatus("A", "room-1", 0.4, Availability.AVAILABLE, 0),
              CoordinationPayload("future-topic", '{"request":"hello"}')]
    for payload in values:
        assert decode_payload(envelope(payload)) == payload
    with pytest.raises(ValueError, match="status sender"):
        decode_payload(envelope(values[1], sender="C"))


@pytest.mark.parametrize("changes", [
    {"payload": '{"observation":{},"path":["A"]}'},
    {"payload": '{"topic":"x","data":[],"extra":1}', "kind": MessageKind.COORDINATION},
    {"payload": '{"topic":"x","topic":"y","data":{}}', "kind": MessageKind.COORDINATION},
    {"payload": '{"topic":"x","data":[]}', "kind": MessageKind.COORDINATION},
    {"kind": MessageKind.TASK},
    {"sender_id": "C"}, {"recipient_id": "A"},
])
def test_invalid_payloads_and_provenance(changes):
    base = envelope(ObservationPayload(observation(), ("A",)))
    with pytest.raises(ValueError):
        decode_payload(replace(base, **changes))


@pytest.mark.parametrize("path", [("B",), ("A", "B", "A"), (), ["A"]])
def test_bad_source_paths(path):
    with pytest.raises(ValueError):
        ObservationPayload(observation(), path)


def test_future_observation_and_status_timestamps():
    with pytest.raises(ValueError, match="timestamp"):
        decode_payload(envelope(ObservationPayload(observation(tick=1), ("A",))))
    with pytest.raises(ValueError, match="timestamp"):
        decode_payload(envelope(AgentStatus("A", "room-1", 1, Availability.AVAILABLE, 1)))


@pytest.mark.parametrize("configuration", [
    {"latency_ticks": 0}, {"latency_ticks": True}, {"ttl_ticks": 0},
    {"loss_probability": -0.1}, {"loss_probability": 1.1}, {"loss_probability": float("nan")},
    {"loss_probability": True}, {"links": []}, {"links": (("A", "A"),)},
    {"links": (("A", "B"), ("A", "B"))}, {"disconnections": []},
])
def test_configuration_validation(configuration):
    with pytest.raises(ValueError):
        CommunicationConfiguration(**configuration)


@pytest.mark.parametrize("start,end", [(1,1), (2,1), (-1,2), (True,2)])
def test_outage_validation(start, end):
    with pytest.raises(ValueError):
        Disconnection("A", start, end)


def test_queue_latency_receipt_provenance_and_knowledge_isolation():
    stores, endpoints, transport = network(CommunicationConfiguration(latency_ticks=2))
    obs = observation()
    stores["A"].record(obs, 0)
    msg = endpoints["A"].share_observation("o", "B", 0)
    transport.submit(0, random.Random(42))
    transport.deliver(1)
    transport.process(1)
    assert stores["B"].snapshot() == stores["C"].snapshot() == ()
    transport.deliver(2)
    assert stores["B"].snapshot() == ()  # Delivery queue and processing are separate.
    transport.process(2)
    entry = stores["B"].snapshot()[0]
    assert entry.observation is not obs and entry.observation == obs
    assert entry.is_remote and entry.received_at == 2 and entry.forwarded_by == "A"
    assert entry.message_id == msg.message_id and entry.path == ("A", "B")
    assert entry.observation.confidence == 0.7 and entry.observation.observed_at == 0
    assert stores["C"].snapshot() == ()
    assert endpoints["B"].received_this_tick[0].delivered_at == 2


def test_sender_recipient_and_evidence_content_enforcement():
    stores, endpoints, transport = network()
    stores["A"].record(observation(), 0)
    valid = envelope(ObservationPayload(observation(), ("A",)))
    for bad in (replace(valid, sender_id="C"), replace(valid, recipient_id="missing"),
                replace(valid, sent_at=1), envelope(ObservationPayload(observation(value="severe"), ("A",)))):
        with pytest.raises(ValueError):
            endpoints["A"].send(bad, 0)
    with pytest.raises(ValueError, match="unknown local"):
        endpoints["A"].share_observation("invented", "B", 0)
    assert transport.trace == () and endpoints["A"].drain_outgoing() == ()


def test_status_and_generic_coordination_are_messages_not_actions():
    stores, endpoints, transport = network()
    endpoints["A"].send_status(AgentStatus("A", "room-1", 0.4, Availability.BUSY, 0), "B", 0)
    endpoints["A"].send_coordination("reserved", '{"intent":"no-execution"}', "B", 0)
    transport.submit(0, random.Random(0))
    transport.deliver(1)
    transport.process(1)
    assert endpoints["B"].peer_statuses[0].status.battery == 0.4
    assert endpoints["B"].peer_statuses[0].received_at == 1
    assert len(endpoints["B"].received_this_tick) == 2 and stores["B"].snapshot() == ()


def test_duplicate_suppression_without_independent_corroboration():
    stores, endpoints, transport = network()
    stores["A"].record(observation(), 0)
    msg = endpoints["A"].share_observation("o", "B", 0)
    assert endpoints["A"].share_observation("o", "B", 0) is None
    assert not endpoints["A"].send(msg, 0)
    assert endpoints["A"].send(replace(msg, message_id="new-message-same-evidence"), 0)
    transport.submit(0, random.Random(1))
    transport.deliver(1)
    transport.process(1)
    assert len(stores["B"].history) == 1 and stores["B"].history[0].observation.confidence == 0.7
    assert any(r.action == "duplicate_evidence" for r in transport.trace)
    endpoints["B"].receive(DeliveryReceipt(msg, 1))
    assert endpoints["B"].process(1)[0].result == "duplicate"
    assert len(stores["B"].history) == 1
    with pytest.raises(ValueError, match="reused"):
        endpoints["A"].send(replace(msg, expires_at=8), 0)


def test_explicit_forwarding_preserves_chain_and_prevents_loops():
    stores, endpoints, transport = network()
    stores["A"].record(observation(), 0)
    endpoints["A"].share_observation("o", "B", 0)
    transport.submit(0, random.Random(0))
    transport.deliver(1)
    transport.process(1)
    endpoints["B"].share_observation("o", "C", 1)
    with pytest.raises(ValueError, match="loop"):
        endpoints["B"].share_observation("o", "A", 1)
    transport.submit(1, random.Random(0))
    transport.deliver(2)
    transport.process(2)
    entry = stores["C"].history[0]
    assert entry.path == ("A", "B", "C") and entry.forwarded_by == "B"
    assert entry.observation == observation()


def test_stale_evidence_and_conflicts_keep_original_times_and_history():
    stores, endpoints, transport = network(CommunicationConfiguration(latency_ticks=5))
    stores["A"].record(observation("old", 0), 0)
    endpoints["A"].share_observation("old", "B", 0)
    transport.submit(0, random.Random(0))
    transport.deliver(5)
    transport.process(5)
    assert stores["B"].history[0].is_stale(5, 3)
    assert stores["B"].history[0].received_at == 5
    stores["B"].record_peer(observation("new", 3, "severe", 0.2), 6, forwarded_by="A", message_id="conflict", path=("A",))
    stores["B"].record_peer(observation("older", 1, "absent", 0.9), 7, forwarded_by="A", message_id="late", path=("A",))
    assert stores["B"].snapshot()[0].observation.value == "severe"
    assert len(stores["B"].history) == 3
    assert {e.observation.value for e in stores["B"].history} == {"present", "severe", "absent"}


def test_broadcast_uses_distinct_recipient_envelopes():
    stores, endpoints, transport = network()
    stores["A"].record(observation(), 0)
    messages = endpoints["A"].broadcast_observation("o", 0)
    assert len({m.message_id for m in messages}) == 2
    assert [m.recipient_id for m in messages] == ["B", "C"]
    assert endpoints["A"].broadcast_observation("o", 0) == ()
    transport.submit(0, random.Random(0))
    transport.deliver(1)
    transport.process(1)
    assert all(len(stores[a].history) == 1 for a in "ABC")


@pytest.mark.parametrize("probability,expected", [(0, "delivered"), (1, "dropped")])
def test_loss_boundaries(probability, expected):
    stores, endpoints, transport = network(CommunicationConfiguration(loss_probability=probability))
    endpoints["A"].send_coordination("test", '{}', "B", 0)
    transport.submit(0, random.Random(1))
    transport.deliver(1)
    transport.process(1)
    assert any(r.action == expected for r in transport.trace)
    if probability:
        assert stores["B"].history == () and endpoints["B"].received_history == ()


@pytest.mark.parametrize("outage,send_tick,delivery_tick", [(Disconnection("B",0,2),0,1), (Disconnection("B",1,2),0,1), (Disconnection("A",1,2),0,1)])
def test_outages_checked_at_submission_and_delivery(outage, send_tick, delivery_tick):
    _, endpoints, transport = network(CommunicationConfiguration(disconnections=(outage,)))
    endpoints["A"].send_coordination("test", '{}', "B", send_tick)
    transport.submit(send_tick, random.Random(1))
    transport.deliver(delivery_tick)
    transport.process(delivery_tick)
    assert any(r.reason == "disconnected" for r in transport.trace)
    assert endpoints["B"].received_history == ()


def test_connection_recovery_and_directional_links():
    _, endpoints, transport = network(CommunicationConfiguration(links=(("A","B"),), disconnections=(Disconnection("B",0,1),)))
    endpoints["A"].send_coordination("hello", '{}', "B", 1)
    endpoints["B"].send_coordination("reply", '{}', "A", 1)
    transport.submit(1, random.Random(0))
    transport.deliver(2)
    transport.process(2)
    assert len(endpoints["B"].received_history) == 1
    assert any(r.reason == "unreachable" for r in transport.trace)
    assert endpoints["A"].received_history == ()


def test_expiration_at_delivery_tick_and_pending_horizon():
    _, endpoints, transport = network(CommunicationConfiguration(latency_ticks=2))
    endpoints["A"].send(envelope(CoordinationPayload("expires", '{}'), expires=2), 0)
    transport.submit(0, random.Random(0))
    assert transport.pending_count == 1
    transport.deliver(2)
    transport.process(2)
    assert transport.pending_count == 0
    assert transport.trace[-1].reason == "expired"
    assert endpoints["B"].received_history == ()


def test_message_order_is_independent_of_endpoint_registration():
    def run(reverse):
        stores = {a: LocalKnowledge(a) for a in "ABC"}
        endpoints = [AgentEndpoint(stores[a], tuple(p for p in "ABC" if p != a)) for a in "ABC"]
        transport = SimulatedTransport(tuple(endpoints[::-1] if reverse else endpoints))
        for endpoint in endpoints:
            endpoint.send_coordination("test", '{}', endpoint.peers[0], 0)
        transport.submit(0, random.Random(0))
        transport.deliver(1)
        transport.process(1)
        return transport.trace
    assert run(False) == run(True)
    assert [r.envelope.message_id for r in run(False) if r.action == "delivered"] == sorted(r.envelope.message_id for r in run(False) if r.action == "delivered")


def test_random_loss_reset_policy_and_knowledge_replay(scenario):
    def build(seed):
        alternate = replace(scenario, mission=replace(scenario.mission, seed=seed))
        sim = alternate.create_engine(with_perception=True)
        sim.enable_communication(CommunicationConfiguration(loss_probability=0.4, latency_ticks=2))
        return sim
    sim = build(42)
    sim.run(8)
    result = sim.communication.trace, tuple(sim.observation_history(a) for a in "ABCD")
    assert any(r.reason == "loss" for r in result[0]) and any(r.action == "delivered" for r in result[0])
    sim.reset()
    assert sim.communication.pending_count == 0 and sim.communication.trace == ()
    assert all(sim.observation_history(a) == () for a in "ABCD")
    sim.run(8)
    assert (sim.communication.trace, tuple(sim.observation_history(a) for a in "ABCD")) == result
    other = build(42)
    other.run(8)
    assert other.communication.trace == result[0]
    different = build(43)
    different.run(8)
    assert different.communication.trace != result[0]


def test_default_policy_avoids_repeated_forwarding_and_world_access(scenario):
    sim = scenario.create_engine(with_communication=True)
    views = []
    sim.register_controller("B", lambda view: views.append(view) or ())
    sim.run(6)
    assert views[0].knowledge and not any(e.is_remote for e in views[0].knowledge)
    assert any(e.is_remote and e.observation.subject == "fire" for e in views[1].knowledge)
    assert views[1].messages and views[1].peer_statuses
    assert all(not hasattr(v, name) for v in views for name in ("world", "transport", "communication"))
    for record in sim.communication.trace:
        if record.action == "sent" and record.envelope.kind is MessageKind.OBSERVATION:
            assert decode_payload(record.envelope).observation.source_id == record.envelope.sender_id
    sent_observations = [r for r in sim.communication.trace if r.action == "sent" and r.envelope.kind is MessageKind.OBSERVATION]
    assert len(sent_observations) == 24  # Initial 12 plus tick-3 refresh/change 12, not every tick.
    assert all(a.task_id is None for a in sim.world.agents) and all(t.owner_id is None for t in sim.world.tasks)
    endpoint = sim.communication.endpoint("B")
    assert not hasattr(endpoint, "world") and not hasattr(endpoint, "engine") and not hasattr(endpoint, "transport")


def test_stateful_policy_reset_is_registered_per_agent(scenario):
    class Policy:
        def __init__(self): self.calls = 0
        def __call__(self, view, endpoint):
            self.calls += 1
            if self.calls == 1:
                endpoint.send_coordination("first", '{}', endpoint.peers[0], view.time.tick)
        def reset(self): self.calls = 0
    policies = {}
    def factory(agent):
        policies[agent] = Policy()
        return policies[agent]
    sim = scenario.create_engine(with_perception=True)
    sim.enable_communication(policy_factory=factory)
    sim.run(2)
    trace = sim.communication.trace
    sim.reset()
    assert all(p.calls == 0 for p in policies.values())
    sim.run(2)
    assert sim.communication.trace == trace


def test_unknown_roster_and_registration_rejected(scenario):
    for config in (CommunicationConfiguration(links=(("A", "unknown"),)), CommunicationConfiguration(disconnections=(Disconnection("unknown",0,2),))):
        with pytest.raises(ValueError, match="unknown"):
            network(config)
    sim = scenario.create_engine()
    with pytest.raises(ValueError, match="knowledge"):
        sim.enable_communication()
    sim = scenario.create_engine(with_communication=True)
    with pytest.raises(ValueError, match="already"):
        sim.enable_communication()
    with pytest.raises(ValueError):
        sim.communication.set_connected("missing", False)


def test_demo_actual_outcomes_and_reset(capsys):
    from coopai.communication_demo import main
    main()
    output = capsys.readouterr().out
    assert "next_tick=2 B_fire=UNKNOWN" in output
    assert "source=A via=A observed=0 received=2" in output
    assert "dropped C->D observation disconnected" in output
    assert "dropped C->D observation loss" in output
    assert "replay_equal=True" in output


def test_agent_port_cannot_deliver_process_reset_or_send_at_another_tick(scenario):
    class Policy:
        def __call__(self, view, endpoint):
            assert endpoint.agent_id == view.own_state.identity.agent_id
            for forbidden in ("process", "receive", "reset", "drain_outgoing", "world", "engine", "transport"):
                assert not hasattr(endpoint, forbidden)
            with pytest.raises(ValueError, match="bound"):
                endpoint.send_coordination("future", '{}', endpoint.peers[0], view.time.tick + 1)
        def reset(self): pass
    sim = scenario.create_engine(with_perception=True)
    sim.enable_communication(policy_factory=lambda agent: Policy())
    sim.run(1)
    assert sim.communication.trace == ()


def test_shared_policy_instance_is_rejected(scenario):
    sim = scenario.create_engine(with_perception=True)
    common = ShareLocalChanges()
    with pytest.raises(ValueError, match="independent"):
        sim.enable_communication(policy_factory=lambda agent: common)
    assert sim.communication is None


def test_delayed_processing_keeps_delivery_time_and_newer_status():
    stores, endpoints, _ = network()
    obs = envelope(ObservationPayload(observation(), ("A",)))
    endpoints["B"].receive(DeliveryReceipt(obs, 1))
    endpoints["B"].process(4)
    assert stores["B"].history[0].received_at == 1
    old = envelope(AgentStatus("A", "room-1", 1, Availability.AVAILABLE, 0), message_id="old", sent=0)
    newer = envelope(AgentStatus("A", "room-1", 0.5, Availability.AVAILABLE, 0), message_id="newer", sent=2)
    endpoints["B"].receive(DeliveryReceipt(old, 3))
    endpoints["B"].process(4)
    endpoints["B"].receive(DeliveryReceipt(newer, 4))
    endpoints["B"].process(4)
    assert endpoints["B"].peer_statuses[0].status.battery == 0.5


def test_invalid_processing_and_changed_duplicate_preserve_knowledge():
    stores, endpoints, _ = network()
    malformed = MessageEnvelope("invalid", "A", "B", MessageKind.OBSERVATION, 0, '{}')
    endpoints["B"].receive(DeliveryReceipt(malformed, 1))
    assert endpoints["B"].process(1)[0].result == "rejected"
    assert stores["B"].history == ()
    original = envelope(ObservationPayload(observation(), ("A",)))
    endpoints["B"].receive(DeliveryReceipt(original, 1))
    endpoints["B"].process(1)
    changed = envelope(ObservationPayload(observation(value="severe"), ("A",)))
    endpoints["B"].receive(DeliveryReceipt(changed, 2))
    assert endpoints["B"].process(2)[0].result == "rejected"
    assert len(stores["B"].history) == 1 and stores["B"].history[0].observation.value == "present"


def test_runtime_disconnection_reset_and_receipt_validation():
    _, endpoints, transport = network()
    transport.set_connected("B", False)
    endpoints["A"].send_coordination("blocked", '{}', "B", 0)
    transport.submit(0, random.Random(0))
    assert transport.trace[-1].reason == "disconnected"
    transport.reset()
    endpoints["A"].send_coordination("restored", '{}', "B", 0)
    transport.submit(0, random.Random(0))
    transport.deliver(1)
    transport.process(1)
    assert endpoints["B"].received_history[0].result == "processed"
    with pytest.raises(ValueError, match="after"):
        DeliveryReceipt(envelope(CoordinationPayload("x", '{}')), 0)
    with pytest.raises(ValueError, match="another"):
        endpoints["C"].receive(DeliveryReceipt(envelope(CoordinationPayload("x", '{}')), 1))


def test_unknown_provenance_cannot_be_sent():
    stores, endpoints, _ = network()
    fake = replace(observation(), source_id="unknown")
    stores["A"].record_peer(fake, 0, forwarded_by="C", message_id="fake", path=("unknown","C"))
    with pytest.raises(ValueError, match="unknown provenance"):
        endpoints["A"].share_observation("o", "B", 0)
