"""Trusted point-kinematic command adapter. Never allocates or selects tasks."""
from dataclasses import replace
from decimal import Decimal

from .allocation_messages import task_digest
from .eligibility import EligibilityConfiguration, HardEligibility, SelfEvaluationContext
from .execution_models import (ExecutionConfiguration, ExecutionEvent, ExecutionFeedback, ExecutionOutcome,
                               ExecutionPhase as Phase, ExecutionSnapshot, TaskExecutionSpec)
from .geometry import FloorPlan
from .messages import parse_object
from .models import Availability, Observation, Position, ResourceLevel, TaskStatus
from .navigation import EPSILON, distance, point_allowed, validate_route


class ExecutionAdapter:
    def __init__(self, public_plan: FloorPlan, specs: tuple[TaskExecutionSpec,...],
                 agent_ids: tuple[str,...], configuration: ExecutionConfiguration = ExecutionConfiguration()):
        if not isinstance(public_plan,FloorPlan) or not isinstance(configuration,ExecutionConfiguration) or not isinstance(specs,tuple) or any(not isinstance(s,TaskExecutionSpec) for s in specs) or len({s.task_id for s in specs})!=len(specs):
            raise ValueError("invalid execution adapter configuration")
        self.plan,self.specs,self.configuration=public_plan,{s.task_id:s for s in specs},configuration
        self.agent_ids=agent_ids
        self.reset()

    def reset(self):
        self._states={a:ExecutionSnapshot(a) for a in self.agent_ids}
        self._feedback={a:[] for a in self.agent_ids}
        self._trace=[]
        self._handled=set()

    def snapshot(self, agent_id):
        if agent_id not in self._states: raise ValueError("unknown execution owner")
        return self._states[agent_id]

    @property
    def trace(self): return tuple(self._trace)

    def feedback(self, agent_id): return tuple(self._feedback[agent_id])

    def clear_feedback(self):
        for values in self._feedback.values(): values.clear()

    def _agent(self, engine, agent_id):
        return next(a for a in engine.world.agents if a.identity.agent_id==agent_id)

    def _task(self, engine, task_id):
        return next((t for t in engine.world.tasks if t.task_id==task_id),None)

    def _proof(self, engine, agent_id, task_id):
        return next((f.request.request_id for f in engine.commit_trace if f.accepted and (f.request.agent_id,f.request.task_id)==(agent_id,task_id)),None)

    def _log(self, engine, agent_id, action, reason):
        own,state=self._agent(engine,agent_id),self.snapshot(agent_id)
        self._trace.append(ExecutionEvent(engine.time.tick,agent_id,state.task_id,action,reason,own.position,own.battery,state.progress_ticks))

    def _reply(self, engine, agent_id, action, accepted, reason, task=None):
        self._feedback[agent_id].append(ExecutionFeedback(agent_id,engine.time.tick,accepted,action,reason,task,self.snapshot(agent_id)))
        self._log(engine,agent_id,action,reason)

    def _atomic(self, engine, own, task=None):
        world=engine.world
        engine.replace_world(replace(world,agents=tuple(own if a.identity.agent_id==own.identity.agent_id else a for a in world.agents),
                                     tasks=tuple(task if task is not None and t.task_id==task.task_id else t for t in world.tasks)))

    def _initialize(self, engine, agent_id, task):
        proof=self._proof(engine,agent_id,task.task_id)
        if proof is None: raise ValueError("Movement/execution requires a protocol-confirmed assignment")
        spec=self.specs.get(task.task_id)
        if spec is None: raise ValueError("No configured execution procedure")
        previous=self.snapshot(agent_id)
        if previous.task_id!=task.task_id or previous.phase in {Phase.IDLE,Phase.COMPLETED,Phase.FAILED}:
            self._states[agent_id]=ExecutionSnapshot(agent_id,task.task_id,duration_ticks=spec.duration_ticks,
                commit_reference=proof,task_base_digest=task_digest(replace(task,status=TaskStatus.PENDING,owner_id=None)))
        return self.snapshot(agent_id)

    def _owned(self, engine, agent_id, task_id):
        own=self._agent(engine,agent_id)
        task=self._task(engine,task_id)
        if own.availability in {Availability.FAILED,Availability.OFFLINE}:
            raise ValueError("Agent disabled: "+own.availability.value)
        if task is None or own.task_id!=task_id or task.owner_id!=agent_id or task.status not in {TaskStatus.ASSIGNED,TaskStatus.IN_PROGRESS}:
            raise ValueError("Command has no own active confirmed task")
        if self._proof(engine,agent_id,task_id) is None:
            raise ValueError("Command has no admitted allocation proof")
        return own,task

    def _eligible(self, engine, own, task):
        state=self.snapshot(own.identity.agent_id)
        if task_digest(replace(task,status=TaskStatus.PENDING,owner_id=None))!=state.task_base_digest:
            raise ValueError("Task requirements/revision changed during execution")
        cfg=engine._suitability[own.identity.agent_id].eligibility_configuration
        physical_cfg=replace(cfg,minimum_battery=max(cfg.minimum_battery,self.configuration.minimum_operating_battery),battery_per_travel_tick=0)
        # The physical adapter validates its applicant only, using that owner's
        # evidence and already-consumed task quota, never other-agent decisions.
        context=SelfEvaluationContext(own,engine.time.tick,(engine._execution_announcement(task),),
                                      engine.observation_history(own.identity.agent_id),engine.world.environment,state.consumed_resources)
        gate=HardEligibility().check(task.task_id,context,physical_cfg)
        if not gate.eligible: raise ValueError("Execution eligibility failed: "+gate.explanation)
        spec=self.specs[task.task_id]
        if spec.sensing_capability and spec.sensing_capability not in own.capabilities.sensing:
            raise ValueError("Execution procedure requires "+spec.sensing_capability+" sensing")
        if spec.inspection_operation and spec.inspection_operation not in own.capabilities.operational:
            raise ValueError("Execution procedure requires "+spec.inspection_operation+" operation")
        if not point_allowed(self.plan,own.position): raise ValueError("Current position outside walkable geometry")

    def _charge(self, engine, own, task, cost):
        minimum=max(task.requirements.minimum_battery,self.configuration.minimum_operating_battery,
                    engine._suitability[own.identity.agent_id].eligibility_configuration.minimum_battery)
        remaining=Decimal(str(own.battery))-Decimal(str(cost))
        if remaining<Decimal(str(minimum)):
            raise ValueError(f"Insufficient battery for energy cost {cost:.6f}; minimum remaining {minimum:g}")
        return replace(own,battery=max(0,min(1,float(remaining))),updated_at=engine.time.tick)

    def _arrived(self, own, task):
        area=next(a for a in self.plan.areas if a.location.location_id==task.location_id)
        goal=Position(task.location_id,*area.bounds.center)
        return own.position.location_id==task.location_id and distance(own.position,goal)<=self.configuration.arrival_tolerance

    def _fail(self, engine, agent_id, reason, task_id=None):
        own=self._agent(engine,agent_id)
        state=self.snapshot(agent_id)
        task=self._task(engine,task_id or state.task_id)
        if state.phase in {Phase.COMPLETED,Phase.FAILED} and (task_id or state.task_id)==state.task_id:
            self._reply(engine,agent_id,"command rejected",False,"Execution already terminal; stale failure/command ignored")
            return
        legitimate=(task is not None and ((task.owner_id==agent_id and own.task_id==task.task_id) or state.task_id==task.task_id and state.commit_reference))
        if not legitimate:
            self._reply(engine,agent_id,"command rejected",False,reason)
            return
        if state.task_id!=task.task_id or not state.commit_reference:
            proof=self._proof(engine,agent_id,task.task_id)
            if proof is None:
                self._reply(engine,agent_id,"command rejected",False,reason)
                return
            spec=self.specs.get(task.task_id)
            state=ExecutionSnapshot(agent_id,task.task_id,duration_ticks=spec.duration_ticks if spec else 0,
                commit_reference=proof,task_base_digest=task_digest(replace(task,status=TaskStatus.PENDING,owner_id=None)))
        if task.status in {TaskStatus.ASSIGNED,TaskStatus.IN_PROGRESS} and task.owner_id==agent_id:
            task=task.transition(TaskStatus.FAILED)
        if own.task_id==task.task_id:
            availability=own.availability if own.availability in {Availability.OFFLINE,Availability.FAILED} else Availability.OFFLINE if own.battery<self.configuration.minimum_operating_battery else Availability.AVAILABLE
            own=replace(own,availability=availability,task_id=None,updated_at=engine.time.tick)
            self._atomic(engine,own,task)
        outcome=ExecutionOutcome(agent_id,task.task_id,engine.time.tick,False,reason)
        self._states[agent_id]=replace(state,phase=Phase.FAILED,outcome=outcome,reason=reason,notice_sequence=state.notice_sequence+1)
        self._reply(engine,agent_id,"failed",False,reason,task)

    def _evidence(self, engine, own, task, identities):
        if not isinstance(identities,list) or not identities or any(not isinstance(i,str) for i in identities) or len(set(identities))!=len(identities):
            raise ValueError("Execution report requires distinct own observation IDs")
        spec=self.specs[task.task_id]
        records={e.observation.observation_id:e.observation for e in engine.observation_history(own.identity.agent_id) if not e.is_remote}
        result=[]
        local=next((c for c in engine.world.conditions if c.location_id==task.location_id and c.subject==spec.subject),None)
        for identity in identities:
            obs=records.get(identity)
            if obs is None or obs.source_id!=own.identity.agent_id or obs.location_id!=task.location_id or obs.subject!=spec.subject or obs.sensing_capability!=spec.observation_method or obs.confidence<=0:
                raise ValueError("Report is not own capability-supported local evidence")
            if not 0<=engine.time.tick-obs.observed_at<=self.configuration.evidence_max_age_ticks:
                raise ValueError("Execution report observation is stale/future")
            if local is None or obs.value!=local.value:
                raise ValueError("Observed execution condition no longer satisfied; fresh sensing required")
            result.append(obs)
        return tuple(result)

    def _navigate(self, engine, own, task, data):
        if set(data)!={"task_base_digest","waypoints"} or not isinstance(data["waypoints"],list): raise ValueError("Invalid navigation command fields")
        state=self._initialize(engine,own.identity.agent_id,task)
        if state.phase is not Phase.IDLE or task.status is not TaskStatus.ASSIGNED: raise ValueError("Route already active or task not assigned")
        self._eligible(engine,own,task)
        if data["task_base_digest"]!=state.task_base_digest: raise ValueError("Route task revision mismatch")
        try:
            if any(not isinstance(p,dict) or set(p)!={"location_id","x","y"} for p in data["waypoints"]): raise ValueError("Invalid waypoint fields")
            route=tuple(Position(**p) for p in data["waypoints"])
        except TypeError as exc: raise ValueError("Invalid waypoint") from exc
        goal=Position(task.location_id,*next(a.bounds.center for a in self.plan.areas if a.location.location_id==task.location_id))
        validate_route(self.plan,engine.world.environment,own.position,route,goal)
        self._states[own.identity.agent_id]=replace(state,phase=Phase.MOVING,route=route,waypoint_index=1)
        self._reply(engine,own.identity.agent_id,"mission accepted",True,"Own validated route accepted",task)
        if self._arrived(own,task) and sum(distance(a,b) for a,b in zip(route,route[1:]))<=EPSILON:
            self._states[own.identity.agent_id]=replace(self.snapshot(own.identity.agent_id),phase=Phase.ARRIVED,waypoint_index=len(route))
            self._reply(engine,own.identity.agent_id,"arrived",True,"Already at destination",task)
        else:
            self._log(engine,own.identity.agent_id,"movement started","Following own accepted doorway route")

    def _move(self, engine, agent_id):
        own,task=self._owned(engine,agent_id,self.snapshot(agent_id).task_id)
        self._eligible(engine,own,task)
        state=self.snapshot(agent_id)
        remaining=(own.position,*state.route[state.waypoint_index:])
        validate_route(self.plan,engine.world.environment,own.position,remaining,state.route[-1])
        budget=self.configuration.speed_units_per_tick
        cursor=state.waypoint_index
        position=own.position
        moved=0.0
        while cursor<len(state.route):
            point=state.route[cursor]
            length=distance(position,point)
            if length<=EPSILON:
                position=point; cursor+=1
                continue
            if budget<=0: break
            step=min(length,budget)
            ratio=step/length
            position=point if step==length else Position(position.location_id,position.x+(point.x-position.x)*ratio,position.y+(point.y-position.y)*ratio)
            moved+=step; budget-=step
            if step==length: cursor+=1
            else: break
        charged=self._charge(engine,own,task,moved*self.configuration.movement_energy_per_unit)
        updated=replace(charged,position=position)
        self._atomic(engine,updated)
        self._states[agent_id]=replace(state,waypoint_index=cursor)
        self._log(engine,agent_id,"moved",f"Distance {moved:.6f}; location {position.location_id}")
        if cursor==len(state.route):
            if not self._arrived(updated,task): raise ValueError("Route ended without valid arrival")
            self._states[agent_id]=replace(self.snapshot(agent_id),phase=Phase.ARRIVED)
            self._reply(engine,agent_id,"arrived",True,"Destination reached through validated doors",task)

    def _command(self, engine, command):
        if command.command_id in self._handled:
            self._reply(engine,command.agent_id,"command rejected",False,"Duplicate execution command")
            return
        self._handled.add(command.command_id)
        agent_id=command.agent_id
        previous=self.snapshot(agent_id)
        if previous.phase in {Phase.COMPLETED,Phase.FAILED} and command.target_id==previous.task_id:
            self._reply(engine,agent_id,"command rejected",False,"Execution already terminal; stale command ignored")
            return
        try:
            own,task=self._owned(engine,agent_id,command.target_id)
            data=parse_object(command.payload)
            if command.action=="navigate":
                self._navigate(engine,own,task,data)
                return
            if command.action=="fail":
                if set(data)!={"reason"} or not isinstance(data["reason"],str) or not data["reason"].strip(): raise ValueError("Invalid failure reason")
                self._fail(engine,agent_id,data["reason"],task.task_id)
                return
            state=self.snapshot(agent_id)
            if state.task_id!=task.task_id or not state.commit_reference: raise ValueError("Mission not accepted before execution")
            self._eligible(engine,own,task)
            if not self._arrived(own,task): raise ValueError("Task execution requires valid destination arrival")
            if command.action=="begin":
                if data or state.phase is not Phase.ARRIVED or task.status is not TaskStatus.ASSIGNED: raise ValueError("Invalid execution-start transition")
                demands={r.name:r.amount for r in task.requirements.resources}
                consumed=tuple(ResourceLevel(r.name,demands[r.name],r.capacity) for r in own.resources if demands.get(r.name,0)>0)
                resources=tuple(replace(r,amount=r.amount-demands.get(r.name,0)) for r in own.resources)
                task=task.transition(TaskStatus.IN_PROGRESS)
                self._atomic(engine,replace(own,resources=resources,updated_at=engine.time.tick),task)
                self._states[agent_id]=replace(state,phase=Phase.EXECUTING,consumed_resources=consumed,notice_sequence=state.notice_sequence+1)
                self._reply(engine,agent_id,"execution started",True,"Resources consumed once; valid-work duration begins next tick",task)
                return
            if command.action not in {"work","complete"} or set(data)!={"observation_ids"} or state.phase is not Phase.EXECUTING or task.status is not TaskStatus.IN_PROGRESS:
                raise ValueError("Invalid execution action/state")
            findings=self._evidence(engine,own,task,data["observation_ids"])
            if command.action=="work":
                if state.progress_ticks>=state.duration_ticks: raise ValueError("Duration already reached; completion report required")
                charged=self._charge(engine,own,task,self.configuration.execution_energy_per_tick)
                self._atomic(engine,charged)
                self._states[agent_id]=replace(state,progress_ticks=state.progress_ticks+1)
                self._reply(engine,agent_id,"progress",True,f"Valid work {state.progress_ticks+1}/{state.duration_ticks}")
            else:
                if state.progress_ticks!=state.duration_ticks: raise ValueError("Execution duration not satisfied")
                task=task.transition(TaskStatus.COMPLETED)
                self._atomic(engine,replace(own,availability=Availability.AVAILABLE,task_id=None,updated_at=engine.time.tick),task)
                reason="Inspection reported "+", ".join(o.subject+"="+o.value for o in findings)+"; hazards unchanged"
                outcome=ExecutionOutcome(agent_id,task.task_id,engine.time.tick,True,reason,findings)
                self._states[agent_id]=replace(state,phase=Phase.COMPLETED,outcome=outcome,reason=reason,notice_sequence=state.notice_sequence+1)
                self._reply(engine,agent_id,"completed",True,reason,task)
        except ValueError as exc:
            self._fail(engine,agent_id,str(exc),command.target_id)

    def advance(self, engine):
        # Commands issued in tick t become physical effects in tick t+1.
        commands=sorted(engine.take_commands(),key=lambda c:(c.agent_id,c.command_id))
        for agent_id in sorted({c.agent_id for c in commands}):
            own_commands=[c for c in commands if c.agent_id==agent_id]
            if len(own_commands)>1:
                self._handled.update(c.command_id for c in own_commands)
                self._fail(engine,agent_id,"Only one execution command per simulation tick is permitted",own_commands[0].target_id)
            else:
                self._command(engine,own_commands[0])
        for agent_id,state in sorted(self._states.items()):
            if state.phase not in {Phase.MOVING,Phase.ARRIVED,Phase.EXECUTING}: continue
            try:
                own,task=self._owned(engine,agent_id,state.task_id)
                self._eligible(engine,own,task)
                if state.phase is Phase.MOVING: self._move(engine,agent_id)
            except ValueError as exc:
                self._fail(engine,agent_id,str(exc))

    def inspect(self, engine):
        """Operational exploration inspection, explicit modality; no sensor invented.

        Runs during PERCEPTION only for the owner's active routine procedure.
        """
        for agent_id,state in sorted(self._states.items()):
            if state.phase is not Phase.EXECUTING: continue
            own=self._agent(engine,agent_id)
            spec=self.specs[state.task_id]
            if spec.inspection_operation is None or spec.inspection_operation not in own.capabilities.operational or own.availability is not Availability.BUSY or own.battery<self.configuration.minimum_operating_battery: continue
            task=self._task(engine,state.task_id)
            if task is None or task.status is not TaskStatus.IN_PROGRESS or not self._arrived(own,task): continue
            condition=next((c for c in engine.world.conditions if c.location_id==own.position.location_id and c.subject==spec.subject),None)
            if condition is not None:
                identity=f"inspection:{agent_id}:{state.task_id}:{engine.time.tick}"
                observation=Observation(identity,agent_id,condition.location_id,condition.subject,condition.value,engine.time.tick,
                                        self.configuration.inspection_confidence,spec.observation_method)
                engine.deliver_observation(agent_id,observation)
