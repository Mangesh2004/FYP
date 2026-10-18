"""Actual scenario diagnostics of independent self-evaluations; no allocation."""
from pathlib import Path
from .scenario import load_scenario


def main():
    scenario = load_scenario(Path(__file__).resolve().parents[2] / "scenarios/four_room_disaster.json")
    engine = scenario.create_engine(with_suitability=True, with_communication=True)
    engine.run(1)
    print("Independent agent-local results (diagnostic only; no peer/global ranking)")
    for agent in "ABCD":
        for result in engine.suitability_snapshot(agent):
            gate = result.eligibility
            score = "N/A" if result.score is None else f"{result.score:.3f}"
            print(f"agent={agent} task={result.task_id} eligible={gate.eligible} suitability={score} travel={gate.travel_ticks} reasons={gate.explanation}")
    engine.run(4)
    before = next(r for r in engine.suitability_snapshot("B") if r.task_id == "task-2")
    engine.run(1)
    after = next(r for r in engine.suitability_snapshot("B") if r.task_id == "task-2")
    print(f"B battery 0.9 -> 0.4 at tick 5: task-2 suitability {before.score:.3f} -> {after.score:.3f}; eligible={after.eligibility.eligible}")
    history = tuple(engine.suitability_history(a) for a in "ABCD")
    engine.reset()
    engine.run(6)
    equal = history == tuple(engine.suitability_history(a) for a in "ABCD")
    assert equal and all(t.owner_id is None for t in engine.world.tasks)
    print(f"self_evaluation_replay_equal={equal} assignments=NONE")


if __name__ == "__main__":
    main()
