"""Five complete missions: configuration, real autonomy, truthful reports and replay."""
from copy import deepcopy
from dataclasses import replace, FrozenInstanceError
import json
from pathlib import Path

import pytest

from coopai.allocation import bid_order
from coopai.allocation_messages import AllocationKind, decode_allocation
from coopai.communication import CommunicationConfiguration
from coopai.messages import CoordinationPayload, decode_payload
from coopai.mission_report import decision_explanation, mission_summary, significant_events, verify_expected
from coopai.mission_scenarios import (SCENARIO_FILES, get_scenario, list_scenarios,
    load_mission_scenario, mission_scenario_from_dict)
from coopai.models import Availability, Capabilities, DynamicEvent, EventKind, Position, TaskStatus
from coopai.navigation import distance, point_allowed, validate_route
from coopai.scenario import ScenarioError, load_scenario
from coopai.scenarios_demo import replay_signature
from coopai.simulation import Phase, RunState


@pytest.fixture
def config():
    return json.loads(SCENARIO_FILES["corridor"].read_text())


@pytest.fixture(scope="module",params=tuple(SCENARIO_FILES))
def finished(request):
    case=get_scenario(request.param)
    engine=case.create_engine()
    frames=[]
    routes={}
    def record(sim):
        frames.append(sim.world)
        for agent in sim.world.agents:
            state=sim.execution_snapshot(agent.identity.agent_id)
            if state.route:
                routes[(agent.identity.agent_id,state.task_id)]=state.route
    engine.register_system("test-physical-records",Phase.RECORD,record)
    engine.run(engine.mission.duration_ticks)
    return case,engine,tuple(frames),routes


def test_registry_validates_five_immutable_distinct_cases():
    cases=list_scenarios()
    assert tuple(c.scenario_id for c in cases)==tuple(SCENARIO_FILES)
    assert len({c.display_name for c in cases})==5
    assert all(c.description and c.demonstration_objective for c in cases)
    with pytest.raises(TypeError): SCENARIO_FILES["fake"]=Path("fake")
    with pytest.raises(FrozenInstanceError): cases[0].display_name="changed"
    with pytest.raises(ScenarioError): get_scenario("unknown")


@pytest.mark.parametrize("mutation",[
    lambda c:c["agents"][0].update(position={"x":float("nan"),"y":5}),
    lambda c:c["agents"][0].update(position={"x":1,"y":None}),
    lambda c:c["agents"][0].update(position={"x":1}),
    lambda c:c["agents"][0].update(position={"x":20,"y":5}),
    lambda c:c["agents"][0].update(position={"x":1,"y":2}),
    lambda c:c["agents"][0].update(position={"x":0,"y":5}),
    lambda c:c["agents"][0].update(position={"x":True,"y":5}),
    lambda c:c["agents"][0].update(location="missing"),
    lambda c:c["agents"][0].update(battery=1.1),
    lambda c:c["agents"][0]["resources"][0].update(amount=3),
    lambda c:c["agents"][0]["resources"][0].update(capacity=-1),
    lambda c:c["agents"][0]["capabilities"].update(sensing=["thermal","unknown"]),
    lambda c:c["agents"][0]["capabilities"].update(operational=["exploration","gas"]),
    lambda c:c["tasks"][0]["requirements"]["capabilities"].update(sensing=[]),
    lambda c:c["tasks"][0]["requirements"]["capabilities"].update(sensing=["unknown"]),
    lambda c:c["tasks"][0].update(type="automatic-success"),
    lambda c:c["tasks"][0].update(duration_ticks=0),
    lambda c:c["tasks"][0].update(urgency=float("inf")),
    lambda c:c["tasks"][0].update(location="unknown"),
    lambda c:c["tasks"][0].update(owner_id="A"),
    lambda c:c["tasks"][0].update(priority_score=1),
    lambda c:c.update(winner="A"),
    lambda c:c.update(allow_infeasible_objectives="yes"),
    lambda c:c["passages"].pop(0),
    lambda c:c["communication"].update(latency_ticks=0),
    lambda c:c["communication"].update(loss_probability=2),
    lambda c:c["communication"].update(ttl_ticks=True),
    lambda c:c["communication"].update(links=[["A","unknown"]]),
    lambda c:c["communication"].update(links=[["A","A"]]),
    lambda c:c["communication"].update(disconnections=[{"agent_id":"Z","start_tick":1,"end_tick":3}]),
    lambda c:c["communication"].update(disconnections=[{"agent_id":"A","start_tick":1,"end_tick":1000}]),
    lambda c:c["demonstration"]["expected"].update(completed_objectives=5),
    lambda c:c["demonstration"]["expected"].update(minimum_moving_agents=5),
    lambda c:c["demonstration"]["expected"].update(requires_recovery=1),
    lambda c:c["demonstration"]["expected"].update(winner="A"),
])
def test_bad_configuration_cannot_bypass_validation(config,mutation):
    mutation(config)
    with pytest.raises(ScenarioError): mission_scenario_from_dict(config)


