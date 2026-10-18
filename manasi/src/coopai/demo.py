"""Run with python -m coopai.demo. Output is truth-level diagnostic logging."""
from dataclasses import replace

from .models import (
    AgentIdentity, AgentState, Capabilities, Connection, DynamicEvent,
    Environment, EventKind, Location, MissionConfiguration, Position,
)
from .simulation import Phase, SimulationEngine
from .world import WorldCondition, WorldSnapshot


def build_demo(seed: int = 42) -> SimulationEngine:
    locations = tuple(Location(f"room-{i}", f"Room {i}") for i in range(1, 5))
    connections = tuple(Connection(f"room-{a}", f"room-{b}")
                        for i in range(1, 4) for a, b in ((i, i + 1), (i + 1, i)))
    agent = AgentState(AgentIdentity("A1", "Scout"), Capabilities(), Position("room-1"), 1)
    world = WorldSnapshot(Environment(locations, connections), agents=(agent,), conditions=(
        WorldCondition("room-1", "fire", "present"),
        WorldCondition("room-2", "gas", "present"),
        WorldCondition("room-3", "person", "possible"),
        WorldCondition("room-4", "hazard", "clear"),
    ))
    engine = SimulationEngine(MissionConfiguration("demo", seed, 5, 0.5), world)

    def change_condition(sim: SimulationEngine, event: DynamicEvent) -> None:
        conditions = tuple(replace(c, value="cleared") if c.location_id == event.target_id else c
                           for c in sim.world.conditions)
        sim.replace_world(replace(sim.world, conditions=conditions))
        print(f"event tick={sim.time.tick} id={event.event_id} target={event.target_id}")

    for event_id, target in (("gas-clears", "room-2"), ("fire-clears", "room-1")):
        engine.schedule(DynamicEvent(event_id, 2, EventKind.HAZARD_CHANGED, target,
                                     "Scripted diagnostic world change"), change_condition)

    def record(sim: SimulationEngine) -> None:
        draw = sim.random_stream("demo").random()
        print(f"tick={sim.time.tick} seconds={sim.time.seconds:.1f} seeded_draw={draw:.6f}")

    engine.register_system("diagnostics", Phase.RECORD, record)
    return engine


def main() -> None:
    engine = build_demo()
    engine.start()
    engine.step()
    engine.pause()
    print(f"paused at tick={engine.time.tick}")
    engine.resume()
    engine.run(10)
    print(f"state={engine.state.value} elapsed={engine.time.seconds:.1f}s")
    engine.reset()
    print("reset: replaying from tick=0")
    engine.run(5)


if __name__ == "__main__":
    main()
