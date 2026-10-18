"""Recovery safety, real wire negotiation, execution, audit and replay."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from coopai.allocation_messages import AllocationKind, decode_allocation
from coopai.execution_models import ExecutionPhase, TaskExecutionSpec
from coopai.interfaces import AgentCommand
from coopai.messages import CoordinationPayload, DeliveryReceipt, decode_payload, encode_payload
from coopai.models import Availability, Capabilities, DynamicEvent, EventKind, MessageEnvelope, Task, TaskStatus
from coopai.recovery_demo import build_demo, signature
from coopai.recovery_models import (TOPIC, ObjectiveAnnouncement, RecoveryConfiguration, RecoveryRequest,
    RecoveryTrigger, availability_data, decode_availability, decode_recovery, encode_recovery)
from coopai.recovery_metrics import recovery_metrics
from coopai.simulation import Phase, RunState
from coopai.visualization import capture_dashboard


@pytest.fixture(scope="module")
def recovered():
    scenario, engine = build_demo(horizon=45)
    engine.run(45)
    return scenario, engine


def objective(engine, identity="task-4"):
    return next(o for o in engine.recovery_snapshot() if o.objective_id == identity)


def task(engine, identity):
    return next(t for t in engine.world.tasks if t.task_id == identity)


def request(engine, *, owner="D", issuer="A", trigger=RecoveryTrigger.SILENCE):
    proof = next(f.request.request_id for f in engine.commit_trace if f.accepted and f.request.task_id == "task-4")
    return RecoveryRequest(issuer, "task-4", owner, proof, engine.time.tick, trigger, "test release")


def apply_inside(engine, callback):
    # Admission is trusted infrastructure; edits occur only in the next step.
    engine.register_system("test-admission", Phase.SUBMISSION, callback)


@pytest.mark.parametrize("change", [
    {"heartbeat_ticks":0}, {"heartbeat_ticks":True}, {"lease_ticks":-1},
    {"lease_ticks":2}, {"retry_ticks":0}, {"announcement_ticks":0},
    {"max_attempts":0}, {"max_attempts":1.5}, {"retry_ticks":float("nan")},
])
def test_recovery_configuration_rejects_invalid_boundaries(change):
    with pytest.raises(ValueError): RecoveryConfiguration(**change)


def test_attempts_preserve_failure_and_assignment_history(recovered):
    _, engine = recovered
    obj = objective(engine)
    assert obj.state == "completed" and len(obj.attempts) == 2
    failed, completed = obj.attempts
    assert failed.task.status is TaskStatus.FAILED and failed.task.owner_id is None
    assert failed.previous_owner == "D" and failed.assigned_at == 6 and failed.ended_at == 9
    assert failed.progress_ticks == 0 and "Battery" in failed.reason
    assert completed.task.task_id != failed.task.task_id
    assert completed.previous_owner == "A" and completed.task.status is TaskStatus.COMPLETED
    assert failed.task.requirements == completed.task.requirements
    with pytest.raises(ValueError): failed.task.transition(TaskStatus.PENDING)


def test_actual_detection_and_physical_release_are_distinct(recovered):
    _, engine = recovered
    metric = next(m for m in recovery_metrics(engine) if m.objective_id == "task-4")
    assert metric.failure_tick == metric.ownership_release_tick == 9
    assert metric.detection_tick == 15 and metric.announcement_tick == 16
    assert metric.failure_announcement_tick is None  # D cannot communicate while offline.
    assert metric.reallocation_start_tick == 20
    assert metric.replacement_agent == "A" and metric.replacement_assignment_tick == 23
    assert metric.movement_start_tick == 25 and metric.arrival_tick == 31
    assert metric.execution_restart_tick == 32 and metric.completion_tick == 35
    assert metric.recovery_duration_ticks == 20 and metric.failure_to_completion_ticks == 26
    assert metric.allocation_rounds == 2 and metric.agent_switching_count == 1
    assert metric.messages_sent > 0 and metric.messages_dropped == 0
    assert metric.deadline_lateness_ticks is None


def test_replacement_selected_from_real_received_bids(recovered):
    _, engine = recovered
    current = objective(engine).current_task_id
    rounds = [r for r in engine.allocation_snapshot("A") if r.round.task_id == current]
    bids = rounds[0].bids
    assert {b.message.sender_id for b in bids} == {"A", "B", "C"}
    assert max(bids, key=lambda b: b.message.score).message.sender_id == "A"
    assert all(b.message.round.task_id != "task-4" for b in bids)
    assert all(b.envelope is not None for b in bids if b.message.sender_id != "A")
    award = next(e for e in engine.allocation_history("A") if e.task_id == current and e.action == "send PROVISIONAL_AWARD")
    assert "candidate A" in award.detail


def test_replacement_navigation_and_evidence_backed_execution(recovered):
    _, engine = recovered
    current = objective(engine).current_task_id
    state = engine.execution_snapshot("A")
    assert state.task_id == current and state.phase is ExecutionPhase.COMPLETED
    assert {p.location_id for p in state.route} == {"room-1", "corridor", "room-4"}
    assert state.outcome.success and state.progress_ticks == state.duration_ticks == 2
    assert all(o.source_id == "A" and o.location_id == "room-4" for o in state.outcome.findings)
    assert state.outcome.findings[0].sensing_capability == "operational:exploration"
    assert next(a for a in engine.world.agents if a.identity.agent_id == "D").availability is Availability.OFFLINE


def test_offline_owner_has_no_post_failure_wire_messages(recovered):
    _, engine = recovered
    assert not any(r.action == "sent" and r.envelope.sender_id == "D" and r.tick >= 9 for r in engine.communication.trace)
    assert not any(e.agent_id == "D" and e.action == "failure detected" for e in engine.recovery_trace)


def test_one_active_commitment_per_agent_and_one_attempt_per_objective_every_tick():
    _, engine = build_demo(horizon=40)
    for _ in range(40):
        engine.run(1)
        active = [t for t in engine.world.tasks if t.status in {TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS}]
        assert len({t.owner_id for t in active}) == len(active)
        assert len([t for t in active if t.task_id.startswith("task-4")]) <= 1
        for agent in engine.world.agents:
            owned = [t.task_id for t in active if t.owner_id == agent.identity.agent_id]
            assert owned == ([agent.task_id] if agent.task_id else [])


def test_permanent_objective_is_not_reopened():
    scenario, _ = build_demo()
    engine = scenario.create_engine(with_execution=True)
    engine.enable_recovery(recoverable_objectives=frozenset({"task-1", "task-2", "task-3"}))
    def fail(sim, event):
        own = next(a for a in sim.world.agents if a.identity.agent_id == "D")
        sim.update_agent(replace(own, battery=.03, updated_at=9))
    engine.schedule(DynamicEvent("fail", 9, EventKind.RESOURCES_CHANGED, "D", "battery"), fail)
    engine.run(40)
    assert objective(engine).state == "permanently failed" and len(objective(engine).attempts) == 1
    assert not any(e.objective_id == "task-4" and e.action == "recovery available" for e in engine.recovery_trace)


def test_retry_budget_marks_failure_permanent():
    scenario, _ = build_demo()
    engine = scenario.create_engine(with_execution=True)
    engine.enable_recovery(RecoveryConfiguration(max_attempts=1))
    def fail(sim, event):
        own = next(a for a in sim.world.agents if a.identity.agent_id == "D")
        sim.update_agent(replace(own, battery=.03, updated_at=9))
    engine.schedule(DynamicEvent("fail", 9, EventKind.RESOURCES_CHANGED, "D", "battery"), fail)
    engine.run(40)
    assert objective(engine).state == "permanently failed" and len(objective(engine).attempts) == 1


def test_no_eligible_replacement_leaves_explicit_unassigned_attempt():
    _, engine = build_demo(horizon=45)
    def lose_capability(sim, event):
        for owner in "ABC":
            own = next(a for a in sim.world.agents if a.identity.agent_id == owner)
            sim.update_agent(replace(own, capabilities=Capabilities(own.capabilities.sensing), updated_at=14))
    engine.schedule(DynamicEvent("no-explorers",14,EventKind.RESOURCES_CHANGED,"mission","No replacement operations"),lose_capability)
    engine.run(45)
    obj = objective(engine)
    assert obj.state == "recoverable / unassigned" and obj.attempts[-1].task.owner_id is None
    assert all(f.request.task_id == "task-4" for f in engine.commit_trace if f.request.task_id.startswith("task-4"))
    assert any(e.action == "round closed" and "No eligible" in e.detail for a in "ABC" for e in engine.allocation_history(a))
    assert next(m for m in recovery_metrics(engine) if m.objective_id=="task-4").completion_tick is None


def test_completed_objective_recovery_request_rejected():
    _, engine = build_demo("new-task", horizon=45)
    results=[]
    def attempt(sim):
        if sim.time.tick == 16:
            before = sim.world
            results.append(sim._recovery_registry.apply(sim, request(sim, owner="D", issuer="A")))
            assert sim.world is before
    apply_inside(engine, attempt)
    engine.run(40)
    assert not results[0].accepted and "completed" in results[0].reason
    assert len(objective(engine).attempts) == 1


def test_duplicate_and_conflicting_release_cannot_create_second_successor():
    _, engine = build_demo(horizon=40)
    responses=[]
    def attempt(sim):
        if sim.time.tick == 15:
            req=request(sim)
            responses.append(sim._recovery_registry.apply(sim,req))
            responses.append(sim._recovery_registry.apply(sim,req))
            responses.append(sim._recovery_registry.apply(sim,replace(req,agent_id="B")))
    apply_inside(engine, attempt)
    engine.run(40)
    # Trusted systems run before pending policy admissions in SUBMISSION.
    assert responses[0].accepted
    assert all(not r.accepted and "superseded" in r.reason for r in responses[1:])
    assert len(objective(engine).attempts) == 2


def test_old_commit_replay_and_late_commands_cannot_restore_failed_owner():
    _, engine=build_demo(horizon=45)
    records=[]
    def stale(sim):
        if sim.time.tick==26:
            old=next(f for f in sim.commit_trace if f.accepted and f.request.task_id=="task-4")
            from coopai.eligibility import SelfEvaluationContext
            own=next(a for a in sim.world.agents if a.identity.agent_id=="D")
            context=SelfEvaluationContext(own,26,sim.known_tasks("D"),sim.observation_history("D"),sim.world.environment)
            world,feedback=sim._commit_registry.apply(old.request,26,sim.world,context,sim._suitability["D"].eligibility_configuration,{})
            assert world is sim.world
            records.append(feedback)
            sim._execution_adapter._command(sim,AgentCommand("late-D", "D",25,"begin","task-4"))
    engine.register_system("stale",Phase.EXECUTION,stale)
    engine.run(45)
    assert not records[0].accepted
    assert task(engine,"task-4").status is TaskStatus.FAILED
    assert objective(engine).state=="completed" and objective(engine).attempts[-1].previous_owner=="A"


def test_reconnection_does_not_reclaim_previous_attempt():
    _,engine=build_demo(horizon=50)
    def reconnect(sim,event):
        own=next(a for a in sim.world.agents if a.identity.agent_id=="D")
        sim.update_agent(replace(own,battery=1,availability=Availability.AVAILABLE,updated_at=27))
    engine.schedule(DynamicEvent("reconnect",27,EventKind.AGENT_RECOVERED,"D","D recovers"),reconnect)
    engine.run(50)
    assert objective(engine).state=="completed" and len(objective(engine).attempts)==2
    assert task(engine,"task-4").status is TaskStatus.FAILED
    assert next(a for a in engine.world.agents if a.identity.agent_id=="D").task_id is None
    assert any(a.task.task_id==objective(engine).current_task_id for a in engine.known_tasks("D"))


def test_live_owner_partition_cannot_be_released_by_silence():
    scenario,_=build_demo()
    engine=scenario.create_engine(with_recovery=True)
    def loss(sim,event): sim.communication.set_loss_probability(1)
    # D is deliberately slow but operational and renewing its own lease.
    engine._execution_adapter.configuration=replace(engine._execution_adapter.configuration,speed_units_per_tick=.15)
    for actor in engine._executors.values(): actor.configuration=engine._execution_adapter.configuration
    engine.schedule(DynamicEvent("lost",9,EventKind.COMMUNICATION_CHANGED,"mission","partition"),loss)
    engine.run(28)
    assert task(engine,"task-4").owner_id=="D" and len(objective(engine).attempts)==1
    assert any(e.action=="recovery rejected" and "lease remains live" in e.reason for e in engine.recovery_trace)


def test_explicit_owner_release_restarts_and_preserves_interrupted_work():
    scenario,_=build_demo()
    engine=scenario.create_engine(with_recovery=True)
    def release(sim,event): sim.request_task_release("D", "Operator interrupted reversible inspection")
    engine.schedule(DynamicEvent("release",13,EventKind.AGENT_FAILED,"D","Explicit task release"),release)
    engine.run(50)
    obj=objective(engine)
    assert obj.state=="completed" and len(obj.attempts)==2
    assert obj.attempts[0].progress_ticks==1 and obj.attempts[0].task.status is TaskStatus.FAILED
    assert obj.attempts[1].progress_ticks==2  # Restart, not inherited progress.
    assert "interrupted" in obj.attempts[0].reason


def test_lost_failure_notices_still_recover_from_bounded_silence():
    scenario,_=build_demo()
    engine=scenario.create_engine(with_recovery=True)
    def stop(sim,event):
        sim.communication.set_loss_probability(1)
        sim.request_task_release("D", "Communication-loss release fixture")
    def restore(sim,event): sim.communication.set_loss_probability(0)
    engine.schedule(DynamicEvent("stop",9,EventKind.AGENT_FAILED,"D","Release while messages lost"),stop)
    engine.schedule(DynamicEvent("restore",26,EventKind.COMMUNICATION_CHANGED,"mission","Restore"),restore)
    engine.run(70)
    assert objective(engine).state=="completed"
    assert any(r.action=="dropped" and 'execution.v1' in r.envelope.payload and 'failed' in r.envelope.payload for r in engine.communication.trace)
    assert len(objective(engine).attempts)==2


def test_disruption_demo_has_actual_drops_and_safe_completion():
    _,engine=build_demo("disruption",horizon=45)
    engine.run(45)
    assert objective(engine).state=="completed"
    metric=next(m for m in recovery_metrics(engine) if m.objective_id=="task-4")
    assert metric.messages_dropped>0 and metric.allocation_rounds>=2
    assert any(e.action=="availability learned" and e.tick>26 for e in engine.recovery_trace)


def test_unknown_new_task_appears_only_after_explicit_announcement():
    _,engine=build_demo("new-task",horizon=45)
    views={a:[] for a in "ABCD"}
    for a in views: engine.register_controller(a,lambda v,a=a: views[a].append(v) or ())
    engine.run(45)
    aware=lambda v:any(a.task.task_id=="urgent-inspection" for a in v.known_tasks)
    assert all(not aware(views[a][10]) for a in "ABCD")
    assert aware(views["B"][11]) and all(not aware(views[a][11]) for a in "ACD")
    assert all(aware(views[a][12]) for a in "ABCD")
    assert all(not hasattr(views[a][12], "world") and not hasattr(views[a][12], "recovery_trace") for a in "ABCD")
    assert task(engine,"urgent-inspection").status is TaskStatus.COMPLETED
    assert objective(engine,"urgent-inspection").attempts[-1].previous_owner=="D"
    priority=next(r for r in views["B"][11].priorities if r.task_id=="urgent-inspection")
    old=next(r for r in views["B"][11].priorities if r.task_id=="task-4")
    assert priority.score>old.score
    assert next(r for r in views["B"][11].suitability if r.task_id=="urgent-inspection").eligibility.eligible
    metric=next(m for m in recovery_metrics(engine) if m.objective_id=="urgent-inspection")
    assert metric.latest_assignment_tick==26 and metric.completion_tick==32 and metric.deadline_lateness_ticks==12


@pytest.mark.parametrize("kind", ["failure", "new-task", "disruption"])
def test_complete_reset_replays_world_wire_execution_recovery_and_metrics(kind):
    _,engine=build_demo(kind,horizon=45)
    engine.run(45)
    baseline=signature(engine)
    initial_spec_ids={s.spec.task_id for s in engine._recovery_registry._initial}
    engine.reset()
    assert engine.state is RunState.READY and engine.recovery_trace==()
    assert set(engine._execution_adapter.specs)==initial_spec_ids
    assert all(set(e.specs)==initial_spec_ids for e in engine._executors.values())
    engine.run(45)
    assert signature(engine)==baseline


def test_read_only_dashboard_supports_retry_and_new_task_metadata(recovered):
    scenario,engine=recovered
    before=signature(engine)
    display=capture_dashboard(scenario,engine)
    retry=next(t for t in display.tasks if "::attempt:" in t.task.task_id)
    assert retry.details.task_type=="routine-inspection" and retry.details.duration_ticks==2
    assert signature(engine)==before


def test_recovery_setup_and_policy_validation():
    scenario,_=build_demo()
    engine=scenario.create_engine()
    with pytest.raises(ValueError): engine.enable_recovery()
    engine=scenario.create_engine(with_execution=True)
    with pytest.raises(ValueError): engine.enable_recovery(recoverable_objectives=frozenset({"unknown"}))
    engine.enable_recovery()
    with pytest.raises(ValueError): engine.enable_recovery()
    engine.run(1)
    with pytest.raises(RuntimeError): engine.enable_recovery()


def test_objective_wire_roundtrip_preserves_requirements(recovered):
    _,engine=recovered
    for offer in engine._recovery_registry.offers.values():
        assert decode_availability(availability_data(offer))==offer
        data=encode_recovery("availability","A",45,availability=availability_data(offer))
        payload=CoordinationPayload(TOPIC,data)
        assert decode_recovery(payload)["kind"]=="availability"


@pytest.mark.parametrize("mutation", ["sender", "time", "extra", "generation", "identity", "capabilities", "status", "kind"])
def test_malformed_recovery_envelopes_are_rejected(recovered,mutation):
    _,engine=recovered
    offer=engine._recovery_registry.offers["task-4::attempt:2"]
    data=json.loads(encode_recovery("availability","A",45,availability=availability_data(offer)))
    if mutation=="sender": data["agent_id"]="B"
    if mutation=="time": data["issued_at"]=44
    if mutation=="extra": data["unexpected"]=1
    if mutation=="generation": data["availability"]["generation"]=0
    if mutation=="identity": data["availability"]["objective_id"]="wrong"
    if mutation=="capabilities": data["availability"]["announcement"]["task"]["requirements"]["capabilities"]["operational"]=["exploration","exploration"]
    if mutation=="status": data["availability"]["announcement"]["task"]["status"]="failed"
    if mutation=="kind": data["kind"]="release-everything"
    kind,wire=encode_payload(CoordinationPayload(TOPIC,json.dumps(data)))
    envelope=MessageEnvelope("invalid","A","C",kind,45,wire,50)
    with pytest.raises(ValueError): decode_payload(envelope)


def test_semantic_duplicate_and_old_availability_do_not_downgrade_completed_objective(recovered):
    _,engine=recovered
    actor=engine._recovery_agents["A"]
    offer=engine._recovery_registry.offers[objective(engine).current_task_id]
    assert not actor.learn(offer,45)
    assert not actor.learn(engine._recovery_registry.offers["task-4"],45)
    assert objective(engine).state=="completed"
    with pytest.raises(ValueError): actor.learn(replace(offer,recoverable=False),45)


@pytest.mark.parametrize("kind", [AllocationKind.BID, AllocationKind.ACCEPT])
def test_delayed_duplicate_bid_and_ack_do_not_change_replacement(recovered,kind):
    _,original=recovered
    _,engine=build_demo(horizon=45)
    injected=[]
    if kind is AllocationKind.ACCEPT:
        # Force a remote winner, so ACCEPT is an actual P2P envelope.
        def no_self_bid(sim,event):
            own=next(a for a in sim.world.agents if a.identity.agent_id=="A")
            sim.update_agent(replace(own,capabilities=Capabilities(own.capabilities.sensing),updated_at=14))
        engine.schedule(DynamicEvent("no-self-bid",14,EventKind.RESOURCES_CHANGED,"A","Remote winner fixture"),no_self_bid)
    def late(sim):
        if sim.time.tick==29:
            for r in sim.communication.trace:
                if r.action!="sent": continue
                p=decode_payload(r.envelope)
                if not isinstance(p,CoordinationPayload) or p.topic!="allocation.v1": continue
                m=decode_allocation(p)
                if "::attempt:" in m.round.task_id and m.kind is kind:
                    env=replace(r.envelope,message_id="late:"+r.envelope.message_id,expires_at=None)
                    sim.communication.endpoint(env.recipient_id).receive(DeliveryReceipt(env,29))
                    injected.append(env)
                    break
    engine.register_system("late-proof",Phase.EXECUTION,late)
    engine.run(45)
    assert len(injected)==1
    assert objective(engine).attempts[0]==objective(original).attempts[0]
    assert objective(engine).state=="completed" and len(objective(engine).attempts)==2
    assert objective(engine).attempts[-1].previous_owner==("C" if kind is AllocationKind.ACCEPT else "A")
    assert any(e.action=="stale message" and "Expired" in e.detail for a in "ABC" for e in engine.allocation_history(a))


@pytest.mark.parametrize("changes", [{"owner_id":"B"},{"commit_reference":"wrong"},{"issued_at":0},{"agent_id":"D"}])
def test_invalid_release_proof_owner_clock_or_offline_requester_rejected(changes):
    _,engine=build_demo(horizon=20)
    results=[]
    def attempt(sim):
        if sim.time.tick==10:
            before=sim.world
            results.append(sim._recovery_registry.apply(sim,replace(request(sim),**changes)))
            assert sim.world is before
    apply_inside(engine,attempt)
    engine.run(11)
    assert not results[0].accepted and len(objective(engine).attempts)==1


def test_capability_loss_triggers_local_failure_notification_and_recovery():
    scenario,_=build_demo()
    engine=scenario.create_engine(with_recovery=True)
    def loss(sim,event):
        own=next(a for a in sim.world.agents if a.identity.agent_id=="D")
        sim.update_agent(replace(own,capabilities=Capabilities(),updated_at=9))
    engine.schedule(DynamicEvent("cap-loss",9,EventKind.RESOURCES_CHANGED,"D","D loses exploration"),loss)
    engine.run(45)
    obj=objective(engine)
    assert obj.state=="completed" and obj.attempts[0].ended_at==9
    metric=next(m for m in recovery_metrics(engine) if m.objective_id=="task-4")
    assert metric.detection_tick==9 and metric.failure_announcement_tick==9
    assert any(r.action=="sent" and r.envelope.sender_id=="D" and '"status":"failed"' in r.envelope.payload for r in engine.communication.trace)


def test_temporary_ineligibility_becoming_available_runs_later_round():
    _,engine=build_demo(horizon=65)
    originals={a.identity.agent_id:a.capabilities for a in engine.world.agents}
    def change(sim,event):
        for owner in "ABC":
            own=next(a for a in sim.world.agents if a.identity.agent_id==owner)
            caps=Capabilities(own.capabilities.sensing) if sim.time.tick==14 else originals[owner]
            sim.update_agent(replace(own,capabilities=caps,updated_at=sim.time.tick))
    engine.schedule(DynamicEvent("loss",14,EventKind.RESOURCES_CHANGED,"mission","No explorers temporarily"),change)
    engine.schedule(DynamicEvent("restore",34,EventKind.RESOURCES_CHANGED,"mission","Exploration restored"),change)
    engine.run(65)
    assert objective(engine).state=="completed"
    assert objective(engine).attempts[-1].assigned_at>=34
    assert next(m for m in recovery_metrics(engine) if m.objective_id=="task-4").no_bids>0


def test_cancellation_is_permanent_and_not_recovered():
    scenario,_=build_demo()
    engine=scenario.create_engine(with_recovery=True)
    def cancel(sim,event):
        own=next(a for a in sim.world.agents if a.identity.agent_id=="D")
        cancelled=task(sim,"task-4").transition(TaskStatus.CANCELLED)
        sim.replace_world(replace(sim.world,agents=tuple(replace(a,availability=Availability.AVAILABLE,task_id=None,updated_at=9) if a.identity.agent_id=="D" else a for a in sim.world.agents),
                                 tasks=tuple(cancelled if t.task_id==cancelled.task_id else t for t in sim.world.tasks)))
    engine.schedule(DynamicEvent("cancel",9,EventKind.AGENT_FAILED,"D","Cancel objective"),cancel)
    engine.run(35)
    assert objective(engine).state=="permanently failed" and len(objective(engine).attempts)==1
    assert task(engine,"task-4").status is TaskStatus.CANCELLED


def test_new_objective_duplicate_or_invalid_location_does_not_modify_world():
    scenario,_=build_demo()
    engine=scenario.create_engine(with_recovery=True)
    spec=TaskExecutionSpec("task-4","routine-inspection",2,"hazard",inspection_operation="exploration")
    with pytest.raises(RuntimeError): engine.introduce_objective(task(engine,"task-4"),spec,"B")
    def invalid(sim,event):
        before=sim.world
        with pytest.raises(ValueError): sim.introduce_objective(task(sim,"task-4"),spec,"B")
        assert sim.world is before
        wrong=Task("new","unknown")
        with pytest.raises(ValueError): sim.introduce_objective(wrong,replace(spec,task_id="new"),"B")
        assert sim.world is before and not any(o.objective_id=="new" for o in sim.recovery_snapshot())
    engine.schedule(DynamicEvent("invalid",0,EventKind.TASK_DISCOVERED,"mission","Invalid discoveries"),invalid)
    engine.run(1)


def test_request_recovery_boundary_has_only_own_inputs():
    _,engine=build_demo(horizon=20)
    views=[]
    engine.register_controller("B",lambda v: views.append(v) or ())
    engine.run(18)
    assert any(v.recovery_feedback for v in views)
    for view in views:
        assert all(f.request.agent_id=="B" for f in view.recovery_feedback)
        assert not hasattr(view,"objectives") and not hasattr(view,"leases")
        assert all(a.announcement.task.task_id=="urgent-inspection" for a in view.mission_announcements)


@pytest.mark.parametrize("availability", [Availability.OFFLINE,Availability.FAILED])
def test_external_disabled_owner_is_detected_without_sending_messages(availability):
    scenario,_=build_demo()
    engine=scenario.create_engine(with_recovery=True)
    def disable(sim,event):
        stopped=task(sim,"task-4").transition(TaskStatus.FAILED)
        sim.replace_world(replace(sim.world,
            agents=tuple(replace(a,availability=availability,task_id=None,updated_at=9) if a.identity.agent_id=="D" else a for a in sim.world.agents),
            tasks=tuple(stopped if t.task_id=="task-4" else t for t in sim.world.tasks)))
    engine.schedule(DynamicEvent("disable",9,EventKind.AGENT_FAILED,"D","Owner disabled"),disable)
    engine.run(45)
    assert objective(engine).state=="completed" and objective(engine).attempts[0].ended_at==9
    assert not any(r.action=="sent" and r.envelope.sender_id=="D" and r.tick>=9 for r in engine.communication.trace)


def test_interrupted_consumables_not_refunded_or_borrowed_by_new_attempt():
    from coopai.models import ResourceLevel
    scenario,_=build_demo()
    original=task(scenario.create_engine(),"task-4")
    required=replace(original,requirements=replace(original.requirements,resources=(ResourceLevel("support-kits",2,2),)))
    world=replace(scenario.initial_world,tasks=tuple(required if t.task_id==required.task_id else t for t in scenario.initial_world.tasks))
    scenario=replace(scenario,initial_world=world)
    engine=scenario.create_engine(with_recovery=True)
    def release(sim,event): sim.request_task_release("D","Interrupt after consumables used")
    engine.schedule(DynamicEvent("release",13,EventKind.AGENT_FAILED,"D","Stop after begin"),release)
    engine.run(45)
    obj=objective(engine)
    assert obj.state=="recoverable / unassigned"
    assert obj.attempts[0].progress_ticks==1 and obj.attempts[1].progress_ticks==0
    assert next(r.amount for a in engine.world.agents if a.identity.agent_id=="D" for r in a.resources if r.name=="support-kits")==0
    assert all(not next(r for r in engine.suitability_snapshot(a) if r.task_id==obj.current_task_id).eligibility.eligible for a in "ABCD")


def test_recovery_requester_can_coordinate_but_cannot_win_without_capability():
    _,engine=build_demo(horizon=45)
    def capability_loss(sim,event):
        own=next(a for a in sim.world.agents if a.identity.agent_id=="A")
        sim.update_agent(replace(own,capabilities=Capabilities(own.capabilities.sensing),updated_at=14))
    engine.schedule(DynamicEvent("A-no-exploration",14,EventKind.RESOURCES_CHANGED,"A","A cannot bid"),capability_loss)
    engine.run(45)
    obj=objective(engine)
    assert obj.state=="completed" and obj.attempts[-1].previous_owner=="C"
    assert any(e.agent_id=="A" and e.action=="recovery available" for e in engine.recovery_trace)
    round=next(r for r in engine.allocation_snapshot("A") if r.round.task_id==obj.current_task_id)
    assert {b.message.sender_id for b in round.bids}=={"B","C"}
    assert max(round.bids,key=lambda b:b.message.score).message.sender_id=="C"
