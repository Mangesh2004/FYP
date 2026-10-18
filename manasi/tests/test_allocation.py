"""Actual-message protocol tests plus adversarial admission and wire invariants."""
from dataclasses import replace
from pathlib import Path

import pytest

from coopai.allocation import AgentAllocator, RoundPhase, bid_order
from coopai.allocation_adapter import CommitRegistry
from coopai.allocation_demo import build_engine, protocol_records, replay_signature
from coopai.allocation_messages import (TOPIC, AllocationConfiguration, AllocationEvidence,
    AllocationKind as Kind, AllocationMessage, AllocationRound, CommitRequest,
    coordinator_for, decode_allocation, encode_allocation, task_digest)
from coopai.communication import CommunicationConfiguration, Disconnection
from coopai.eligibility import EligibilityConfiguration, SelfEvaluationContext
from coopai.knowledge import LocalKnowledge
from coopai.messages import CoordinationPayload, decode_payload, encode_payload
from coopai.models import (AgentIdentity, AgentState, Availability, Capabilities, DynamicEvent,
    Environment, EventKind, Location, MessageEnvelope, MissionConfiguration, Position,
    ResourceLevel, Task, TaskRequirements, TaskStatus)
from coopai.priority import PriorityConfiguration, TaskAnnouncement
from coopai.scenario import load_scenario
from coopai.simulation import Phase, SimulationEngine
from coopai.world import WorldSnapshot

PATH = Path(__file__).resolve().parents[1] / "scenarios/four_room_disaster.json"


@pytest.fixture
def scenario():
    return load_scenario(PATH)


def extended(scenario, ticks=40):
    return replace(scenario, mission=replace(scenario.mission,duration_ticks=ticks))


def owners(engine):
    return {t.task_id: t.owner_id for t in engine.world.tasks}


def spec(task_id="work", epoch=0, roster=("A","B"), cfg=AllocationConfiguration()):
    task = Task(task_id,"loc")
    start = epoch * cfg.epoch_ticks
    return AllocationRound(task_id,1,task_digest(task),coordinator_for(task_id,epoch,roster),epoch,start,
                           start+cfg.bid_window_ticks,start+cfg.bid_window_ticks+cfg.award_window_ticks,
                           start+cfg.bid_window_ticks+cfg.award_window_ticks+cfg.commit_window_ticks)


def packet(message, identity="m", recipient=None):
    recipient = recipient or next(a for a in ("A","B","C") if a != message.sender_id)
    kind,text = encode_payload(CoordinationPayload(TOPIC,encode_allocation(message)))
    return MessageEnvelope(identity,message.sender_id,recipient,kind,message.issued_at,text,message.issued_at+8)


@pytest.mark.parametrize("score", [0,0.5,1])
def test_bid_wire_round_trip_and_bounds(score):
    round = spec()
    bid = AllocationMessage(Kind.BID,round,"A",1,"A",score,1)
    envelope = packet(bid)
    assert decode_allocation(decode_payload(envelope),envelope) == bid
    assert bid.score == score and bid.round.round_id


@pytest.mark.parametrize("changes", [
    {"score":-0.1}, {"score":1.1}, {"score":float("nan")}, {"score":float("inf")},
    {"score":True}, {"score":None}, {"candidate_id":"B"}, {"bid_tick":True},
    {"bid_tick":3}, {"bid_tick":2}, {"issued_at":3}, {"references":("unexpected",)},
])
def test_invalid_bid_fields(changes):
    with pytest.raises(ValueError):
        replace(AllocationMessage(Kind.BID,spec(),"A",1,"A",0.7,1),**changes)


@pytest.mark.parametrize("kind,refs", [(Kind.CALL_FOR_PROPOSALS,()), (Kind.NO_BID,()),
    (Kind.PROVISIONAL_AWARD,("bid",)), (Kind.ACCEPT,("bid","award")),
    (Kind.COMMIT,("bid","award","accept")), (Kind.REJECT,()), (Kind.CANCEL,()),
    (Kind.ROUND_CLOSED,("commit",))])
def test_all_protocol_message_types_roundtrip(kind, refs):
    round = spec()
    coordinator = round.coordinator_id
    winner = "A" if coordinator != "A" else "B"
    scored = kind in {Kind.PROVISIONAL_AWARD,Kind.ACCEPT,Kind.COMMIT}
    sender = coordinator if kind in {Kind.CALL_FOR_PROPOSALS,Kind.PROVISIONAL_AWARD,Kind.COMMIT,Kind.CANCEL} else winner
    message = AllocationMessage(kind,round,sender,0 if kind is Kind.CALL_FOR_PROPOSALS else 1 if kind is Kind.NO_BID else 3,
                                winner if scored or kind in {Kind.REJECT,Kind.ROUND_CLOSED} else None,
                                0.7 if scored else None,1 if scored else None,refs)
    envelope = packet(message)
    assert decode_allocation(decode_payload(envelope),envelope) == message


