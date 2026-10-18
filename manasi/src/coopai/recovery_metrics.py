"""Evaluation-only measured timelines. No input to agent policies."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RecoveryMetrics:
    objective_id: str
    failure_tick: int | None
    detection_tick: int | None
    announcement_tick: int | None
    failure_announcement_tick: int | None
    ownership_release_tick: int | None
    reallocation_start_tick: int | None
    latest_assignment_tick: int | None
    replacement_agent: str | None
    replacement_assignment_tick: int | None
    movement_start_tick: int | None
    arrival_tick: int | None
    execution_restart_tick: int | None
    completion_tick: int | None
    recovery_duration_ticks: int | None
    failure_to_completion_ticks: int | None
    allocation_rounds: int
    messages_sent: int
    messages_dropped: int
    no_bids: int
    rejected_bids: int
    deadline_lateness_ticks: int | None
    agent_switching_count: int


def recovery_metrics(engine) -> tuple[RecoveryMetrics, ...]:
    results = []
    for objective in engine.recovery_snapshot():
        attempts = objective.attempts
        ids = {a.task.task_id for a in attempts}
        latest = attempts[-1].task.task_id
        audit = [e for e in engine.recovery_trace if e.objective_id == objective.objective_id]
        events = [e for e in engine.execution_trace if e.task_id in ids]
        allocation = [e for agent in engine.communication.agent_ids for e in engine.allocation_history(agent) if e.task_id in ids]
        first = lambda action: min((e.tick for e in audit if e.action == action), default=None)
        final = lambda action: min((e.tick for e in events if e.task_id == latest and e.action == action), default=None)
        failure = min((e.tick for e in events if e.action == "failed"), default=None)
        detection, completion = first("failure detected"), final("completed")
        replacements = [a for a in attempts[1:] if a.assigned_at is not None]
        replacement = replacements[-1] if replacements else None
        rounds = {e.round_id for e in allocation if e.action == "round opened"}
        recovery_rounds = [e.tick for e in allocation if e.action == "round opened" and e.task_id != attempts[0].task.task_id]
        # Count actual wire envelopes once; allocation/local audit sends are not wire messages.
        from .messages import CoordinationPayload, decode_payload
        packets = []
        for record in engine.communication.trace:
            try:
                payload = decode_payload(record.envelope)
            except ValueError:
                continue  # Malformed/rejected transport traffic cannot drive metrics or UI.
            if not isinstance(payload, CoordinationPayload):
                continue
            from .messages import parse_object
            data = parse_object(payload.data)
            task_id = (data.get("task_id") or data.get("round", {}).get("task_id") or data.get("availability", {}).get("spec", {}).get("task_id"))
            if task_id in ids:
                packets.append(record)
        failure_announcements = []
        for r in packets:
            if r.action == "sent":
                payload = decode_payload(r.envelope)
                data = parse_object(payload.data)
                if payload.topic == "execution.v1" and data.get("status") == "failed":
                    failure_announcements.append(r.tick)
        deadline = attempts[-1].task.deadline
        results.append(RecoveryMetrics(objective.objective_id, failure, detection,
            min((e.tick for e in audit if e.action == "availability announced" and e.task_id != attempts[0].task.task_id), default=None),
            min(failure_announcements, default=None), first("ownership released"), min(recovery_rounds, default=None),
            attempts[-1].assigned_at,
            replacement.previous_owner if replacement else None, replacement.assigned_at if replacement else None,
            final("movement started"), final("arrived"), final("execution started"), completion,
            completion - detection if completion is not None and detection is not None else None,
            completion - failure if completion is not None and failure is not None else None,
            len(rounds), len({r.envelope.message_id for r in packets if r.action == "sent"}),
            len({r.envelope.message_id for r in packets if r.action == "dropped"}),
            sum(e.action == "send NO_BID" for e in allocation),
            sum(e.action in {"stale bid", "stale award", "rejected award", "rejected acceptance", "commit rejected", "rejected commit"} for e in allocation),
            max(0, completion - deadline) if completion is not None and deadline is not None else None,
            sum(any(prior.accepted and prior.request.agent_id == feedback.request.agent_id
                    and prior.request.task_id != feedback.request.task_id and prior.applied_at < feedback.applied_at
                    for prior in engine.commit_trace)
                for feedback in engine.commit_trace if feedback.accepted and feedback.request.task_id in ids)))
    return tuple(results)
