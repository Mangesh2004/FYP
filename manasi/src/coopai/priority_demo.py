"""Actual agent-local priority differences: python -m coopai.priority_demo."""
from .communication import CommunicationConfiguration
from .scenario import load_scenario
from .scenario_demo import DEFAULT_SCENARIO


def main():
    scenario = load_scenario(DEFAULT_SCENARIO)
    engine = scenario.create_engine(with_priority=True)
    engine.enable_communication(CommunicationConfiguration(latency_ticks=2, ttl_ticks=8))
    engine.start()
    for agent_id in ("A", "B"):
        print(f"mission-start {agent_id}: " + ", ".join(f"{r.task_id}={r.score:.5f} ({r.evidence_status})" for r in engine.priority_snapshot(agent_id)))
    for _ in range(8):
        engine.step()
        for agent_id in ("A", "B"):
            result = next(r for r in engine.priority_snapshot(agent_id) if r.task_id == "task-1")
            factors = {f.name: f.value for f in result.factors}
            print(f"next_tick={engine.time.tick} agent={agent_id} fire_priority={result.score:.5f} eval_tick={result.evaluation_tick} "
                  f"risk={factors['environment']:.5f} deadline={factors['deadline']:.3f} status={result.evidence_status}")
    expected = tuple(engine.priority_history(a) for a in "ABCD")
    histories = tuple(engine.observation_history(a) for a in "ABCD")
    engine.reset()
    assert all(engine.priority_snapshot(a) == () for a in "ABCD")
    engine.run(8)
    assert tuple(engine.priority_history(a) for a in "ABCD") == expected
    assert tuple(engine.observation_history(a) for a in "ABCD") == histories
    assert all(t.owner_id is None for t in engine.world.tasks)
    print("priority_replay_equal=True assignments=NONE")


if __name__ == "__main__":
    main()