def test_wire_rejects_spoofed_identity_unknown_fields_and_future_evaluation():
    message = AllocationMessage(Kind.BID,spec(),"A",1,"A",0.7,1)
    envelope = packet(message)
    with pytest.raises(ValueError,match="sender"):
        decode_payload(replace(envelope,sender_id="C"))
    with pytest.raises(ValueError,match="timestamp"):
        decode_payload(replace(envelope,sent_at=2))
    text = encode_allocation(message)
    with pytest.raises(ValueError,match="fields"):
        decode_allocation(CoordinationPayload(TOPIC,text[:-1]+',"extra":1}'))
    with pytest.raises(ValueError):
        decode_allocation(CoordinationPayload(TOPIC,text.replace('"references":[]','"references":{}')))
    with pytest.raises(ValueError):
        replace(message,bid_tick=2)


@pytest.mark.parametrize("changes", [{"bid_window_ticks":0},{"award_window_ticks":True},
    {"commit_window_ticks":-1},{"confirmation_interval_ticks":0}])
def test_invalid_protocol_windows(changes):
    with pytest.raises(ValueError): AllocationConfiguration(**changes)


def test_public_roster_rotation_is_deterministic_and_score_independent():
    roster=("D","B","A","C")
    chosen=[coordinator_for("opaque-task",e,roster) for e in range(8)]
    assert chosen == [coordinator_for("opaque-task",e,roster[::-1]) for e in range(8)]
    assert len(set(chosen[:4])) == 4 and chosen[:4] == chosen[4:]
    for invalid in ((),("A","A"),["A"]):
        with pytest.raises(ValueError): coordinator_for("x",0,invalid)
    assert AllocationConfiguration.for_latency(2).bid_window_ticks == 5
    with pytest.raises(ValueError): AllocationConfiguration.for_latency(0)


def test_tie_breaking_score_then_timestamp_then_agent_id():
    round=spec()
    def bid(agent,score,tick):
        return AllocationEvidence(agent,AllocationMessage(Kind.BID,round,agent,tick,agent,score,tick),agent)
    bids=[bid("C",0.8,1),bid("B",0.8,1),bid("A",0.8,2),bid("D",0.7,0)]
    assert [r.message.sender_id for r in sorted(bids,key=bid_order)] == ["B","C","A","D"]
    assert min(bids,key=bid_order) == min(bids[::-1],key=bid_order)
    with pytest.raises(ValueError): bid_order(AllocationEvidence("c",AllocationMessage(Kind.CALL_FOR_PROPOSALS,round,round.coordinator_id,0),round.coordinator_id))


def test_actual_four_room_rounds_scores_eligibility_and_ownership(scenario):
    engine=scenario.create_engine(with_allocation=True)
    engine.run(3)
    assert all(t.owner_id is None for t in engine.world.tasks)
    engine.run(1)
    assert owners(engine)["task-2"] == "B" and owners(engine)["task-1"] is None
    engine.run(3)
    assert owners(engine) == {"task-1":"A","task-2":"B","task-3":"C","task-4":"D"}
    assert all(t.status is TaskStatus.ASSIGNED for t in engine.world.tasks)
    assert all(a.availability is Availability.BUSY and a.task_id for a in engine.world.agents)
    assert len({t.owner_id for t in engine.world.tasks}) == 4
    assert {t.position for t in engine.world.agents} == {t.position for t in scenario.initial_world.agents}
    network=list(protocol_records(engine))
    bids=[m for r,m in network if r.action == "sent" and m.kind is Kind.BID]
    assert not any(m.sender_id=="D" and m.round.task_id in {"task-1","task-2","task-3"} for m in bids)
    assert next(m.score for m in bids if m.sender_id=="A" and m.round.task_id=="task-1") == pytest.approx(0.97)
    assert any(m.kind is Kind.NO_BID for r,m in network)
    assert any(m.kind is Kind.ACCEPT and r.action=="processed" for r,m in network)
    assert all(f.accepted for f in engine.commit_trace)
    assert tuple((f.request.task_id,f.applied_at) for f in engine.commit_trace) == (("task-2",3),("task-1",6),("task-3",6),("task-4",6))