@pytest.mark.parametrize("mutation",[
    lambda c:c["events"][1].update(recipient="unknown"),
    lambda c:c["events"][1].update(target="wrong"),
    lambda c:c["events"][1]["task"].update(id="task-1"),
    lambda c:c["events"][1]["task"].update(deadline=11),
    lambda c:c["events"][1]["task"]["requirements"]["capabilities"].update(sensing=["unknown"]),
    lambda c:c["events"][1].update(recoverable=1),
    lambda c:c["events"][0].update(tick=64),
])
def test_invalid_dynamic_objective_configurations_are_rejected(mutation):
    config=json.loads(SCENARIO_FILES["emergency"].read_text())
    mutation(config)
    with pytest.raises(ScenarioError): mission_scenario_from_dict(config)


@pytest.mark.parametrize("mutation",[
    lambda c:c["events"][0].update(target="room-1"),
    lambda c:c["events"][1].update(connected="false"),
    lambda c:c["events"][1].update(target="unknown"),
    lambda c:c["events"].append(deepcopy(c["events"][0])),
])
def test_invalid_failure_and_connection_events_are_rejected(mutation):
    config=json.loads(SCENARIO_FILES["failure-disruption"].read_text())
    mutation(config)
    with pytest.raises(ScenarioError): mission_scenario_from_dict(config)


@pytest.mark.parametrize("text",['[]','{','{"demonstration":{},"demonstration":{}}','{"x":NaN}'])
def test_loader_rejects_nonfinite_duplicate_or_malformed_json(tmp_path,text):
    path=tmp_path/"invalid.json";path.write_text(text)
    with pytest.raises(ScenarioError): load_mission_scenario(path)


def test_input_data_is_not_retained_or_mutated(config):
    before=deepcopy(config)
    case=mission_scenario_from_dict(config)
    assert config==before
    config["agents"][0]["position"]["x"]=9
    assert case.scenario.initial_world.agents[0].position.x==1


def test_original_scenario_and_fixture_contracts_are_preserved():
    legacy=load_scenario(Path(__file__).resolve().parents[1]/"scenarios/four_room_disaster.json")
    assert all(a.position.x is None for a in legacy.initial_world.agents)
    assert legacy.mission.duration_ticks==16 and len(legacy.events)==2
    assert not legacy.allow_infeasible_objectives
    assert legacy.create_engine(with_execution=True).recovery_enabled is False


def test_corridor_initial_positions_are_separate_and_do_not_assign_tasks():
    case=get_scenario("corridor")
    engine=case.create_engine()
    assert {a.position.location_id for a in engine.world.agents}=={"corridor"}
    assert len({a.position for a in engine.world.agents})==4
    assert all(point_allowed(case.scenario.floor_plan,a.position) for a in engine.world.agents)
    assert all(t.status is TaskStatus.PENDING and t.owner_id is None for t in engine.world.tasks)
    assert not engine.commit_trace and not engine.execution_trace
    assert all(len(engine.known_tasks(a))==4 for a in "ABCD")


def test_scenario_expected_behaviors_use_actual_end_to_end_results(finished):
    case,engine,frames,routes=finished
    summary=verify_expected(case,engine)
    assert summary.total_objectives==case.expected.completed_objectives+case.expected.incomplete_objectives
    assert summary.ticks==case.scenario.mission.duration_ticks
    assert frames[-1]==engine.world and routes
    assert engine.state is RunState.FINISHED


