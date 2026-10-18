"""Deterministic, actual-message allocation and controlled timeout/retry demo."""
from dataclasses import replace
from pathlib import Path

from .allocation_messages import AllocationConfiguration, TOPIC, decode_allocation
from .communication import CommunicationConfiguration
from .messages import CoordinationPayload, decode_payload
from .scenario import load_scenario
from .simulation import Phase


def build_engine(scenario, latency=1, *, lose_awards=False):
    engine = scenario.create_engine(with_suitability=True)
    engine.enable_communication(CommunicationConfiguration(latency_ticks=latency))
    engine.enable_allocation(AllocationConfiguration.for_latency(latency))
    if lose_awards:
        def loss(sim):
            sim.communication.set_loss_probability(1 if sim.time.tick == 3 else 0)
        engine.register_system("controlled-award-loss", Phase.SUBMISSION, loss)
    return engine


def replay_signature(engine):
    ids = tuple(sorted(a.identity.agent_id for a in engine.world.agents))
    return (engine.world, engine.event_trace, engine.communication.trace, engine.commit_trace,
            tuple((engine.allocation_snapshot(a), engine.allocation_history(a)) for a in ids))


def protocol_records(engine):
    for record in engine.communication.trace:
        payload = decode_payload(record.envelope)
        if isinstance(payload, CoordinationPayload) and payload.topic == TOPIC:
            yield record, decode_allocation(payload, record.envelope)


def main():
    scenario = load_scenario(Path(__file__).resolve().parents[2] / "scenarios/four_room_disaster.json")
    engine = build_engine(scenario)
    engine.run(scenario.mission.duration_ticks)
    print("Public initial mission task announcements; independent Contract Net coordinators")
    for record, message in protocol_records(engine):
        if record.action == "sent" and message.kind.value != "ROUND_CLOSED":
            print(f"t{record.tick} {record.envelope.sender_id}->{record.envelope.recipient_id} {message.kind.value} task={message.round.task_id} epoch={message.round.epoch} score={message.score}")
    print("Authoritative confirmed owners (after protocol-backed admission):")
    for task in engine.world.tasks:
        print(f"{task.task_id} status={task.status.value} owner={task.owner_id or 'unassigned'}")
    print("commit_ticks=" + str(tuple((f.request.task_id,f.applied_at) for f in engine.commit_trace if f.accepted)))
    original = replay_signature(engine)
    engine.reset()
    engine.run(scenario.mission.duration_ticks)
    equal = replay_signature(engine) == original
    assert equal
    print(f"allocation_replay_equal={equal} movement=NONE execution=NONE")
    # Explicit experiment horizon extends time only; same tasks, capabilities,
    # geometry and disaster/battery events. No assignments are scripted.
    experiment = replace(scenario, mission=replace(scenario.mission, duration_ticks=40))
    slow = build_engine(experiment, latency=2)
    slow.run(20)
    print("latency_2_commit_ticks=" + str(tuple((f.request.task_id,f.applied_at) for f in slow.commit_trace if f.accepted)))
    failed = build_engine(experiment, lose_awards=True)
    failed.run(10)
    dropped = [(r.tick,m.round.task_id,m.kind.value) for r,m in protocol_records(failed) if r.action == "dropped"]
    print(f"controlled_award_loss={dropped}")
    print("before_retry_owners=" + str(tuple((t.task_id,t.owner_id) for t in failed.world.tasks)))
    failed.run(20)
    print("after_retry_owners=" + str(tuple((t.task_id,t.owner_id) for t in failed.world.tasks)))
    print("retry_commit_ticks=" + str(tuple((f.request.task_id,f.applied_at) for f in failed.commit_trace if f.accepted)))
    recovered = replay_signature(failed)
    failed.reset()
    failed.run(30)
    assert replay_signature(failed) == recovered
    print("failure_replay_equal=True; expired reservations clear; confirmed ownership is never reassigned")


if __name__ == "__main__":
    main()