def test_agent_priority_controls_round_initiation_not_suitability(scenario):
    engine=scenario.create_engine(with_allocation=True)
    engine.run(1)
    opened=[e for e in engine.allocation_history("B") if e.action=="round opened"]
    assert [e.task_id for e in opened] == ["task-1","task-2"]
    # B cannot perform its first-priority fire task but can coordinate received bids.
    assert next(r for r in engine.suitability_snapshot("B") if r.task_id=="task-1").score is None
    assert all(e.detail.startswith("Own task priority") for e in opened)


def test_agent_local_views_and_hidden_task_no_global_registry(scenario):
    engine=scenario.create_engine(with_allocation=True)
    hidden=Task("secret","room-4")
    engine.replace_world(replace(engine.world,tasks=(*engine.world.tasks,hidden)))
    views=[]
    engine.register_controller("A",lambda view: views.append(view) or ())
    engine.run(9)
    assert owners(engine)["secret"] is None
    assert not any(e.task_id=="secret" for a in "ABCD" for e in engine.allocation_history(a))
    assert all(f.request.agent_id=="A" for v in views for f in v.allocation_feedback)
    assert all(r.agent_id=="A" for v in views for r in v.suitability)
    assert not hasattr(views[0],"world") and not hasattr(views[0],"global_scores")
    allocator=AgentAllocator("A",("A","B"))
    assert not hasattr(allocator,"world") and not hasattr(allocator,"engine")
    # Receiver has learned peer ownership through ROUND_CLOSED, not truth backfill.
    assert {a.task.task_id:a.task.owner_id for a in engine.known_tasks("A")} == {"task-1":"A","task-2":"B","task-3":"C","task-4":"D"}


def test_reset_replays_all_round_message_feedback_and_world_histories(scenario):
    engine=scenario.create_engine(with_allocation=True)
    engine.run(16)
    signature=replay_signature(engine)
    engine.reset()
    assert engine.commit_trace == ()
    assert all(engine.allocation_snapshot(a)==() and engine.allocation_history(a)==() for a in "ABCD")
    assert all(a.availability is Availability.AVAILABLE for a in engine.world.agents)
    assert all(t.owner_id is None for t in engine.world.tasks)
    engine.run(16)
    assert replay_signature(engine) == signature


@pytest.mark.parametrize("latency,remote_tick", [(1,6),(2,11),(3,16)])
def test_explicit_latency_budget_changes_timing(latency,remote_tick,scenario):
    engine=build_engine(extended(scenario),latency)
    engine.run(25)
    assert owners(engine) == {"task-1":"A","task-2":"B","task-3":"C","task-4":"D"}
    assert next(f.applied_at for f in engine.commit_trace if f.request.task_id=="task-1") == remote_tick


@pytest.mark.parametrize("lost_tick,kind", [(1,Kind.BID),(3,Kind.PROVISIONAL_AWARD),(4,Kind.ACCEPT),(5,Kind.COMMIT)])
def test_lost_negotiation_messages_timeout_clear_and_rotate_recover(lost_tick,kind,scenario):
    engine=build_engine(extended(scenario))
    def loss(sim): sim.communication.set_loss_probability(1 if sim.time.tick==lost_tick else 0)
    engine.register_system("one-tick-loss",Phase.SUBMISSION,loss)
    engine.run(10)
    assert any(r.action=="dropped" and m.kind is kind for r,m in protocol_records(engine))
    assert any(e.action in {"round expired","round closed"} for a in "ABCD" for e in engine.allocation_history(a))
    assert all(s.phase not in {RoundPhase.ACCEPTED,RoundPhase.PROTOCOL_CONFIRMED,RoundPhase.AWARDED} for a in "ABCD" for s in engine.allocation_snapshot(a))
    engine.run(20)
    if lost_tick == 1:
        # With all peer bids lost, C legitimately wins its own routine round.
        # Its later commitment leaves no available visual agent for person search:
        # this greedy baseline cannot promise optimal completion under loss.
        assert owners(engine) == {"task-1":"A","task-2":"B","task-3":None,"task-4":"C"}
    else:
        assert owners(engine) == {"task-1":"A","task-2":"B","task-3":"C","task-4":"D"}
    assert any(e.action=="round opened" and '1,' in e.round_id for a in "ABCD" for e in engine.allocation_history(a))
    signature=replay_signature(engine)
    engine.reset();engine.run(30)
    assert replay_signature(engine) == signature