def test_all_admitted_assignments_have_real_eligible_bids_and_protocol_proofs(finished):
    case,engine,_,_=finished
    assert len(engine.commit_trace)>=3
    for feedback in engine.commit_trace:
        if not feedback.accepted: continue
        req=feedback.request
        rounds=[r for owner in "ABCD" for r in engine.allocation_snapshot(owner)
                if r.round.round_id==req.round_id and r.round.coordinator_id==owner]
        assert len(rounds)==1 and rounds[0].bids
        assert min(rounds[0].bids,key=bid_order).message.sender_id==req.agent_id
        history=[r for batch in engine.suitability_history(req.agent_id) for r in batch
                 if r.task_id==req.task_id and r.evaluation_tick==feedback.applied_at]
        assert any(r.eligibility.eligible and r.score is not None for r in history)
        assert len(set(req.evidence_ids))==4
    assert any(r.action=="processed" and 'allocation.v1' in r.envelope.payload for r in engine.communication.trace)
    assert all(not ({"winner","replacement_agent","waypoints","priority_score"}&set(t)) for t in json.loads(SCENARIO_FILES[case.scenario_id].read_text())["tasks"])


def test_every_route_uses_valid_geometry_and_all_actual_steps_are_bounded(finished):
    case,engine,frames,routes=finished
    for (agent,task_id),route in routes.items():
        task=next(t for t in engine.world.tasks if t.task_id==task_id)
        validate_route(case.scenario.floor_plan,engine.world.environment,route[0],route,route[-1])
        assert route[-1].location_id==task.location_id
        for a,b in zip(route,route[1:]):
            if a.location_id!=b.location_id: assert distance(a,b)==0
    previous={a.identity.agent_id:a.position for a in case.scenario.initial_world.agents}
    for frame in frames:
        for agent in frame.agents:
            assert point_allowed(case.scenario.floor_plan,agent.position)
            assert distance(previous[agent.identity.agent_id],agent.position)<=1.5+1e-8
            previous[agent.identity.agent_id]=agent.position


def test_no_tick_has_double_ownership_or_overlapping_active_attempts(finished):
    case,engine,frames,_=finished
    objectives={a.task.task_id:o.objective_id for o in engine.recovery_snapshot() for a in o.attempts}
    for frame in frames:
        active=[t for t in frame.tasks if t.status in {TaskStatus.ASSIGNED,TaskStatus.IN_PROGRESS}]
        assert len(active)==len({t.owner_id for t in active})
        assert len(active)==len({objectives[t.task_id] for t in active})
        for agent in frame.agents:
            assert (agent.task_id is not None)==(agent.availability is Availability.BUSY)


def test_capability_gated_observations_match_actual_positions_and_energy_is_spent(finished):
    case,engine,frames,_=finished
    starts={a.identity.agent_id:a for a in case.scenario.initial_world.agents}
    end={a.identity.agent_id:a for a in engine.world.agents}
    moved={e.agent_id for e in engine.execution_trace if e.action=="moved"}
    for agent in moved:
        assert end[agent].battery<starts[agent].battery
    for agent in "ABCD":
        for entry in engine.observation_history(agent):
            obs=entry.observation
            source=starts[obs.source_id]
            if obs.sensing_capability.startswith("operational:"):
                assert obs.sensing_capability.split(":",1)[1] in source.capabilities.operational
            else:
                assert obs.sensing_capability in source.capabilities.sensing
            actual=next(a for a in frames[obs.observed_at].agents if a.identity.agent_id==obs.source_id)
            assert actual.position.location_id==obs.location_id
    assert all(0<=a.battery<=1 and all(0<=r.amount<=r.capacity for r in a.resources) for a in engine.world.agents)


def test_mission_summary_and_events_are_read_only_truthful_and_not_hazard_mitigation(finished):
    case,engine,_,_=finished
    before=replay_signature(engine)
    summary=mission_summary(engine,case.display_name)
    assert summary.completed_objectives+summary.incomplete_objectives==summary.total_objectives
    assert summary.failed_execution_attempts==sum(a.task.status is TaskStatus.FAILED for o in engine.recovery_snapshot() for a in o.attempts)
    assert summary.final_battery==tuple(sorted((a.identity.agent_id,a.battery) for a in engine.world.agents))
    assert any(e.category=="ownership confirmed" for e in significant_events(engine))
    assert not any("heartbeat"==e.category for e in significant_events(engine))
    initial_gas=next(c.value for c in case.scenario.initial_world.conditions if c.subject=="gas")
    assert {c.subject:c.value for c in engine.world.conditions}["gas"]==initial_gas
    assert replay_signature(engine)==before


