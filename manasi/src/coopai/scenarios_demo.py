"""Run the five review missions without rendering or comparison benchmarks."""
import argparse

from .mission_scenarios import SCENARIO_FILES, get_scenario
from .mission_report import significant_events, verify_expected
from .recovery_demo import signature as recovery_signature


def replay_signature(engine):
    return (recovery_signature(engine),
            tuple((a.identity.agent_id, engine.priority_history(a.identity.agent_id),
                   engine.suitability_history(a.identity.agent_id)) for a in engine.world.agents),
            significant_events(engine))


def run_scenario(case, *, replay: bool = True):
    engine = case.create_engine()
    print(f"\n{case.display_name} [{case.scenario_id}] seed={engine.mission.seed} horizon={engine.mission.duration_ticks}")
    print(case.description)
    for agent in engine.world.agents:
        print(f"  initial {agent.identity.agent_id}: {agent.position}; battery={agent.battery:.1%}")
    engine.run(engine.mission.duration_ticks)
    summary = verify_expected(case, engine)
    for feedback in engine.commit_trace:
        if not feedback.accepted:
            continue
        request = feedback.request
        records = [r for owner in engine.communication.agent_ids for r in engine.allocation_snapshot(owner)
                   if r.round.round_id == request.round_id and r.round.coordinator_id == owner]
        bids = tuple((b.message.sender_id, round(b.message.score,6)) for b in records[0].bids) if records else ()
        print(f"  Assignment t{feedback.applied_at}: {request.task_id} -> {request.agent_id}; received bids={bids}")
    for event in significant_events(engine):
        print(f"  t{event.tick:02} {event.category}: agent={event.agent_id or '-'} task={event.task_id or '-'} {event.detail}")
    print(f"  Summary: objectives={summary.total_objectives} completed={summary.completed_objectives} incomplete={summary.incomplete_objectives} pending={summary.pending_objectives} permanently_failed={summary.permanently_failed_objectives} failed_attempts={summary.failed_execution_attempts} reassignments={summary.reassignments} agent_failures={summary.agent_failures} ticks={summary.ticks} last_completion={summary.last_completion_tick}")
    print(f"  Final owners={summary.final_owners}")
    print("  Final battery=" + str(tuple((a, round(b,6)) for a,b in summary.final_battery)))
    print("  Final physical state=" + str(tuple((a.identity.agent_id,a.position,a.availability.value,a.task_id) for a in engine.world.agents)))
    if replay:
        before = replay_signature(engine)
        engine.reset(); engine.run(engine.mission.duration_ticks)
        assert replay_signature(engine) == before
        assert verify_expected(case, engine) == summary
        print("  deterministic_replay_equal=True")
    return engine, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--all", action="store_true")
    selection.add_argument("--scenario", choices=tuple(SCENARIO_FILES), default=None)
    args = parser.parse_args()
    for identity in SCENARIO_FILES if args.all else (args.scenario or "corridor",):
        run_scenario(get_scenario(identity))


if __name__ == "__main__":
    main()