def test_partition_does_not_fabricate_consensus_or_keep_reservations(scenario):
    engine=scenario.create_engine(with_suitability=True)
    engine.enable_communication(CommunicationConfiguration(links=()))
    engine.enable_allocation()
    engine.run(16)
    assert owners(engine)["task-1"] is None and owners(engine)["task-3"] is None
    assert owners(engine)["task-2"] == "B"  # Legitimate local self-protocol only.
    assert any(r.action=="dropped" for r in engine.communication.trace)
    assert all(s.phase in {RoundPhase.CLOSED,RoundPhase.EXPIRED,RoundPhase.COMMITTED} for a in "ABCD" for s in engine.allocation_snapshot(a) if s.round.epoch==0)


def test_coordinator_failure_rotates_and_free_tasks_recover(scenario):
    engine=build_engine(extended(scenario))
    initial=next(a for a in engine.world.agents if a.identity.agent_id=="B")
    def failure(sim):
        if sim.time.tick==1: sim.update_agent(replace(initial,availability=Availability.FAILED,updated_at=1))
    engine.register_system("failed-coordinator",Phase.EXECUTION,failure)
    engine.run(30)
    assert owners(engine)["task-1"] == "A" and owners(engine)["task-2"] is None
    assert not any(f.accepted and f.request.agent_id=="B" for f in engine.commit_trace)
    assert any(e.action=="round opened" and e.tick==10 and e.task_id=="task-1" for e in engine.allocation_history("C"))


def test_temporarily_unavailable_winner_rejects_then_retries(scenario):
    engine=build_engine(extended(scenario))
    original=next(a for a in engine.world.agents if a.identity.agent_id=="A")
    def battery(sim):
        if sim.time.tick==4: sim.update_agent(replace(original,battery=0.05,updated_at=4))
        if sim.time.tick==10: sim.update_agent(replace(original,battery=1,updated_at=10))
    engine.register_system("award-battery-change",Phase.EXECUTION,battery)
    engine.run(10)
    assert owners(engine)["task-1"] is None
    assert any(e.action=="rejected award" and "Battery" in e.detail for e in engine.allocation_history("A"))
    engine.run(20)
    assert owners(engine)["task-1"] == "A"


def test_local_and_authoritative_ownership_separate_when_closure_is_lost(scenario):
    engine=build_engine(extended(scenario))
    def loss(sim): sim.communication.set_loss_probability(1 if sim.time.tick==7 else 0)
    engine.register_system("lose-closure",Phase.SUBMISSION,loss)
    engine.run(9)
    assert owners(engine)["task-1"] == "A"
    assert next(a.task.owner_id for a in engine.known_tasks("B") if a.task.task_id=="task-1") is None
    engine.run(3)
    assert next(a.task.owner_id for a in engine.known_tasks("B") if a.task.task_id=="task-1") == "A"


def test_duplicate_receipt_and_old_round_messages_do_not_reassign(scenario):
    engine=build_engine(extended(scenario))
    engine.run(9)
    before=owners(engine)
    accept_receipt=next(p.receipt for p in engine.communication.endpoint("B").received_history
                        if isinstance(decode_payload(p.receipt.envelope),CoordinationPayload)
                        and decode_payload(p.receipt.envelope).topic==TOPIC
                        and decode_allocation(decode_payload(p.receipt.envelope)).kind is Kind.ACCEPT)
    # Trusted test injection uses the existing endpoint receive/duplicate path.
    engine.communication.endpoint("B").receive(accept_receipt)
    engine.run(1)
    assert owners(engine)==before and len(engine.commit_trace)==4
    assert any(r.action=="duplicate" and r.envelope.message_id==accept_receipt.envelope.message_id for r in engine.communication.trace)


def small_engine():
    topology=Environment((Location("loc","Location"),))
    capable=Capabilities(frozenset({"needed"}))
    agents=tuple(AgentState(AgentIdentity(a,a),capable if a=="A" else Capabilities(),Position("loc"),1) for a in "ABC")
    names=[f"work-{n}" for n in range(30) if coordinator_for(f"work-{n}",0,("A","B","C"))=="B"][:2]
    tasks=tuple(Task(name,"loc",TaskRequirements(capable),urgency=urgency) for name,urgency in zip(names,(0.9,0.1)))
    engine=SimulationEngine(MissionConfiguration("small",4,40),WorldSnapshot(topology,agents,tasks))
    for a in agents: engine.register_local_knowledge(LocalKnowledge(a.identity.agent_id))
    engine.enable_task_priority()
    for a in agents:
        for task in tasks: engine.announce_task(a.identity.agent_id,TaskAnnouncement(task,"custom",0))
    engine.enable_self_evaluation(topology)
    engine.enable_communication()
    engine.enable_allocation()
    return engine,tasks