def test_reset_and_fresh_instance_repeat_complete_case_histories(finished):
    case,reference,_,_=finished
    engine=case.create_engine()
    assert engine.world is not reference.world
    engine.run(engine.mission.duration_ticks)
    assert replay_signature(engine)==replay_signature(reference)
    result=replay_signature(engine)
    initial=case.create_engine().world
    engine.reset()
    assert engine.world==initial and engine.time.tick==0
    assert not engine.execution_trace and not engine.communication.trace and not engine.recovery_trace
    assert all(len(engine.known_tasks(a))==4 for a in "ABCD")
    engine.run(engine.mission.duration_ticks)
    assert replay_signature(engine)==result


def test_distributed_proximity_is_not_a_capability_override():
    case=get_scenario("distributed")
    engine=case.create_engine();engine.start()
    a=next(x for x in engine.world.agents if x.identity.agent_id=="A")
    assert a.position.location_id=="room-2"
    gas=next(r for r in engine.suitability_snapshot("A") if r.task_id=="task-2")
    assert not gas.eligibility.eligible and "Missing sensing: gas" in gas.eligibility.explanation
    engine.run(engine.mission.duration_ticks)
    gas_task=next(t for t in engine.world.tasks if t.task_id=="task-2")
    owner=next(x for x in engine.world.agents if x.identity.agent_id==gas_task.owner_id)
    assert "gas" in owner.capabilities.sensing and gas_task.owner_id!="A"


def test_emergency_is_learned_locally_and_priority_changes_follow_observations():
    case=get_scenario("emergency");engine=case.create_engine()
    views={a:[] for a in "ABCD"}
    for a in "ABCD": engine.register_controller(a,lambda v,a=a:views[a].append(v) or ())
    engine.run(13)  # tick 12 just processed
    assert any(t.task_id=="emergency-verification" for t in engine.world.tasks)
    assert any(a.task.task_id=="emergency-verification" for a in views["B"][12].known_tasks)
    assert all(not any(a.task.task_id=="emergency-verification" for a in views[p][12].known_tasks) for p in "ACD")
    assert "UNKNOWN" in decision_explanation(engine,"C","emergency-verification")[0]
    assert not any(e.observation.value=="severe" for a in "ABCD" for e in engine.observation_history(a))
    engine.run(engine.mission.duration_ticks-engine.time.tick)
    for a in "ABCD":
        received=min(e.received_at for e in engine.observation_history(a) if e.observation.subject=="fire" and e.observation.value=="severe")
        assert received>=12
        before=next(r.score for r in views[a][11].priorities if r.task_id=="task-1")
        after=next(r.score for r in views[a][received].priorities if r.task_id=="task-1")
        assert after>before
        assert all(not hasattr(views[a][received],field) for field in ("world","scenario","conditions","events"))
    assert all(any(a.task.task_id=="emergency-verification" for a in views[p][13].known_tasks) for p in "ABCD")
    summary=verify_expected(case,engine)
    assert summary.failed_execution_attempts==0  # no invented priority preemption
    assert not any(e.action=="failed" for e in engine.execution_trace)


def test_failure_and_link_outage_are_distinct_and_recovery_uses_received_bids():
    case=get_scenario("failure-disruption");engine=case.create_engine()
    frames=[]
    engine.register_system("connection-safety",Phase.RECORD,lambda s:frames.append(s.world))
    engine.run(engine.mission.duration_ticks)
    obj=next(o for o in engine.recovery_snapshot() if len(o.attempts)>1)
    failed,success=obj.attempts
    assert failed.ended_at==10 and failed.task.status is TaskStatus.FAILED
    assert success.task.status is TaskStatus.COMPLETED and success.previous_owner!=failed.previous_owner
    assert all(next(a for a in f.agents if a.identity.agent_id=="C").availability not in {Availability.OFFLINE,Availability.FAILED} for f in frames[11:22])
    assert any(e.agent_id=="C" and e.action=="completed" and 11<=e.tick<22 for e in engine.execution_trace)
    assert not any(r.action=="sent" and r.envelope.sender_id==failed.previous_owner and r.tick>=10 for r in engine.communication.trace)
    assert any(r.action=="dropped" and r.reason=="disconnected" for r in engine.communication.trace)
    assert len(next(o for o in engine.recovery_snapshot() if o.objective_id=="task-3").attempts)==1
    proof=next(f for f in engine.commit_trace if f.accepted and f.request.task_id==success.task.task_id)
    coordinator_round=next(r for owner in "ABCD" for r in engine.allocation_snapshot(owner) if r.round.round_id==proof.request.round_id and r.round.coordinator_id==owner)
    assert min(coordinator_round.bids,key=bid_order).message.sender_id==success.previous_owner
    assert mission_summary(engine).agent_failures==1


