from dataclasses import FrozenInstanceError, replace
import pytest
from coopai.models import (
    AgentIdentity, AgentState, Availability, Capabilities, Connection,
    DynamicEvent, Environment, EventKind, Location, MessageEnvelope, MessageKind,
    MissionConfiguration, Observation, Position, ResourceLevel, Task,
    TaskRequirements, TaskStatus,
)


def agent(**changes):
    base = AgentState(AgentIdentity("A1", "Scout"), Capabilities(), Position("r1"), 1)
    return replace(base, **changes)


def message(**changes):
    base = MessageEnvelope("m1", "A1", "A2", MessageKind.STATE, 2, '{"battery":0.8}')
    return replace(base, **changes)


@pytest.mark.parametrize("battery", [0, 0.5, 1])
def test_valid_battery(battery):
    assert agent(battery=battery).battery == battery


@pytest.mark.parametrize("battery", [-0.01, 1.01, float("nan"), float("inf"), True, "1"])
def test_invalid_battery(battery):
    with pytest.raises(ValueError):
        agent(battery=battery)


@pytest.mark.parametrize("availability,task_id", [
    (Availability.BUSY, None), (Availability.AVAILABLE, "t"),
    (Availability.FAILED, "t"), (Availability.OFFLINE, "t"), ("available", None),
])
def test_commitment_invariant(availability, task_id):
    with pytest.raises(ValueError):
        agent(availability=availability, task_id=task_id)


def test_valid_busy_and_immutable_snapshot():
    busy = agent(availability=Availability.BUSY, task_id="t")
    assert busy.task_id == "t"
    with pytest.raises(FrozenInstanceError):
        busy.battery = 0.1
    assert replace(busy, battery=0.5).battery == 0.5
    assert busy.battery == 1


def test_sensing_does_not_grant_execution():
    sensor = Capabilities(sensing=frozenset({"thermal"}))
    assert sensor.meets(Capabilities())
    assert sensor.meets(Capabilities(sensing=frozenset({"thermal"})))
    assert not sensor.meets(Capabilities(operational=frozenset({"thermal"})))
    assert not sensor.meets(Capabilities(sensing=frozenset({"thermal", "gas"})))
    with pytest.raises(ValueError):
        Capabilities(sensing={"thermal"})


@pytest.mark.parametrize("amount,capacity", [(-1, 1), (2, 1), (0, -1), (float("nan"), 1)])
def test_resource_bounds(amount, capacity):
    with pytest.raises(ValueError):
        ResourceLevel("water", amount, capacity)


def test_resource_names_and_empty_capacity():
    empty = ResourceLevel("water", 0, 0)
    assert agent(resources=(empty,)).resources == (empty,)
    with pytest.raises(ValueError):
        agent(resources=(empty, empty))
    with pytest.raises(ValueError):
        TaskRequirements(resources=(empty, empty))


def test_task_full_lifecycle_and_attribution():
    pending = Task("t", "r1")
    assigned = pending.transition(TaskStatus.ASSIGNED, "A1")
    active = assigned.transition(TaskStatus.IN_PROGRESS)
    done = active.transition(TaskStatus.COMPLETED)
    assert pending.owner_id is None
    assert done.owner_id == "A1"
    assert done.status is TaskStatus.COMPLETED
    with pytest.raises(ValueError):
        done.transition(TaskStatus.PENDING)


def test_task_release_before_reassignment():
    assigned = Task("t", "r1").transition(TaskStatus.ASSIGNED, "A1")
    with pytest.raises(ValueError):
        assigned.transition(TaskStatus.IN_PROGRESS, "A2")
    released = assigned.transition(TaskStatus.PENDING)
    assert released.owner_id is None
    assert released.transition(TaskStatus.ASSIGNED, "A2").owner_id == "A2"
    assert assigned.transition(TaskStatus.FAILED).owner_id is None


@pytest.mark.parametrize("changes", [
    {"owner_id": "A1"}, {"status": TaskStatus.ASSIGNED},
    {"severity": 2}, {"urgency": float("nan")},
    {"created_at": 3, "deadline": 2}, {"created_at": True}, {"status": "pending"},
])
def test_task_snapshot_invariants(changes):
    with pytest.raises(ValueError):
        Task("t", "r1", **changes)