def test_multiple_task_contention_one_capacity_prefers_own_priority():
    engine,tasks=small_engine()
    engine.run(20)
    assert owners(engine)[tasks[0].task_id]=="A" and owners(engine)[tasks[1].task_id] is None
    awards=[e for e in engine.allocation_history("A") if e.action=="receive PROVISIONAL_AWARD"]
    assert {e.task_id for e in awards} == {t.task_id for t in tasks}
    assert any(e.action=="rejected award" and e.task_id==tasks[1].task_id for e in engine.allocation_history("A"))
    assert len([t for t in engine.world.tasks if t.owner_id=="A"])==1


def proof_fixture(*, winner="A", roster=("A","B"), epoch=0):
    task=Task("work","loc")
    round=spec(task.task_id,epoch,roster)
    coordinator=round.coordinator_id
    now=round.opened_at
    entries=[]
    processed={}
    def add(kind,sender,recipient,issued,refs):
        message=AllocationMessage(kind,round,sender,issued,winner,0.8,now+1,refs)
        identity=f"proof-{epoch}-{winner}-{kind.value}"
        env=None if sender==recipient else packet(message,identity,recipient)
        entry=AllocationEvidence(identity,message,recipient,env)
        entries.append(entry)
        if env: processed[identity]=issued+1
        return identity
    bid=add(Kind.BID,winner,coordinator,now+1,())
    award=add(Kind.PROVISIONAL_AWARD,coordinator,winner,now+3,(bid,))
    accept=add(Kind.ACCEPT,winner,coordinator,now+4,(bid,award))
    commit=add(Kind.COMMIT,coordinator,winner,now+5,(bid,award,accept))
    request=CommitRequest(winner,task.task_id,round.round_id,now+6,(bid,award,accept,commit))
    topology=Environment((Location("loc","Location"),))
    agents=tuple(AgentState(AgentIdentity(a,a),Capabilities(),Position("loc"),1) for a in roster)
    world=WorldSnapshot(topology,agents,(task,))
    own=next(a for a in agents if a.identity.agent_id==winner)
    ctx=SelfEvaluationContext(own,request.issued_at,(TaskAnnouncement(task,"custom",0),),(),topology)
    registry=CommitRegistry(roster,AllocationConfiguration())
    for entry in entries: registry.record(entry.message.sender_id,entry)
    return registry,request,world,ctx,processed,entries


def test_proof_admission_applies_atomic_consistency_and_is_idempotent():
    registry,request,world,ctx,processed,_=proof_fixture()
    updated,feedback=registry.apply(request,6,world,ctx,EligibilityConfiguration(),processed)
    assert feedback.accepted and updated.tasks[0].owner_id=="A"
    assert next(a for a in updated.agents if a.identity.agent_id=="A").task_id=="work"
    repeated,second=registry.apply(request,7,updated,replace(ctx,tick=7),EligibilityConfiguration(),processed)
    assert repeated is updated and second==feedback and len(registry.trace)==1


@pytest.mark.parametrize("missing_index", [0,1,2,3])
def test_missing_confirmation_evidence_cannot_assign(missing_index):
    registry,request,world,ctx,processed,entries=proof_fixture()
    blank=CommitRegistry(registry.roster,registry.configuration)
    for i,entry in enumerate(entries):
        if i!=missing_index: blank.record(entry.message.sender_id,entry)
    updated,feedback=blank.apply(request,6,world,ctx,EligibilityConfiguration(),processed)
    assert updated is world and not feedback.accepted and "Missing" in feedback.reason


@pytest.mark.parametrize("missing_index", [0,1,2,3])
def test_sent_but_not_received_confirmation_is_insufficient(missing_index):
    registry,request,world,ctx,processed,entries=proof_fixture()
    registry,request,world,ctx,processed,entries=proof_fixture(winner="B")
    # Candidate B differs from coordinator A for a real peer chain.
    assert entries[missing_index].envelope is not None
    processed.pop(entries[missing_index].record_id)
    updated,feedback=registry.apply(request,6,world,ctx,EligibilityConfiguration(),processed)
    assert updated is world and not feedback.accepted and "processed" in feedback.reason


