"""Trusted ownership admission. Validates one outcome; never ranks candidates."""
from dataclasses import replace

from .allocation_messages import (AllocationConfiguration, AllocationEvidence, AllocationKind as Kind,
    CommitFeedback, CommitRequest, coordinator_for, decode_allocation, task_digest)
from .eligibility import EligibilityConfiguration, HardEligibility, SelfEvaluationContext
from .messages import decode_payload
from .models import Availability, TaskStatus, _tick
from .world import WorldSnapshot


class CommitRegistry:
    """Protocol audit plus atomic simulator CAS (compare-and-set) admission.

    Benign-agent audit, not cryptographic authentication or distributed consensus.
    """
    def __init__(self, public_roster: tuple[str, ...], configuration: AllocationConfiguration):
        coordinator_for("validate-roster", 0, public_roster)
        if not isinstance(configuration, AllocationConfiguration):
            raise ValueError("invalid allocation configuration")
        self.roster, self.configuration = tuple(sorted(public_roster)), configuration
        self.reset()

    def reset(self) -> None:
        self._evidence: dict[str, AllocationEvidence] = {}
        self._outcomes: dict[str, CommitFeedback] = {}
        self._trace: list[CommitFeedback] = []

    @property
    def trace(self) -> tuple[CommitFeedback, ...]:
        return tuple(self._trace)

    def record(self, producer_id: str, evidence: AllocationEvidence) -> None:
        if producer_id not in self.roster or not isinstance(evidence, AllocationEvidence) or evidence.message.sender_id != producer_id:
            raise ValueError("invalid audit producer")
        if evidence.envelope is not None:
            if decode_allocation(decode_payload(evidence.envelope), evidence.envelope) != evidence.message:
                raise ValueError("wire/audit content mismatch")
        previous = self._evidence.get(evidence.record_id)
        if previous is not None and previous != evidence:
            raise ValueError("audit ID reused with changed content")
        self._evidence[evidence.record_id] = evidence

    def _validate(self, request, now, world, context, eligibility_configuration, processed):
        if request.issued_at != now or context.tick != now or request.agent_id != context.own_state.identity.agent_id:
            raise ValueError("commit issuer/timestamp mismatch")
        records = [self._evidence.get(identity) for identity in request.evidence_ids]
        if any(record is None for record in records):
            raise ValueError("Missing bid/award/accept/commit evidence")
        bid, award, accept, commit = records
        spec = bid.message.round
        if any(r.message.round != spec for r in records) or request.round_id != spec.round_id or request.task_id != spec.task_id:
            raise ValueError("Commit round/task evidence mismatch")
        cfg = self.configuration
        start = spec.epoch * cfg.epoch_ticks
        if spec.coordinator_id != coordinator_for(spec.task_id, spec.epoch, self.roster) or (spec.opened_at, spec.bid_deadline, spec.accept_deadline, spec.commit_deadline) != (start, start + cfg.bid_window_ticks, start + cfg.bid_window_ticks + cfg.award_window_ticks, start + cfg.bid_window_ticks + cfg.award_window_ticks + cfg.commit_window_ticks):
            raise ValueError("Invalid coordinator or round windows")
        if now >= spec.commit_deadline:
            raise ValueError("Expired commit round")
        winner, coordinator = request.agent_id, spec.coordinator_id
        if winner not in self.roster:
            raise ValueError("Unknown candidate")
        expected = ((Kind.BID, winner, coordinator, ()),
                    (Kind.PROVISIONAL_AWARD, coordinator, winner, (bid.record_id,)),
                    (Kind.ACCEPT, winner, coordinator, (bid.record_id, award.record_id)),
                    (Kind.COMMIT, coordinator, winner, (bid.record_id, award.record_id, accept.record_id)))
        for record, (kind, sender, recipient, refs) in zip(records, expected):
            msg = record.message
            if (msg.kind, msg.sender_id, record.recipient_id, msg.candidate_id, msg.references) != (kind, sender, recipient, winner, refs) or (msg.score, msg.bid_tick) != (bid.message.score, bid.message.bid_tick):
                raise ValueError("Protocol lineage/role/bid mismatch")
            if record.envelope is not None:
                received_at = processed.get(record.record_id)
                phase_end = spec.bid_deadline if kind is Kind.BID else spec.accept_deadline if kind in {Kind.PROVISIONAL_AWARD, Kind.ACCEPT} else spec.commit_deadline
                if received_at is None or not msg.issued_at < received_at <= now or received_at >= phase_end:
                    raise ValueError("Required peer evidence was not processed in its phase")
            elif sender != recipient or sender != winner or winner != coordinator:
                raise ValueError("Invalid local protocol action")
        if not bid.message.issued_at <= award.message.issued_at <= accept.message.issued_at <= commit.message.issued_at <= now or award.message.issued_at < spec.bid_deadline:
            raise ValueError("Invalid protocol chronology")
        announced = context.task(request.task_id)
        if announced.version != spec.task_version or task_digest(announced.task) != spec.task_digest:
            raise ValueError("Locally known task revision changed")
        task = next((t for t in world.tasks if t.task_id == request.task_id), None)
        if task is None or task.status is not TaskStatus.PENDING:
            raise ValueError("Authoritative task is not pending")
        if task_digest(task) != spec.task_digest:
            raise ValueError("Authoritative task revision changed")
        own = next((a for a in world.agents if a.identity.agent_id == winner), None)
        if own != context.own_state or own.availability is not Availability.AVAILABLE or own.task_id is not None:
            raise ValueError("Agent already committed/unavailable or stale own state")
        # Evaluate this applicant only. No other-agent scores, candidates or hidden
        # environmental truth are consulted. Physical assignment is not execution.
        gate = HardEligibility().check(request.task_id, context, eligibility_configuration)
        if not gate.eligible:
            raise ValueError("Current eligibility failed: " + gate.explanation)
        return task, own

    def apply(self, request: CommitRequest, now: int, world: WorldSnapshot,
              context: SelfEvaluationContext, eligibility_configuration: EligibilityConfiguration,
              processed: dict[str, int]) -> tuple[WorldSnapshot, CommitFeedback]:
        _tick(now)
        if not isinstance(request, CommitRequest) or not isinstance(world, WorldSnapshot) or not isinstance(context, SelfEvaluationContext):
            raise ValueError("invalid commit admission inputs")
        prior = self._outcomes.get(request.request_id)
        if prior:
            if prior.request != request:
                return world, CommitFeedback(request, now, False, "Request ID reused with changed content")
            # A true replay is idempotent, never creates a second assignment.
            if prior.accepted and any(t.task_id == request.task_id and t.owner_id == request.agent_id and t.status is TaskStatus.ASSIGNED for t in world.tasks):
                return world, prior
            return world, CommitFeedback(request, now, False, "Request already handled; no new state transition")
        try:
            task, own = self._validate(request, now, world, context, eligibility_configuration, processed)
            assigned = task.transition(TaskStatus.ASSIGNED, request.agent_id)
            busy = replace(own, availability=Availability.BUSY, task_id=task.task_id, updated_at=now)
            updated = replace(world, tasks=tuple(assigned if t.task_id == task.task_id else t for t in world.tasks),
                              agents=tuple(busy if a.identity.agent_id == own.identity.agent_id else a for a in world.agents))
            feedback = CommitFeedback(request, now, True, "Protocol-backed ownership applied atomically", assigned)
        except ValueError as exc:
            updated, feedback = world, CommitFeedback(request, now, False, str(exc))
        self._outcomes[request.request_id] = feedback
        self._trace.append(feedback)
        return updated, feedback