def test_constrained_mission_explains_close_agents_no_bid_and_consumes_resources_once():
    case=get_scenario("constrained");engine=case.create_engine();engine.start()
    first={a:{r.task_id:r for r in engine.suitability_snapshot(a)} for a in "ABCD"}
    assert not first["A"]["task-1"].eligibility.eligible
    assert "Battery" in first["A"]["task-1"].eligibility.explanation
    assert "Missing sensing: gas" in first["C"]["task-2"].eligibility.explanation
    assert "Missing operational: exploration" in first["D"]["task-4"].eligibility.explanation
    assert all(not first[a]["task-4"].eligibility.eligible for a in "ABCD")
    engine.run(engine.mission.duration_ticks)
    summary=verify_expected(case,engine)
    assert summary.pending_objectives==1 and summary.permanently_failed_objectives==0
    assert dict(summary.final_owners)["task-4"] is None
    assert any(e.action=="send NO_BID" and "Battery" in e.detail for e in engine.allocation_history("A"))
    gas=next(t for t in engine.world.tasks if t.task_id=="task-2")
    owner=next(a for a in engine.world.agents if a.identity.agent_id==gas.owner_id)
    assert next(r.amount for r in owner.resources if r.name=="support-kits")==0
    explanation=decision_explanation(engine,"A","task-1")
    assert any("NO_BID" in line for line in explanation) and any("Battery" in line for line in explanation)


def test_unrecoverable_capacity_loss_remains_explicit_incomplete():
    case=get_scenario("failure-disruption");engine=case.create_engine()
    def disable_replacements(sim,event):
        for owner in "ABC":
            own=next(a for a in sim.world.agents if a.identity.agent_id==owner)
            assert own.task_id is None
            sim.update_agent(replace(own,availability=Availability.OFFLINE,updated_at=19))
    engine.schedule(DynamicEvent("no-replacements",19,EventKind.AGENT_FAILED,"mission","All replacement capacity lost"),disable_replacements)
    engine.run(engine.mission.duration_ticks)
    summary=mission_summary(engine)
    assert summary.completed_objectives==3 and summary.incomplete_objectives==1
    assert summary.pending_objectives==1 and summary.failed_execution_attempts==1
    with pytest.raises(AssertionError): verify_expected(case,engine)


def test_configured_latency_changes_real_transport_and_protocol_windows():
    case=get_scenario("corridor")
    case=replace(case,communication=replace(case.communication,latency_ticks=2))
    engine=case.create_engine();engine.run(engine.mission.duration_ticks)
    assert verify_expected(case,engine).completed_objectives==4
    delivered=[r for r in engine.communication.trace if r.action=="delivered"]
    assert delivered and all(r.tick-r.envelope.sent_at==2 for r in delivered)
    assert all(r.round.bid_deadline-r.round.opened_at==5 for a in "ABCD" for r in engine.allocation_snapshot(a))


def test_no_event_configuration_can_encode_an_assignment_or_movement_override(config):
    for action in ("assign_task","choose_replacement","move_agent","complete_task"):
        config["events"]=[{"id":"forged","tick":1,"action":action,"target":"A","description":"forbidden shortcut"}]
        with pytest.raises(ScenarioError,match="unsupported action"): mission_scenario_from_dict(config)


def test_distributed_information_quality_is_actual_direct_evidence():
    engine=get_scenario("distributed").create_engine();engine.run(1)
    person=next(r for r in engine.suitability_snapshot("C") if r.task_id=="task-3")
    information=next(f for f in person.factors if f.name=="information")
    assert information.value>0 and person.evidence
    assert all(not e.is_remote and e.observation.source_id=="C" and e.observation.subject=="person" for e in person.evidence)


def test_power_failure_immediately_releases_a_just_admitted_commitment():
    case=get_scenario("failure-disruption")
    failure=case.scenario.events[0]
    early=replace(failure,event=replace(failure.event,tick=7))
    case=replace(case,scenario=replace(case.scenario,events=(early,*case.scenario.events[1:])))
    engine=case.create_engine();engine.run(8)
    original=next(t for t in engine.world.tasks if t.task_id=="task-4")
    disabled=next(a for a in engine.world.agents if a.identity.agent_id==failure.event.target_id)
    assert original.status is TaskStatus.FAILED and original.owner_id is None
    assert disabled.availability is Availability.OFFLINE and disabled.task_id is None
    assert engine.execution_snapshot(disabled.identity.agent_id).outcome.tick==7