def test_stale_conflicting_commit_and_invalid_candidate_rejected():
    registry,request,world,ctx,processed,entries=proof_fixture()
    # Wrong task content cannot be hidden by a correct-looking protocol handshake.
    changed=replace(world,tasks=(replace(world.tasks[0],urgency=0.7),))
    updated,feedback=registry.apply(request,6,changed,ctx,EligibilityConfiguration(),processed)
    assert not feedback.accepted and "revision changed" in feedback.reason
    registry,request,world,ctx,processed,entries=proof_fixture()
    expired=replace(request,issued_at=9)
    updated,feedback=registry.apply(expired,9,world,replace(ctx,tick=9),EligibilityConfiguration(),processed)
    assert not feedback.accepted and "Expired" in feedback.reason
    registry,request,world,ctx,processed,entries=proof_fixture()
    failed=replace(ctx.own_state,availability=Availability.FAILED)
    failedworld=replace(world,agents=tuple(failed if a.identity.agent_id=="A" else a for a in world.agents))
    _,feedback=registry.apply(request,6,failedworld,replace(ctx,own_state=failed),EligibilityConfiguration(),processed)
    assert not feedback.accepted


def test_conflicting_valid_outcomes_single_owner_guard_without_ranking():
    registry,request,world,ctx,processed,entries=proof_fixture(winner="A")
    updated,feedback=registry.apply(request,6,world,ctx,EligibilityConfiguration(),processed)
    assert feedback.accepted
    other,request2,_,ctx2,processed2,entries2=proof_fixture(winner="B")
    for entry in entries2: registry.record(entry.message.sender_id,entry)
    unchanged,feedback2=registry.apply(request2,6,updated,ctx2,EligibilityConfiguration(),{**processed,**processed2})
    assert unchanged is updated and not feedback2.accepted
    assert "not pending" in feedback2.reason and updated.tasks[0].owner_id=="A"


def test_bad_proof_roles_and_audit_spoof_are_rejected():
    registry,request,world,ctx,processed,entries=proof_fixture()
    with pytest.raises(ValueError,match="producer"):
        registry.record("B",entries[0])
    with pytest.raises(ValueError,match="reused"):
        registry.record(entries[0].message.sender_id,replace(entries[0],message=replace(entries[0].message,score=0.1),envelope=None,recipient_id="A"))
    wrong=replace(request,evidence_ids=request.evidence_ids[::-1])
    _,feedback=registry.apply(wrong,6,world,ctx,EligibilityConfiguration(),processed)
    assert not feedback.accepted and "lineage" in feedback.reason


def test_positive_bid_cannot_bypass_current_capability_or_battery_gate():
    registry,request,world,ctx,processed,_=proof_fixture()
    depleted=replace(ctx.own_state,battery=0.01)
    world=replace(world,agents=tuple(depleted if a.identity.agent_id=="A" else a for a in world.agents))
    _,feedback=registry.apply(request,6,world,replace(ctx,own_state=depleted),EligibilityConfiguration(),processed)
    assert not feedback.accepted and "eligibility failed" in feedback.reason


def test_engine_allocation_setup_requires_existing_independent_modules(scenario):
    engine=scenario.create_engine()
    with pytest.raises(ValueError,match="requires"): engine.enable_allocation()
    engine=scenario.create_engine(with_allocation=True)
    with pytest.raises(ValueError): engine.enable_allocation()
    engine.run(1)
    with pytest.raises(RuntimeError): engine.enable_allocation()


def test_demo_failure_trace_and_actual_commit_times(capsys):
    from coopai.allocation_demo import main
    main()
    output=capsys.readouterr().out
    assert "task-1 status=assigned owner=A" in output
    assert "task-4 status=assigned owner=D" in output
    assert "controlled_award_loss=" in output and "PROVISIONAL_AWARD" in output
    assert "allocation_replay_equal=True" in output and "failure_replay_equal=True" in output


def test_semantic_duplicate_call_does_not_create_second_bid(scenario):
    from coopai.allocation_messages import encode_allocation
    engine=build_engine(extended(scenario))
    def repeat(sim):
        if sim.time.tick==1:
            call=next(m for r,m in protocol_records(sim) if r.action=="sent" and m.kind is Kind.CALL_FOR_PROPOSALS and m.round.task_id=="task-1")
            current=replace(call,issued_at=1)
            sim.communication.endpoint(call.sender_id).send_coordination(TOPIC,encode_allocation(current),"A",1)
    engine.register_system("repeat-call",Phase.SUBMISSION,repeat)
    engine.run(9)
    sent=[m for r,m in protocol_records(engine) if r.action=="sent" and m.kind is Kind.BID and m.sender_id=="A" and m.round.task_id=="task-1"]
    assert len(sent)==1 and owners(engine)["task-1"]=="A"


