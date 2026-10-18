"""Three reproducible fixtures, using real P2P allocation and execution."""
from dataclasses import replace
from pathlib import Path

from .execution_demo import signature as execution_signature
from .execution_models import TaskExecutionSpec
from .models import Availability, DynamicEvent, EventKind, Task, TaskRequirements, Capabilities
from .scenario import load_scenario
from .recovery_metrics import recovery_metrics


def build_demo(kind="failure", *, horizon=80):
    scenario = load_scenario(Path(__file__).resolve().parents[2] / "scenarios/four_room_disaster.json")
    scenario = replace(scenario, mission=replace(scenario.mission, duration_ticks=horizon))
    engine = scenario.create_engine(with_recovery=True)
    if kind in {"failure", "disruption"}:
        def failure(sim, event):
            own = next(a for a in sim.world.agents if a.identity.agent_id == "D")
            sim.update_agent(replace(own, battery=0.03, updated_at=sim.time.tick))
        engine.schedule(DynamicEvent("D-low-battery", 9, EventKind.RESOURCES_CHANGED, "D", "D battery falls to .03 during motion"), failure)
    if kind == "new-task":
        def discovery(sim, event):
            task = Task("urgent-inspection", "room-4", TaskRequirements(Capabilities(operational=frozenset({"exploration"})), .1),
                        severity=1, urgency=1, created_at=11, deadline=20)
            sim.introduce_objective(task, TaskExecutionSpec(task.task_id, "routine-inspection", 2, "hazard", inspection_operation="exploration"), "B")
        engine.schedule(DynamicEvent("new-urgent-task", 11, EventKind.TASK_DISCOVERED, "urgent-inspection", "Only B initially receives a new urgent inspection"), discovery)
    if kind == "disruption":
        def partition(sim, event):
            sim.communication.set_loss_probability(1)
        def restore(sim, event):
            sim.communication.set_loss_probability(0)
        engine.schedule(DynamicEvent("partition", 9, EventKind.COMMUNICATION_CHANGED, "mission", "All messages lost temporarily"), partition)
        engine.schedule(DynamicEvent("restore", 26, EventKind.COMMUNICATION_CHANGED, "mission", "Reliable transport restored"), restore)
    if kind not in {"failure", "new-task", "disruption"}:
        raise ValueError("unknown demonstration fixture")
    return scenario, engine


def signature(engine):
    return execution_signature(engine), engine.recovery_trace, engine.recovery_snapshot(), recovery_metrics(engine)


def main():
    for kind in ("failure", "new-task", "disruption"):
        _, engine = build_demo(kind)
        engine.run(engine.mission.duration_ticks)
        print(f"Scenario {kind}: actual decentralized outcomes")
        for objective in engine.recovery_snapshot():
            print(f"  {objective.objective_id}: {objective.state}; attempts=" + str(tuple((a.task.task_id, a.previous_owner, a.task.status.value) for a in objective.attempts)))
        target = "urgent-inspection" if kind == "new-task" else "task-4"
        print("  metrics=" + str(next(m for m in recovery_metrics(engine) if m.objective_id == target)))
        current = next(o.current_task_id for o in engine.recovery_snapshot() if o.objective_id == target)
        for agent in engine.communication.agent_ids:
            for negotiation in engine.allocation_snapshot(agent):
                if negotiation.round.task_id == current and negotiation.round.coordinator_id == agent:
                    print(f"  epoch={negotiation.round.epoch} coordinator={agent} received_bids=" + str(tuple((b.message.sender_id, round(b.message.score, 6)) for b in negotiation.bids)))
        for event in engine.recovery_trace:
            if event.objective_id == target and event.action in {"failure detected", "ownership released", "recovery available", "availability learned"}:
                print(f"  t{event.tick} {event.agent_id or '-'} {event.task_id} {event.action}: {event.reason}")
        original = signature(engine)
        engine.reset(); engine.run(engine.mission.duration_ticks)
        assert signature(engine) == original
        print(f"  {kind}_replay_equal=True")


if __name__ == "__main__":
    main()
