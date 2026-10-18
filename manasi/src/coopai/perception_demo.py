"""Deterministic local perception demonstration: python -m coopai.perception_demo."""
from .scenario import load_scenario
from .scenario_demo import DEFAULT_SCENARIO


def main() -> None:
    scenario = load_scenario(DEFAULT_SCENARIO)
    engine = scenario.create_engine(with_perception=True)
    ids = sorted(a.identity.agent_id for a in engine.world.agents)
    print("Initial scenario: four rooms, local sensing only; no observations at setup.")
    engine.start()
    for _ in range(6):
        engine.step()
        for agent_id in ids:
            entries = engine.knowledge_snapshot(agent_id)
            text = "; ".join(f"{e.observation.location_id} {e.observation.subject}={e.observation.value} via {e.observation.sensing_capability} confidence={e.observation.confidence:.0%} observed={e.observation.observed_at}" for e in entries)
            print(f"next_tick={engine.time.tick} agent={agent_id}: {text or 'UNKNOWN (no permitted observations)'}")
    histories = tuple(engine.observation_history(a) for a in ids)
    engine.reset()
    if any(engine.observation_history(a) or engine.knowledge_snapshot(a) for a in ids):
        raise RuntimeError("reset retained local evidence")
    engine.run(6)
    if tuple(engine.observation_history(a) for a in ids) != histories:
        raise RuntimeError("observation replay differed")
    print(f"replay_equal=True seed={scenario.mission.seed} history_counts={dict(zip(ids, map(len, histories)))}")


if __name__ == "__main__":
    main()
