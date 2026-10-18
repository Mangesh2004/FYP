"""Trusted objective/attempt admission and read-only audit. Never ranks agents."""
from dataclasses import replace

from .models import Availability, TaskStatus
from .recovery_models import (AttemptRecord, ObjectiveAnnouncement, ObjectiveSnapshot,
    RecoveryConfiguration, RecoveryEvent, RecoveryFeedback, RecoveryRequest, RecoveryTrigger)


class RecoveryRegistry:
    def __init__(self, initial: tuple[ObjectiveAnnouncement, ...], configuration: RecoveryConfiguration):
        if not isinstance(configuration, RecoveryConfiguration) or not isinstance(initial, tuple) or any(not isinstance(a, ObjectiveAnnouncement) for a in initial):
            raise ValueError("invalid recovery setup")
        if len({a.objective_id for a in initial}) != len(initial) or any(a.generation != 1 for a in initial):
            raise ValueError("initial objectives must be unique first attempts")
        self.configuration, self._initial = configuration, initial
        self.reset()

    def reset(self):
        self.offers = {a.spec.task_id: a for a in self._initial}
        self.current = {a.objective_id: a.spec.task_id for a in self._initial}
        self._records = {a.spec.task_id: AttemptRecord(a.announcement.task, 1, None, None, None, 0, "") for a in self._initial}
        self._leases = {}
        self._trace = []

    @property
    def trace(self):
        return tuple(self._trace)

    def add_objective(self, offer, now):
        if not isinstance(offer, ObjectiveAnnouncement) or offer.generation != 1 or offer.objective_id in self.current or offer.spec.task_id in self.offers:
            raise ValueError("new objective requires fresh identity and first attempt")
        self.offers[offer.spec.task_id] = offer
        self.current[offer.objective_id] = offer.spec.task_id
        self._records[offer.spec.task_id] = AttemptRecord(offer.announcement.task, 1, None, None, None, 0, "")
        self._trace.append(RecoveryEvent(now, offer.objective_id, offer.spec.task_id, None, "objective registered", "Not globally announced to agents"))

    def renew(self, engine, agent_id, task_id, proof):
        task = next((t for t in engine.world.tasks if t.task_id == task_id), None)
        own = next(a for a in engine.world.agents if a.identity.agent_id == agent_id)
        admitted = next((f for f in engine.commit_trace if f.accepted and f.request.request_id == proof), None)
        if task_id in self.offers and task and own.task_id == task_id and own.availability is Availability.BUSY and task.owner_id == agent_id and admitted and admitted.request.task_id == task_id and admitted.request.agent_id == agent_id:
            self._leases[task_id] = engine.time.tick + self.configuration.lease_ticks

    def observe(self, engine):
        # Metrics only. This catalogue is never given to agent policies.
        for task in engine.world.tasks:
            if task.task_id not in self.offers:
                continue
            prior = self._records[task.task_id]
            commits = [f for f in engine.commit_trace if f.accepted and f.request.task_id == task.task_id]
            owner = commits[0].request.agent_id if commits else prior.previous_owner
            assigned = commits[0].applied_at if commits else prior.assigned_at
            events = [e for e in engine.execution_trace if e.task_id == task.task_id]
            ended = next((e.tick for e in events if e.action in {"completed", "failed"}), prior.ended_at)
            reason = next((e.reason for e in reversed(events) if e.action in {"completed", "failed"}), prior.reason)
            progress = max((e.progress_ticks for e in events), default=prior.progress_ticks)
            self._records[task.task_id] = AttemptRecord(task, prior.generation, owner, assigned, ended, progress, reason)
            if ended is not None and prior.ended_at is None and task.status is TaskStatus.FAILED:
                self._trace.append(RecoveryEvent(ended, self.offers[task.task_id].objective_id, task.task_id, owner, "ownership released", reason))
            if assigned is not None:
                self._leases.setdefault(task.task_id, assigned + self.configuration.lease_ticks)

    def apply(self, engine, request: RecoveryRequest):
        if not isinstance(request, RecoveryRequest):
            raise ValueError("expected a validated recovery request")
        self.observe(engine)
        now = engine.time.tick
        offer = self.offers.get(request.task_id)
        def reject(reason):
            if offer:
                self._trace.append(RecoveryEvent(now, offer.objective_id, request.task_id, request.agent_id, "recovery rejected", reason))
            return RecoveryFeedback(request, False, reason)
        if request.issued_at != now or request.agent_id not in engine.communication.agent_ids or offer is None:
            return reject("Unknown attempt/requester or timestamp mismatch")
        requester = next(a for a in engine.world.agents if a.identity.agent_id == request.agent_id)
        if requester.availability not in {Availability.AVAILABLE, Availability.BUSY} or requester.battery <= 0:
            return reject("Requester is unavailable")
        if self.current[offer.objective_id] != request.task_id:
            return reject("Stale attempt already superseded")
        if not offer.recoverable or offer.generation >= self.configuration.max_attempts:
            return reject("Objective permanently failed or retry budget exhausted")
        task = next(t for t in engine.world.tasks if t.task_id == request.task_id)
        if task.status in {TaskStatus.PENDING, TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
            return reject("Pending, completed or cancelled attempt cannot be released")
        record = self._records[request.task_id]
        proof = next((f.request.request_id for f in engine.commit_trace if f.accepted and f.request.task_id == request.task_id and f.request.agent_id == request.owner_id), None)
        if proof is None or record.previous_owner != request.owner_id or request.commit_reference not in {proof, "unobserved"}:
            return reject("Previous owner/admitted attempt proof mismatch")
        known = next((a for a in engine.known_tasks(request.agent_id) if a.task.task_id == request.task_id), None)
        if known is None:
            return reject("Requester has no explicit task announcement")
        if known.task.status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
            return reject("Requester knows a terminal objective")
        if known.task.owner_id not in {None, request.owner_id}:
            return reject("Requester reports a different known owner")
        if request.trigger is RecoveryTrigger.RELEASE:
            if request.agent_id != request.owner_id:
                return reject("Explicit release requires the owner")
        elif request.trigger is RecoveryTrigger.FAILURE:
            if task.status is not TaskStatus.FAILED:
                return reject("Failed-attempt report not corroborated")
        elif now < self._leases.get(request.task_id, now + 1):
            return reject("Authoritative lease remains live; peer silence insufficient")
        generation = offer.generation + 1
        new_id = f"{offer.objective_id}::attempt:{generation}"
        if any(t.task_id == new_id for t in engine.world.tasks):
            return reject("Fresh attempt ID collision")
        if task.status in {TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS}:
            # Physical stop/release only. No replacement candidate is examined here.
            engine._execution_adapter._fail(engine, request.owner_id, request.reason, task.task_id)
            task = next(t for t in engine.world.tasks if t.task_id == request.task_id)
            if task.status is not TaskStatus.FAILED:
                return reject("Active attempt could not be safely interrupted")
        self.observe(engine)
        # Preserve original creation/deadline (soft pressure), requirements and history.
        pending = replace(task, task_id=new_id, status=TaskStatus.PENDING, owner_id=None)
        announcement = replace(offer.announcement, task=pending, announced_at=now, version=1)
        successor = ObjectiveAnnouncement(offer.objective_id, generation, announcement,
            replace(offer.spec, task_id=new_id), offer.recoverable, task.task_id)
        engine.replace_world(replace(engine.world, tasks=(*engine.world.tasks, pending)))
        engine._execution_adapter.specs[new_id] = successor.spec
        self.offers[new_id] = successor
        self.current[offer.objective_id] = new_id
        self._records[new_id] = AttemptRecord(pending, generation, None, None, None, 0, "")
        self._trace.append(RecoveryEvent(now, offer.objective_id, task.task_id, request.owner_id, "recovery validated", request.reason))
        self._trace.append(RecoveryEvent(now, offer.objective_id, new_id, request.agent_id, "recovery available", "Fresh attempt; progress restarts; no resources refunded"))
        return RecoveryFeedback(request, True, "Recoverable objective admitted; requester must announce availability", successor)

    def snapshots(self):
        result = []
        for objective, task_id in sorted(self.current.items()):
            offer = self.offers[task_id]
            task = self._records[task_id].task
            state = ("completed" if task.status is TaskStatus.COMPLETED else
                     "permanently failed" if task.status in {TaskStatus.FAILED, TaskStatus.CANCELLED} and (not offer.recoverable or offer.generation >= self.configuration.max_attempts or task.status is TaskStatus.CANCELLED) else
                     "awaiting recovery" if task.status is TaskStatus.FAILED else
                     "recoverable / unassigned" if offer.generation > 1 and task.status is TaskStatus.PENDING else task.status.value)
            attempts = tuple(self._records[a.spec.task_id] for a in sorted(self.offers.values(), key=lambda a: a.generation) if a.objective_id == objective)
            result.append(ObjectiveSnapshot(objective, offer.recoverable, state, task_id, attempts))
        return tuple(result)
