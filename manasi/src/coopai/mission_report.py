"""Read-only mission diagnostics. None of these records are decision inputs."""
from dataclasses import dataclass

from .models import Availability, TaskStatus
from .simulation import RunState


@dataclass(frozen=True)
class ObjectiveReport:
    objective_id: str
    current_task_id: str
    state: str
    owner_id: str | None
    attempts: int
    progress_ticks: int
    completion_tick: int | None


@dataclass(frozen=True)
class MissionSummary:
    scenario_name: str
    run_state: str
    total_objectives: int
    completed_objectives: int
    incomplete_objectives: int
    pending_objectives: int
    permanently_failed_objectives: int
    failed_execution_attempts: int
    reassignments: int
    agent_failures: int
    final_owners: tuple[tuple[str, str | None], ...]
    final_battery: tuple[tuple[str, float], ...]
    ticks: int
    last_completion_tick: int | None
    objectives: tuple[ObjectiveReport, ...]


def mission_summary(engine, scenario_name: str | None = None) -> MissionSummary:
    snapshots = engine.recovery_snapshot()
    reports = []
    for obj in snapshots:
        latest = obj.attempts[-1]
        reports.append(ObjectiveReport(obj.objective_id, obj.current_task_id, obj.state,
            latest.task.owner_id, len(obj.attempts), latest.progress_ticks,
            latest.ended_at if latest.task.status is TaskStatus.COMPLETED else None))
    if not snapshots:
        for task in engine.world.tasks:
            events = [e for e in engine.execution_trace if e.task_id == task.task_id]
            reports.append(ObjectiveReport(task.task_id, task.task_id, task.status.value,
                task.owner_id, 1, max((e.progress_ticks for e in events), default=0),
                next((e.tick for e in events if e.action == "completed"), None)))
    reports.sort(key=lambda r: r.objective_id)
    completed = sum(r.state == "completed" for r in reports)
    failed_attempts = sum(a.task.status is TaskStatus.FAILED for o in snapshots for a in o.attempts) if snapshots else sum(t.status is TaskStatus.FAILED for t in engine.world.tasks)
    # Count actual final physical failures, not communication outages or event labels.
    failed_agents = {a.identity.agent_id for a in engine.world.agents if a.availability in {Availability.OFFLINE, Availability.FAILED}}
    return MissionSummary(scenario_name or engine.mission.mission_id, engine.state.value,
        len(reports), completed, len(reports) - completed,
        sum(r.state in {"pending", "recoverable / unassigned"} for r in reports),
        sum(r.state in {"permanently failed", "failed", "cancelled"} for r in reports), failed_attempts,
        sum(a.assigned_at is not None for o in snapshots for a in o.attempts[1:]), len(failed_agents),
        tuple((r.objective_id, r.owner_id) for r in reports),
        tuple(sorted((a.identity.agent_id, a.battery) for a in engine.world.agents)), engine.time.tick,
        max((r.completion_tick for r in reports if r.completion_tick is not None), default=None), tuple(reports))


@dataclass(frozen=True)
class MissionEvent:
    tick: int
    category: str
    agent_id: str | None
    task_id: str | None
    detail: str


def significant_events(engine) -> tuple[MissionEvent, ...]:
    """Filter existing logs; repeated heartbeat and work increments stay in inspectors."""
    result = []
    for record in engine.event_trace:
        result.append(MissionEvent(record.tick, "external event", None, record.event.target_id,
                                   record.event.description + " [simulator event; agents learn separately]"))
    initial_announcements = {}
    for agent in sorted(a.identity.agent_id for a in engine.world.agents):
        history = engine.priority_history(agent)
        if history:
            # start() records this first batch before any runtime discovery event.
            for evaluation in history[0]:
                initial_announcements.setdefault(evaluation.task_id, []).append(agent)
    for task_id, recipients in sorted(initial_announcements.items()):
        result.append(MissionEvent(0, "initial announcement", None, task_id,
                                   "Initial objective explicitly announced to " + ", ".join(recipients)))
    for agent in sorted(a.identity.agent_id for a in engine.world.agents):
        if engine.allocation_enabled:
            for e in engine.allocation_history(agent):
                if e.action in {"round opened", "send BID", "send NO_BID", "send PROVISIONAL_AWARD", "commit rejected", "rejected award"}:
                    result.append(MissionEvent(e.tick, e.action, agent, e.task_id, e.detail))
        seen_evidence = set()
        for entry in sorted(engine.observation_history(agent), key=lambda e: (e.received_at, e.observation.observation_id)):
            obs = entry.observation
            key = obs.location_id, obs.subject, obs.value
            if key in seen_evidence:
                continue
            seen_evidence.add(key)
            result.append(MissionEvent(entry.received_at, "observation received", agent, None,
                f"{obs.location_id} {obs.subject}={obs.value}; source {obs.source_id}; {'peer' if entry.is_remote else 'direct'}"))
        previous = {}
        for batch in engine.priority_history(agent):
            for evaluation in batch:
                selected = tuple(sorted({e.entry.observation.value for e in evaluation.evidence if e.disposition in {"selected", "frontier"}}))
                signature = evaluation.evidence_status, selected, evaluation.score == 0
                before = previous.get(evaluation.task_id)
                if before is None or signature != before[0]:
                    detail = f"{before[1]:.3f} -> {evaluation.score:.3f}" if before else f"Initial calculated priority {evaluation.score:.3f}"
                    result.append(MissionEvent(evaluation.evaluation_tick, "priority changed", agent, evaluation.task_id,
                                              detail + "; " + evaluation.explanation))
                previous[evaluation.task_id] = signature, evaluation.score
    for feedback in engine.commit_trace:
        if feedback.accepted:
            result.append(MissionEvent(feedback.applied_at, "ownership confirmed", feedback.request.agent_id,
                                      feedback.request.task_id, feedback.reason))
    for e in engine.execution_trace:
        if e.action in {"movement started", "arrived", "execution started", "completed", "failed"}:
            result.append(MissionEvent(e.tick, e.action, e.agent_id, e.task_id, e.reason))
    for e in engine.recovery_trace:
        if e.action in {"failure detected", "ownership released", "recovery available", "recovery validated", "availability learned"}:
            result.append(MissionEvent(e.tick, e.action, e.agent_id, e.task_id, e.reason))
    # Stable grouping within a tick; preserve recorded priority-history order for
    # repeated evaluations of the same task instead of sorting explanation text.
    return tuple(sorted(dict.fromkeys(result), key=lambda e: (e.tick, e.category, e.agent_id or "", e.task_id or "")))