def test_illegal_lifecycle_and_missing_owner():
    pending = Task("t", "r1", created_at=2, deadline=2)
    for target in (TaskStatus.COMPLETED, TaskStatus.IN_PROGRESS, TaskStatus.ASSIGNED):
        with pytest.raises(ValueError):
            pending.transition(target)
    assert pending.transition(TaskStatus.CANCELLED).status is TaskStatus.CANCELLED
    with pytest.raises(ValueError):
        TaskRequirements(minimum_battery=1.1)


@pytest.mark.parametrize("confidence", [-0.1, 1.1, float("nan"), float("inf"), True])
def test_observation_confidence_bounds(confidence):
    with pytest.raises(ValueError):
        Observation("o", "A1", "r1", "fire", "present", 0, confidence, "thermal")


@pytest.mark.parametrize("confidence", [0, 1])
def test_observation_provenance(confidence):
    obs = Observation("o", "A1", "r1", "fire", "present", 3, confidence, "thermal")
    assert obs.source_id == "A1" and obs.observed_at == 3


@pytest.mark.parametrize("changes", [
    {"recipient_id": "A1"}, {"sender_id": " "}, {"sent_at": -1},
    {"expires_at": 2}, {"expires_at": 1}, {"schema_version": 2},
    {"schema_version": True}, {"kind": "state"}, {"payload": "[]"},
    {"payload": "garbage"}, {"payload": '{"x": NaN}'}, {"payload": {"x": 1}},
    {"correlation_id": ""}, {"payload": '{"nested": [1e999]}'},
])
def test_message_integrity(changes):
    with pytest.raises(ValueError):
        message(**changes)


def test_message_preserves_payload_and_correlation():
    envelope = message(expires_at=3, correlation_id="request-1")
    assert envelope.payload == '{"battery":0.8}'
    assert envelope.correlation_id == "request-1"


def test_directed_and_disconnected_topology_valid():
    env = Environment((Location("r1", "Room"), Location("r2", "Room"), Location("r3", "Isolated")), (Connection("r1", "r2", 2),))
    assert len(env.connections) == 1
    assert env.connections[0].travel_ticks == 2


@pytest.mark.parametrize("case", ["empty", "duplicate", "unknown", "duplicate-edge"])
def test_invalid_topology(case):
    locations = (Location("r1", "One"), Location("r2", "Two"))
    connections = ()
    if case == "empty":
        locations = ()
    elif case == "duplicate":
        locations = (locations[0], locations[0])
    elif case == "unknown":
        connections = (Connection("r1", "missing"),)
    else:
        connections = (Connection("r1", "r2"), Connection("r1", "r2"))
    with pytest.raises(ValueError):
        Environment(locations, connections)


@pytest.mark.parametrize("source,target,ticks", [("r1", "r1", 1), ("r1", "r2", 0), ("r1", "r2", 1.5)])
def test_invalid_connections(source, target, ticks):
    with pytest.raises(ValueError):
        Connection(source, target, ticks)


@pytest.mark.parametrize("changes", [{"seed": -1}, {"seed": True}, {"duration_ticks": 0}, {"tick_seconds": 0}, {"tick_seconds": float("inf")}, {"objectives": ["inspect"]}])
def test_mission_validation(changes):
    with pytest.raises(ValueError):
        replace(MissionConfiguration("mission", 42, 100), **changes)


def test_events_are_descriptors_and_time_is_discrete():
    event = DynamicEvent("e", 5, EventKind.AGENT_FAILED, "A1", "Simulated failure")
    assert event.tick == 5
    with pytest.raises(ValueError):
        replace(event, tick=0.5)
    with pytest.raises(ValueError):
        replace(event, kind="agent_failed")


@pytest.mark.parametrize("constructor", [
    lambda: AgentIdentity("", "Scout"), lambda: Position(" "),
    lambda: Location("r1", ""), lambda: agent(updated_at=-1),
    lambda: agent(identity="A1"), lambda: agent(resources=[]),
])
def test_invalid_identifiers_and_nested_types(constructor):
    with pytest.raises(ValueError):
        constructor()
