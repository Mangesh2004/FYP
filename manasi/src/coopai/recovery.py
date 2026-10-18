"""Agent-local reassessment. Receives only own views and a peer send port."""
from dataclasses import replace

from .execution_models import TOPIC as EXECUTION_TOPIC, decode_notice
from .messages import CoordinationPayload, decode_payload
from .models import Availability, TaskStatus, _id
from .recovery_models import (TOPIC, ObjectiveAnnouncement, RecoveryConfiguration,
    RecoveryEvent, RecoveryRequest, RecoveryTrigger, availability_data,
    decode_availability, decode_recovery, encode_recovery)


class AgentRecovery:
    def __init__(self, agent_id, initial: tuple[ObjectiveAnnouncement, ...],
                 configuration: RecoveryConfiguration = RecoveryConfiguration()):
        _id(agent_id)
        if not isinstance(configuration, RecoveryConfiguration) or not isinstance(initial, tuple) or any(not isinstance(a, ObjectiveAnnouncement) for a in initial):
            raise ValueError("invalid agent recovery setup")
        self.agent_id, self.configuration = agent_id, configuration
        self._initial = initial
        self.reset()

    def reset(self):
        self.offers = {a.spec.task_id: a for a in self._initial}
        self._generation = {a.objective_id: a.generation for a in self._initial}
        self._heartbeat = {}
        self._first_seen = {}
        self._retry = {}
        self._sent = {}
        self._detected = set()
        self._trace = []

    @property
    def trace(self):
        return tuple(self._trace)

    def learn(self, offer, now):
        current = self._generation.get(offer.objective_id, 0)
        if offer.generation < current:
            return False
        prior = self.offers.get(offer.spec.task_id)
        if prior is not None:
            if prior != offer:
                raise ValueError("attempt identity reused with changed availability")
            return False
        if offer.generation == current:
            raise ValueError("conflicting objective attempt generation")
        self.offers[offer.spec.task_id] = offer
        self._generation[offer.objective_id] = offer.generation
        self._trace.append(RecoveryEvent(now, offer.objective_id, offer.spec.task_id, self.agent_id,
                                         "availability learned", f"Generation {offer.generation}; explicit announcement"))
        return True

    def step(self, view, port):
        if view.own_state.identity.agent_id != self.agent_id or port.agent_id != self.agent_id:
            raise ValueError("recovery owner mismatch")
        now, cfg = view.time.tick, self.configuration
        announcements, requests, renewals, specs = [], [], [], []
        # An offline process cannot read messages, request release or send a heartbeat.
        if view.own_state.availability not in {Availability.AVAILABLE, Availability.BUSY} or view.own_state.battery <= 0:
            return (), (), (), ()
        known = {a.task.task_id: a for a in view.known_tasks}
        for receipt in sorted(view.messages, key=lambda r: (r.delivered_at, r.envelope.message_id)):
            payload = decode_payload(receipt.envelope)
            if not isinstance(payload, CoordinationPayload):
                continue
            if payload.topic == TOPIC:
                data = decode_recovery(payload, receipt.envelope)
                if data["kind"] == "availability":
                    offer = decode_availability(data["availability"])
                    predecessor = known.get(offer.predecessor_id)
                    if predecessor and predecessor.task.status is TaskStatus.COMPLETED:
                        continue
                    try:
                        learned = self.learn(offer, now)
                    except ValueError as exc:
                        self._trace.append(RecoveryEvent(now, offer.objective_id, offer.spec.task_id, self.agent_id, "availability rejected", str(exc)))
                        continue
                    if learned:
                        specs.append(offer.spec)
                        announcements.append(offer.announcement)
                        predecessor = known.get(offer.predecessor_id)
                        if predecessor and predecessor.task.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                            announcements.append(replace(predecessor, task=replace(predecessor.task, status=TaskStatus.FAILED, owner_id=None),
                                                         version=predecessor.version + 1, announced_at=now))
                else:
                    task_id, actor = data["task_id"], data["agent_id"]
                    a = known.get(task_id)
                    if a and a.task.owner_id in {actor, None} and a.task.status in {TaskStatus.PENDING, TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS}:
                        previous = self._heartbeat.get(task_id)
                        if previous is None or data["issued_at"] > previous[0]:
                            self._heartbeat[task_id] = data["issued_at"], actor, data["commit_reference"]
            elif payload.topic == EXECUTION_TOPIC:
                notice = decode_notice(payload, receipt.envelope)
                a = known.get(notice.task_id)
                if a and a.task.owner_id in {notice.agent_id, None} and notice.status is TaskStatus.FAILED:
                    self._heartbeat.setdefault(notice.task_id, (notice.issued_at, notice.agent_id, notice.commit_reference))
        for feedback in view.recovery_feedback:
            if feedback.accepted and feedback.availability and self.learn(feedback.availability, now):
                announcements.append(feedback.availability.announcement)
                specs.append(feedback.availability.spec)
                predecessor = known.get(feedback.availability.predecessor_id)
                if predecessor and predecessor.task.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                    announcements.append(replace(predecessor, task=replace(predecessor.task, status=TaskStatus.FAILED, owner_id=None),
                                                 version=predecessor.version + 1, announced_at=now))
        # Newly injected mission announcements are explicit own input, never world discovery.
        for offer in view.mission_announcements:
            if self.learn(offer, now):
                specs.append(offer.spec)
        own_task = view.own_state.task_id
        if own_task:
            proof = next((f.request.request_id for f in view.allocation_feedback if f.accepted and f.request.task_id == own_task), None)
            proof = proof or (view.execution.commit_reference if view.execution and view.execution.task_id == own_task else None)
            key = "heartbeat", own_task
            if proof and now - self._sent.get(key, -cfg.heartbeat_ticks) >= cfg.heartbeat_ticks:
                renewals.append((own_task, proof))
                for peer in port.peers:
                    port.send_coordination(TOPIC, encode_recovery("heartbeat", self.agent_id, now,
                        task_id=own_task, commit_reference=proof), peer, now)
                self._sent[key] = now
        for task_id, offer in sorted(self.offers.items()):
            if offer.generation != self._generation[offer.objective_id]:
                continue
            # Repeated/forwarded availability never downgrades an already known task.
            key = "availability", task_id
            if now - self._sent.get(key, -cfg.announcement_ticks) >= cfg.announcement_ticks:
                for peer in port.peers:
                    port.send_coordination(TOPIC, encode_recovery("availability", self.agent_id, now,
                        availability=availability_data(offer)), peer, now)
                self._sent[key] = now
                self._trace.append(RecoveryEvent(now, offer.objective_id, task_id, self.agent_id,
                                                 "availability announced", "P2P retry/forward"))
            a = known.get(task_id)
            if not a or not offer.recoverable or offer.generation >= cfg.max_attempts or a.task.status in {TaskStatus.PENDING, TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
                continue
            beat = self._heartbeat.get(task_id)
            own_failed = next((f for f in view.execution_feedback if f.task and f.task.task_id == task_id and f.task.status is TaskStatus.FAILED), None)
            if own_failed:
                owner, proof, trigger = self.agent_id, own_failed.snapshot.commit_reference, RecoveryTrigger.FAILURE
                reason = own_failed.reason
            elif a.task.status is TaskStatus.FAILED and beat:
                _, owner, proof = beat
                trigger, reason = RecoveryTrigger.FAILURE, "Received failed-attempt notice"
            else:
                if a.task.owner_id == self.agent_id:
                    continue
                self._first_seen.setdefault(task_id, now)
                last = beat[0] if beat else self._first_seen[task_id]
                if now - last < cfg.lease_ticks:
                    continue
                owner = beat[1] if beat else a.task.owner_id
                if owner is None:
                    continue
                proof = beat[2] if beat else "unobserved"
                trigger, reason = RecoveryTrigger.SILENCE, "Owner heartbeat timeout; request validated lease check"
            if task_id not in self._detected:
                self._detected.add(task_id)
                self._trace.append(RecoveryEvent(now, offer.objective_id, task_id, self.agent_id,
                                                 "failure detected", reason))
            if now - self._retry.get(task_id, -cfg.retry_ticks) >= cfg.retry_ticks:
                requests.append(RecoveryRequest(self.agent_id, task_id, owner, proof, now, trigger, reason))
                self._retry[task_id] = now
        return tuple(announcements), tuple(requests), tuple(renewals), tuple(specs)