def decision_explanation(engine, agent_id: str, task_id: str) -> tuple[str, ...]:
    """Selected-agent current and historical evidence. Never recompute a winner."""
    known = next((a for a in engine.known_tasks(agent_id) if a.task.task_id == task_id), None)
    if known is None:
        return (f"Task UNKNOWN to {agent_id}; simulator visibility is separate.",)
    lines = [f"{agent_id} knows {known.task.status.value}; known owner {known.task.owner_id or '-'}"]
    priority = next((r for r in engine.priority_snapshot(agent_id) if r.task_id == task_id), None)
    suitability = next((r for r in engine.suitability_snapshot(agent_id) if r.task_id == task_id), None)
    if priority:
        lines.extend((f"Priority {priority.score:.3f} (evaluation {priority.evaluation_tick})", priority.explanation))
        history = [r for batch in engine.priority_history(agent_id) for r in batch if r.task_id == task_id]
        if history:
            first = history[0]
            lines.append(f"Stored priority: first t{first.evaluation_tick} {first.score:.3f} -> t{priority.evaluation_tick} {priority.score:.3f}")
        relevant = [e.entry for e in priority.evidence if e.disposition in {"selected", "frontier"}]
        for entry in sorted(relevant, key=lambda e: (e.received_at,e.observation.observation_id), reverse=True)[:3]:
            obs = entry.observation
            lines.append(f"Evidence {obs.subject}={obs.value}: source {obs.source_id}, observed {obs.observed_at}, received {entry.received_at}, confidence {obs.confidence:.0%}")
    else:
        lines.append("Priority not evaluated yet; play or single-step.")
    if suitability:
        lines.extend((f"Eligible {suitability.eligibility.eligible} (evaluation {suitability.evaluation_tick})", suitability.eligibility.explanation))
        if suitability.score is not None:
            lines.append(f"Suitability {suitability.score:.3f}")
            lines.extend(f"{f.name}: {f.value:.3f}, weight {f.normalized_weight:.3f}" for f in suitability.factors)
    else:
        lines.append("Eligibility/suitability not evaluated yet.")
    if engine.allocation_enabled:
        decisions = [e for e in engine.allocation_history(agent_id) if e.task_id == task_id and e.action in {"send BID", "send NO_BID"}]
        if decisions:
            decision = decisions[-1]
            lines.append(f"Stored decision t{decision.tick}: {decision.action}")
            lines.append(decision.detail)
            if decision.action == "send BID":
                recorded = [r for batch in engine.suitability_history(agent_id) for r in batch
                            if r.task_id == task_id and r.evaluation_tick == decision.tick and r.score is not None]
                if recorded:
                    bid = recorded[0]
                    lines.append(f"At bid: eligible {bid.eligibility.eligible}, suitability {bid.score:.3f}")
                    lines.extend(f"At bid {f.name}: {f.value:.3f}, weight {f.normalized_weight:.3f}" for f in bid.factors)
        rounds = [r for r in engine.allocation_snapshot(agent_id) if r.round.task_id == task_id]
        if rounds:
            latest = max(rounds, key=lambda r: r.round.epoch)
            lines.append(f"Round {latest.round.epoch}; coordinator {latest.round.coordinator_id}; {latest.phase.value}")
            lines.append(f"Locally confirmed owner {latest.confirmed_owner or '-'}; {latest.reason}")
    return tuple(lines)


def verify_expected(case, engine) -> MissionSummary:
    """Post-run assertions only; no scenario encodes a winning identity."""
    summary = mission_summary(engine, case.display_name)
    if engine.state is not RunState.FINISHED:
        raise AssertionError("verification requires a completed bounded simulation run")
    expected = case.expected
    assert summary.completed_objectives == expected.completed_objectives, summary
    assert summary.incomplete_objectives == expected.incomplete_objectives, summary
    moving = {e.agent_id for e in engine.execution_trace if e.action == "moved"}
    assert len(moving) >= expected.minimum_moving_agents, moving
    if expected.requires_recovery:
        assert summary.failed_execution_attempts > 0 and summary.reassignments > 0
        assert any(len(o.attempts) > 1 and o.state == "completed" for o in engine.recovery_snapshot())
    return summary