def test_late_delivered_bid_is_ignored_not_ranked(scenario):
    from coopai.messages import DeliveryReceipt
    engine=build_engine(extended(scenario))
    def late(sim):
        if sim.time.tick==2:
            original=next(r.envelope for r,m in protocol_records(sim) if r.action=="sent" and m.kind is Kind.BID and m.sender_id=="A" and m.round.task_id=="task-1")
            duplicate=replace(original,message_id="late-new-id")
            sim.communication.endpoint("B").receive(DeliveryReceipt(duplicate,3))
    engine.register_system("late-bid",Phase.EXECUTION,late)
    engine.run(7)
    assert any(e.action=="stale bid" for e in engine.allocation_history("B"))
    assert not any(b.record_id=="late-new-id" for state in engine.allocation_snapshot("B") for b in state.bids)
    assert owners(engine)["task-1"]=="A"


def test_unknown_call_and_wrong_public_round_configuration_are_rejected(scenario):
    engine=build_engine(extended(scenario))
    def inject(sim):
        if sim.time.tick==0:
            unknown=spec("unknown",roster=("A","B","C","D"))
            sender=unknown.coordinator_id
            recipient=next(a for a in "ABCD" if a!=sender)
            sim.communication.endpoint(sender).send_coordination(TOPIC,encode_allocation(AllocationMessage(Kind.CALL_FOR_PROPOSALS,unknown,sender,0)),recipient,0)
            task=next(t for t in sim.world.tasks if t.task_id=="task-1")
            wrong=replace(spec("task-1",roster=("A","B","C","D")),task_digest=task_digest(task),bid_deadline=4)
            sim.communication.endpoint(wrong.coordinator_id).send_coordination(TOPIC,encode_allocation(AllocationMessage(Kind.CALL_FOR_PROPOSALS,wrong,wrong.coordinator_id,0)),"A",0)
    engine.register_system("bad-calls",Phase.SUBMISSION,inject)
    engine.run(9)
    assert not any(s.round.task_id=="unknown" for a in "ABCD" for s in engine.allocation_snapshot(a))
    assert any(e.action=="rejected message" and "configuration" in e.detail for e in engine.allocation_history("A"))
    assert owners(engine)["task-1"]=="A"


def test_changed_task_announcement_invalidates_existing_round(scenario):
    engine=build_engine(extended(scenario))
    def update(sim):
        if sim.time.tick==4:
            known=next(a for a in sim.known_tasks("A") if a.task.task_id=="task-1")
            changed=replace(known.task,requirements=TaskRequirements(Capabilities(operational=frozenset({"firefighting"}))))
            sim.announce_task("A",replace(known,task=changed,announced_at=4,version=2))
    engine.register_system("new-requirements",Phase.EXECUTION,update)
    engine.run(10)
    assert owners(engine)["task-1"] is None
    assert any(e.action=="stale message" for e in engine.allocation_history("A"))


def test_peer_failure_during_accepted_reservation_cannot_commit(scenario):
    engine=build_engine(extended(scenario))
    original=next(a for a in engine.world.agents if a.identity.agent_id=="A")
    def failure(sim):
        if sim.time.tick==5:
            sim.update_agent(replace(original,availability=Availability.FAILED,updated_at=5))
    engine.register_system("fail-accepted-winner",Phase.EXECUTION,failure)
    engine.run(10)
    assert owners(engine)["task-1"] is None
    assert any(s.phase is RoundPhase.EXPIRED for s in engine.allocation_snapshot("A") if s.round.task_id=="task-1")
    assert not any(f.request.agent_id=="A" for f in engine.commit_trace)


def test_expired_envelopes_and_seeded_loss_do_not_invent_owners(scenario):
    engine=extended(scenario).create_engine(with_suitability=True)
    engine.enable_communication(CommunicationConfiguration(latency_ticks=2,ttl_ticks=1))
    engine.enable_allocation(AllocationConfiguration.for_latency(2))
    engine.run(20)
    assert owners(engine)["task-1"] is None
    assert any(r.action=="dropped" and r.reason=="expired" for r in engine.communication.trace)
    lost=extended(scenario).create_engine(with_suitability=True)
    lost.enable_communication(CommunicationConfiguration(loss_probability=0.3))
    lost.enable_allocation()
    lost.run(30)
    signature=replay_signature(lost)
    lost.reset();lost.run(30)
    assert replay_signature(lost)==signature
    assert len([t.owner_id for t in lost.world.tasks if t.owner_id]) == len({t.owner_id for t in lost.world.tasks if t.owner_id})


