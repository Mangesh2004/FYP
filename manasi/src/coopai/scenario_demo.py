"""Truth-level diagnostic demo: python -m coopai.scenario_demo [scenario.json]."""
import argparse
from pathlib import Path

from .scenario import Scenario, load_scenario
from .simulation import Phase


DEFAULT_SCENARIO = Path(__file__).resolve().parents[2] / "scenarios" / "four_room_disaster.json"


def run_demo(scenario: Scenario, steps: int = 8) -> None:
    print("SIMULATOR DIAGNOSTICS — private world state, not agent knowledge")
    for area in scenario.floor_plan.areas:
        print(f"location={area.location.location_id} name={area.location.label!r} type={area.area_type} bounds={area.bounds}")
    for passage in scenario.floor_plan.passages:
        print(f"passage={passage.passage_id} {passage.source}<->{passage.target or 'outside'} opening={passage.opening}")
    print(f"directed_connections={len(scenario.initial_world.environment.connections)} walls={len(scenario.floor_plan.walls())}")
    for condition in scenario.initial_world.conditions:
        print(f"condition={condition.location_id}:{condition.subject} value={condition.value}")
    for agent in scenario.initial_world.agents:
        print(f"agent={agent.identity.agent_id} spawn={agent.position.location_id} sensing={sorted(agent.capabilities.sensing)} operations={sorted(agent.capabilities.operational)} battery={agent.battery} availability={agent.availability.value}")
    details = {d.task_id: d for d in scenario.task_details}
    for task in scenario.initial_world.tasks:
        print(f"task={task.task_id} type={details[task.task_id].task_type} location={task.location_id} sensing={sorted(task.requirements.capabilities.sensing)} operations={sorted(task.requirements.capabilities.operational)} urgency={task.urgency} deadline={task.deadline} status={task.status.value} owner={task.owner_id}")
    sim = scenario.create_engine()
    rows = []

    def record(engine):
        draw = engine.random_stream("scenario-demo-diagnostics").random()
        row = (engine.time, engine.world, engine.event_trace, draw)
        rows.append(row)
        current = [r for r in engine.event_trace if r.tick == engine.time.tick]
        for item in current:
            print(f"event tick={item.tick} id={item.event.event_id} target={item.event.target_id} description={item.event.description!r}")
        fire = next((c.value for c in engine.world.conditions if c.location_id == "room-1" and c.subject == "fire"), "n/a")
        battery = next((a.battery for a in engine.world.agents if a.identity.agent_id == "B"), "n/a")
        print(f"tick={engine.time.tick} seconds={engine.time.seconds:.1f} fire={fire} B_battery={battery} draw={draw:.6f}")

    sim.register_system("diagnostics", Phase.RECORD, record)
    sim.start()
    sim.run(steps)
    expected = tuple(rows)
    sim.reset()
    rows.clear()  # This diagnostic buffer is owned by the demo.
    sim.run(steps)
    if tuple(rows) != expected:
        raise RuntimeError("replay trace differs")
    print(f"replay_equal=True seed={scenario.mission.seed} processed_ticks={len(rows)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", nargs="?", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--steps", type=int, default=8)
    args = parser.parse_args()
    if args.steps < 1:
        parser.error("--steps must be positive")
    run_demo(load_scenario(args.scenario), args.steps)


if __name__ == "__main__":
    main()
