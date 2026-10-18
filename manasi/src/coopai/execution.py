"""Autonomous own-assignment controller; public geometry and own views only."""
from dataclasses import asdict, replace
import json
from typing import TYPE_CHECKING

from .geometry import FloorPlan

from .allocation_messages import task_digest
from .execution_models import (TOPIC, ExecutionConfiguration, ExecutionNotice, ExecutionPhase,
                               TaskExecutionSpec, decode_notice, encode_notice)
from .interfaces import AgentCommand
from .messages import CoordinationPayload, decode_payload
from .models import Availability, TaskStatus, _id
from .navigation import plan_route


if TYPE_CHECKING:
    from .interfaces import AgentView
    from .communication import AgentCommunicationPort


class AgentExecutor:
    def __init__(self, agent_id: str, public_plan: FloorPlan, specs: tuple[TaskExecutionSpec,...],
                 configuration: ExecutionConfiguration = ExecutionConfiguration()):
        _id(agent_id)
        if not isinstance(public_plan,FloorPlan) or not isinstance(configuration,ExecutionConfiguration) or not isinstance(specs,tuple) or any(not isinstance(s,TaskExecutionSpec) for s in specs) or len({s.task_id for s in specs})!=len(specs):
            raise ValueError("invalid autonomous execution setup")
        self.agent_id, self.plan, self.configuration = agent_id, public_plan, configuration
        self.specs = {s.task_id:s for s in specs}
        self.reset()

    def reset(self) -> None:
        self.phase=ExecutionPhase.IDLE
        self._counter=0
        self._confirmed={}
        self._pending_commands=set()
        self._notices={}
        self._last_sent={}
        self._received={}

    def _command(self, view, action, task_id, payload=None):
        identity="execution-command:"+json.dumps([self.agent_id,self._counter],separators=(",",":"))
        self._counter+=1
        return AgentCommand(identity,self.agent_id,view.time.tick,action,task_id,
                            json.dumps(payload or {},sort_keys=True,separators=(",",":"),allow_nan=False))

    def _apply_notice(self, notice, view, announcements):
        known=next((a for a in view.known_tasks if a.task.task_id==notice.task_id),None)
        if known is None or task_digest(replace(known.task,status=TaskStatus.PENDING,owner_id=None))!=notice.task_base_digest:
            return
        if known.task.owner_id not in {None,notice.agent_id}:
            return
        key=notice.task_id,notice.agent_id
        prior=self._received.get(key)
        if prior and (notice.sequence,notice.issued_at)<=(prior.sequence,prior.issued_at):
            return
        if known.task.status in {TaskStatus.COMPLETED,TaskStatus.FAILED,TaskStatus.CANCELLED}:
            return
        self._received[key]=notice
        task=replace(known.task,status=notice.status,owner_id=notice.agent_id if notice.status in {TaskStatus.IN_PROGRESS,TaskStatus.COMPLETED} else None)
        if task!=known.task:
            announcements.append(replace(known,task=task,version=known.version+1,announced_at=view.time.tick))

    def communicate(self, view: "AgentView", port: "AgentCommunicationPort") -> tuple:
        """Only explicit own feedback/received P2P notices change task awareness."""
        announcements=[]
        for feedback in view.allocation_feedback:
            if feedback.accepted and feedback.request.agent_id==self.agent_id:
                self._confirmed[feedback.request.task_id]=feedback.request.request_id
        for feedback in view.execution_feedback:
            if feedback.agent_id!=self.agent_id:
                raise ValueError("foreign execution feedback")
            state=feedback.snapshot
            if feedback.task is None or state is None or feedback.task.status not in {TaskStatus.IN_PROGRESS,TaskStatus.COMPLETED,TaskStatus.FAILED,TaskStatus.CANCELLED} or not state.commit_reference:
                continue
            notice=ExecutionNotice(feedback.task.task_id,self.agent_id,feedback.task.status,state.notice_sequence,
                                   view.time.tick,state.task_base_digest,state.commit_reference,feedback.reason)
            prior=self._notices.get(notice.task_id)
            if prior is None or notice.sequence>prior.sequence:
                self._notices[notice.task_id]=notice
                self._apply_notice(notice,view,announcements)
        for receipt in sorted(view.messages,key=lambda r:(r.delivered_at,r.envelope.message_id)):
            payload=decode_payload(receipt.envelope)
            if isinstance(payload,CoordinationPayload) and payload.topic==TOPIC:
                notice=decode_notice(payload,receipt.envelope)
                self._apply_notice(notice,view,announcements)
        for task_id,notice in sorted(self._notices.items()):
            signature=notice.sequence
            previous=self._last_sent.get(task_id)
            if previous is None or previous[0]!=signature or view.time.tick-previous[1]>=self.configuration.notification_interval_ticks:
                current=replace(notice,issued_at=view.time.tick)
                for peer in port.peers if view.own_state.availability in {Availability.AVAILABLE,Availability.BUSY} and view.own_state.battery>0 else ():
                    port.send_coordination(TOPIC,encode_notice(current),peer,view.time.tick)
                self._last_sent[task_id]=signature,view.time.tick
        return tuple(announcements)

    def __call__(self, view: "AgentView") -> tuple[AgentCommand, ...]:
        if view.own_state.identity.agent_id!=self.agent_id:
            raise ValueError("execution controller owner mismatch")
        state=view.own_state
        if view.execution is not None:
            self.phase=view.execution.phase
        task_id=state.task_id
        if state.availability is not Availability.BUSY or task_id is None or task_id not in self._confirmed:
            return ()
        known=next((a for a in view.known_tasks if a.task.task_id==task_id),None)
        if known is None or known.task.owner_id!=self.agent_id or known.task.status not in {TaskStatus.ASSIGNED,TaskStatus.IN_PROGRESS}:
            return ()
        spec=self.specs.get(task_id)
        if spec is None:
            return (self._command(view,"fail",task_id,{"reason":"No public execution procedure"}),)
        physical=view.execution
        if physical is None or physical.task_id!=task_id or physical.phase is ExecutionPhase.IDLE:
            self.phase=ExecutionPhase.ASSIGNED
            self.phase=ExecutionPhase.PLANNING
            route=plan_route(self.plan,state.position,known.task.location_id)
            if route is None:
                return (self._command(view,"fail",task_id,{"reason":"Route unavailable through public topology"}),)
            return (self._command(view,"navigate",task_id,{"task_base_digest":task_digest(replace(known.task,status=TaskStatus.PENDING,owner_id=None)),
                                                          "waypoints":[asdict(p) for p in route]}),)
        if physical.phase is ExecutionPhase.ARRIVED:
            return (self._command(view,"begin",task_id),)
        if physical.phase is ExecutionPhase.EXECUTING:
            # A task report must be own direct, capability-supported, current local
            # evidence; received peer reports cannot stand in for doing the task.
            evidence=[o for o in view.observations if o.source_id==self.agent_id and o.location_id==known.task.location_id
                      and o.subject==spec.subject and o.sensing_capability==spec.observation_method and o.confidence>0]
            if not evidence:
                return (self._command(view,"fail",task_id,{"reason":"No fresh own observation for execution procedure"}),)
            selected=max(evidence,key=lambda o:(o.confidence,o.observation_id))
            action="complete" if physical.progress_ticks>=spec.duration_ticks else "work"
            return (self._command(view,action,task_id,{"observation_ids":[selected.observation_id]}),)
        return ()