def test_temporary_partition_and_coordinator_recovery(scenario):
    engine=extended(scenario).create_engine(with_suitability=True)
    engine.enable_communication(CommunicationConfiguration(disconnections=(Disconnection("A",3,7),)))
    engine.enable_allocation()
    engine.run(10)
    assert owners(engine)["task-1"] is None
    engine.run(20)
    assert owners(engine)["task-1"]=="A"


def test_current_capability_mismatch_cannot_be_admitted_even_with_positive_wire_bid():
    registry,request,world,ctx,processed,entries=proof_fixture()
    required=replace(world.tasks[0],requirements=TaskRequirements(Capabilities(frozenset({"gas"}))))
    revised_spec=replace(entries[0].message.round,task_digest=task_digest(required))
    blank=CommitRegistry(registry.roster,registry.configuration)
    for entry in entries:
        revised=replace(entry,message=replace(entry.message,round=revised_spec))
        blank.record(revised.message.sender_id,revised)
    world=replace(world,tasks=(required,))
    ctx=replace(ctx,known_tasks=(TaskAnnouncement(required,"custom",0),))
    unchanged,feedback=blank.apply(request,6,world,ctx,EligibilityConfiguration(),processed)
    assert unchanged is world and not feedback.accepted and "Missing sensing: gas" in feedback.reason


def test_roster_and_config_identity_validation_without_world_access():
    with pytest.raises(ValueError): AgentAllocator("X",("A","B"))
    with pytest.raises(ValueError): AgentAllocator("A",("A","B"),None)
    with pytest.raises(ValueError): CommitRequest("A","task","round",0,("same",)*4)
    with pytest.raises(ValueError): replace(spec(),task_digest="wrong")
    with pytest.raises(ValueError): replace(spec(),bid_deadline=0)


def test_resource_consumption_after_acceptance_rejects_commit():
    engine,tasks=small_engine()
    own=next(a for a in engine.world.agents if a.identity.agent_id=="A")
    supplied=replace(own,resources=(ResourceLevel("kit",1,2),))
    required=replace(tasks[0],requirements=replace(tasks[0].requirements,resources=(ResourceLevel("kit",1,2),)))
    engine.replace_world(replace(engine.world,agents=tuple(supplied if a.identity.agent_id=="A" else a for a in engine.world.agents),
                               tasks=tuple(required if t.task_id==required.task_id else t for t in engine.world.tasks)))
    for agent in "ABC":
        prior=next(a for a in engine.known_tasks(agent) if a.task.task_id==required.task_id)
        engine.announce_task(agent,replace(prior,task=required,version=2))
    def consume(sim):
        if sim.time.tick==5:
            sim.update_agent(replace(supplied,resources=(ResourceLevel("kit",0,2),),updated_at=5))
    engine.register_system("consume-reservation-stock",Phase.EXECUTION,consume)
    engine.run(10)
    assert owners(engine)[required.task_id] is None
    assert any(e.action=="rejected commit" for e in engine.allocation_history("A"))


def test_controller_views_refresh_after_ownership_notifications(scenario):
    engine=build_engine(scenario)
    views=[]
    engine.register_controller("B",lambda v: views.append(v) or ())
    engine.run(9)
    own=views[4]
    assert next(a.task.status for a in own.known_tasks if a.task.task_id=="task-2") is TaskStatus.ASSIGNED
    assert next(r.eligibility.eligible for r in own.suitability if r.task_id=="task-2")
    peer=views[8]
    assert next(a.task.owner_id for a in peer.known_tasks if a.task.task_id=="task-1")=="A"
    assert next(r.score for r in peer.suitability if r.task_id=="task-1") is None


def test_late_closure_does_not_overwrite_newer_task_requirements(scenario):
    engine=build_engine(extended(scenario))
    def newer(sim):
        if sim.time.tick==8:
            known=next(a for a in sim.known_tasks("B") if a.task.task_id=="task-1")
            revised=replace(known.task,requirements=TaskRequirements(Capabilities(operational=frozenset({"new-operation"}))))
            sim.announce_task("B",replace(known,task=revised,version=2,announced_at=8))
    engine.register_system("newer-before-closure",Phase.EXECUTION,newer)
    engine.run(12)
    assert owners(engine)["task-1"]=="A"
    known=next(a for a in engine.known_tasks("B") if a.task.task_id=="task-1")
    assert known.task.owner_id is None and "new-operation" in known.task.requirements.capabilities.operational
    assert any(e.action=="stale closure" for e in engine.allocation_history("B"))
