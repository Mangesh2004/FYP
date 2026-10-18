"""Actual decentralized allocation, point movement and evidence-backed outcomes."""
from dataclasses import replace
from pathlib import Path

from .allocation_demo import replay_signature
from .models import DynamicEvent, EventKind
from .scenario import load_scenario


def signature(engine):
    return replay_signature(engine),engine.execution_trace,tuple(engine.execution_snapshot(a) for a in sorted(x.identity.agent_id for x in engine.world.agents)),tuple((a,engine.observation_history(a)) for a in sorted(x.identity.agent_id for x in engine.world.agents))


def print_timeline(engine):
    for task in engine.world.tasks:
        owner=task.owner_id or next((f.request.agent_id for f in engine.commit_trace if f.accepted and f.request.task_id==task.task_id),None)
        events=[e for e in engine.execution_trace if e.task_id==task.task_id]
        first=lambda action:next((e.tick for e in events if e.action==action),None)
        assigned=next((f.applied_at for f in engine.commit_trace if f.accepted and f.request.task_id==task.task_id),None)
        state=engine.execution_snapshot(owner) if owner else None
        print(f"{task.task_id}: owner={owner} status={task.status.value} assignment={assigned} movement_start={first('movement started')} arrival={first('arrived')} execution_start={first('execution started')} completion={first('completed')} failure={first('failed')}")
        if state and state.route:
            print("  route="+str(tuple((p.location_id,p.x,p.y) for p in state.route)))
        if state and state.outcome:
            print("  outcome="+state.outcome.reason)
    for agent in engine.world.agents:
        print(f"agent={agent.identity.agent_id} position={agent.position} battery={agent.battery:.6f} availability={agent.availability.value} commitment={agent.task_id}")


def main():
    scenario=load_scenario(Path(__file__).resolve().parents[2]/"scenarios/four_room_disaster.json")
    engine=scenario.create_engine(with_execution=True)
    engine.run(scenario.mission.duration_ticks)
    print("Public initial task announcements; actual P2P allocation; own execution commands")
    print_timeline(engine)
    print("Movement/progress events:")
    for event in engine.execution_trace:
        if event.action in {"moved","progress"}:
            print(f"t{event.tick} agent={event.agent_id} task={event.task_id} {event.action} {event.reason}")
    print("World hazards remain="+str(tuple((c.location_id,c.subject,c.value) for c in engine.world.conditions)))
    original=signature(engine)
    engine.reset();engine.run(scenario.mission.duration_ticks)
    assert signature(engine)==original
    print("execution_replay_equal=True; completion is inspection, not hazard resolution")
    failed=scenario.create_engine(with_execution=True)
    def low_battery(sim,event):
        own=next(a for a in sim.world.agents if a.identity.agent_id=="D")
        sim.update_agent(replace(own,battery=0.03,updated_at=sim.time.tick))
    failed.schedule(DynamicEvent("controlled-low-battery",9,EventKind.RESOURCES_CHANGED,"D","Controlled low-battery failure fixture"),low_battery)
    failed.run(scenario.mission.duration_ticks)
    print("Controlled failure: D battery=0.03 at tick 9, during movement")
    print_timeline(failed)
    original=signature(failed)
    failed.reset();failed.run(scenario.mission.duration_ticks)
    assert signature(failed)==original
    print("failure_replay_equal=True; failed ownership cleared; no reassignment")


if __name__=="__main__": main()
