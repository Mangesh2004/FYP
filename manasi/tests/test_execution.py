"""Geometry, actual command execution, outcomes, energy and decentralized boundaries."""
from dataclasses import asdict, replace
from math import ceil
from pathlib import Path
import json

import pytest

from coopai.allocation_messages import task_digest
from coopai.execution import AgentExecutor
from coopai.execution_demo import signature
from coopai.execution_models import (TOPIC, ExecutionConfiguration, ExecutionNotice, ExecutionPhase as EP,
                                    TaskExecutionSpec, decode_notice, encode_notice, scenario_execution_specs)
from coopai.geometry import AreaGeometry, FloorPlan, Rectangle
from coopai.interfaces import AgentCommand
from coopai.messages import CoordinationPayload, decode_payload, encode_payload
from coopai.models import (Availability, Capabilities, DynamicEvent, Environment, EventKind, Location,
                           MessageEnvelope, Position, ResourceLevel, TaskRequirements, TaskStatus)
from coopai.navigation import distance, located_position, plan_route, point_allowed, validate_route
from coopai.scenario import load_scenario
from coopai.simulation import Phase, RunState
from coopai.visualization import PlaybackController, capture_dashboard

PATH=Path(__file__).resolve().parents[1]/"scenarios/four_room_disaster.json"


@pytest.fixture
def scenario(): return load_scenario(PATH)


def engine_for(scenario, *, configuration=ExecutionConfiguration(), autonomous=True, ticks=None):
    if ticks: scenario=replace(scenario,mission=replace(scenario.mission,duration_ticks=ticks))
    engine=scenario.create_engine(with_allocation=True)
    engine.enable_execution(scenario.floor_plan,scenario_execution_specs(scenario.task_details),configuration,autonomous=autonomous)
    return engine


def command(view, action, task_id, data=None):
    return AgentCommand(f"test-{view.own_state.identity.agent_id}-{view.time.tick}-{action}",view.own_state.identity.agent_id,
                        view.time.tick,action,task_id,json.dumps(data or {},sort_keys=True))


def nav_data(view, scenario, task_id):
    task=next(a.task for a in view.known_tasks if a.task.task_id==task_id)
    route=plan_route(scenario.floor_plan,view.own_state.position,task.location_id)
    return {"task_base_digest":task_digest(replace(task,status=TaskStatus.PENDING,owner_id=None)),"waypoints":[asdict(p) for p in route]}


def report_ids(view, subject, method):
    return [o.observation_id for o in view.observations if o.subject==subject and o.sensing_capability==method and o.source_id==view.own_state.identity.agent_id]


@pytest.mark.parametrize("changes", [{"x":None,"y":1},{"x":1,"y":None},{"x":True,"y":0},
    {"x":float("nan"),"y":0},{"x":0,"y":float("inf")}])
def test_invalid_position_coordinates(changes):
    with pytest.raises(ValueError): Position("loc",**changes)


def test_coordinates_are_optional_and_finite_negative_coordinates_allowed():
    assert Position("loc").x is None
    assert Position("loc",-2,-3).x==-2


@pytest.mark.parametrize("changes", [{"speed_units_per_tick":0},{"speed_units_per_tick":True},
    {"movement_energy_per_unit":-1},{"execution_energy_per_tick":float("nan")},
    {"minimum_operating_battery":0},{"minimum_operating_battery":1.1},{"arrival_tolerance":0},
    {"evidence_max_age_ticks":True},{"inspection_confidence":1.1},{"notification_interval_ticks":0}])
def test_invalid_execution_configuration(changes):
    with pytest.raises(ValueError): ExecutionConfiguration(**changes)


@pytest.mark.parametrize("arguments", [("t","custom",0,"fire","thermal",None),
    ("t","custom",1,"fire",None,None),("t","custom",1,"fire","thermal","exploration")])
def test_invalid_execution_procedures(arguments):
    with pytest.raises(ValueError): TaskExecutionSpec(*arguments)


def test_graph_doorway_route_is_not_wall_crossing_euclidean_path(scenario):
    plan=scenario.floor_plan
    start=Position("room-1",0.8,1.7)
    route=plan_route(plan,start,"room-4")
    assert route[0]==start and route[-1]==Position("room-4",8,8)
    assert {p.location_id for p in route}=={"room-1","corridor","room-4"}
    crossings=[(a,b) for a,b in zip(route,route[1:]) if a.location_id!=b.location_id]
    assert len(crossings)==2 and all(distance(a,b)==0 for a,b in crossings)
    validate_route(plan,plan.environment(),start,route,route[-1])
    for a,b in crossings:
        assert point_allowed(plan,a) and point_allowed(plan,b)
    with pytest.raises(ValueError,match="doorway"):
        validate_route(plan,plan.environment(),start,(start,route[-1]),route[-1])


def test_wall_boundary_following_between_doors_is_rejected(scenario):
    plan=scenario.floor_plan
    start=Position("corridor",1.5,4)
    end=Position("corridor",7.5,4)
    with pytest.raises(ValueError,match="solid wall"):
        validate_route(plan,plan.environment(),start,(start,end),end)
    route=plan_route(plan,start,"room-2")
    assert Position("corridor",5,5) in route
    validate_route(plan,plan.environment(),start,route,route[-1])


def test_same_location_route_has_no_teleport_and_spawn_initialization(scenario):
    plan=scenario.floor_plan
    center=located_position(plan,Position("room-1"))
    assert center==Position("room-1",2,2)
    assert plan_route(plan,center,"room-1")== (center,)
    inside=Position("room-1",1,1)
    assert plan_route(plan,inside,"room-1")== (inside,center)
    with pytest.raises(ValueError): located_position(plan,Position("room-1",0,0))
    with pytest.raises(ValueError): located_position(plan,Position("room-1",5,5))


def test_disconnected_and_directed_routes(scenario):
    a=AreaGeometry(Location("a","A"),"room",Rectangle(0,0,2,2))
    b=AreaGeometry(Location("b","B"),"room",Rectangle(5,0,2,2))
    isolated=FloorPlan((a,b),())
    assert plan_route(isolated,Position("a"),"b") is None
    with pytest.raises(ValueError): plan_route(isolated,Position("a"),"unknown")
    topology=scenario.floor_plan.environment()
    directed=replace(topology,connections=tuple(e for e in topology.connections if e.source=="corridor"))
    assert plan_route(scenario.floor_plan,Position("room-1"),"room-2",directed) is None
    assert plan_route(scenario.floor_plan,Position("corridor"),"room-2",directed)


def test_geometry_route_determinism_and_negative_origins(scenario):
    plan=scenario.floor_plan
    reversed_plan=FloorPlan(plan.areas[::-1],plan.passages[::-1])
    assert plan_route(plan,Position("room-1"),"room-4")==plan_route(reversed_plan,Position("room-1"),"room-4")
    translated=FloorPlan(tuple(replace(a,bounds=replace(a.bounds,x=a.bounds.x-20,y=a.bounds.y-10)) for a in plan.areas),
                         tuple(replace(p,opening=replace(p.opening,x1=p.opening.x1-20,x2=p.opening.x2-20,y1=p.opening.y1-10,y2=p.opening.y2-10)) for p in plan.passages))
    original=plan_route(plan,Position("room-1"),"room-4")
    shifted=plan_route(translated,Position("room-1"),"room-4")
    assert [(p.x-20,p.y-10,p.location_id) for p in original]==[(p.x,p.y,p.location_id) for p in shifted]


@pytest.mark.parametrize("change", ["wrong_start","wrong_goal","solid_wall","outside","empty"])
def test_bad_routes_are_rejected(scenario,change):
    plan=scenario.floor_plan; start=Position("corridor",5,5); route=plan_route(plan,start,"room-4"); goal=route[-1]
    bad={"wrong_start":(Position("corridor",4,5),*route[1:]),"wrong_goal":(*route[:-1],Position("room-4",9,9)),
         "solid_wall":(start,Position("corridor",4,6),goal),"outside":(start,Position("corridor",50,50),goal),"empty":()}[change]
    with pytest.raises(ValueError): validate_route(plan,plan.environment(),start,bad,goal)


def test_actual_allocation_execution_timing_and_environment_outcomes(scenario):
    engine=engine_for(scenario); engine.run(16)
    assert {t.task_id:(t.status,t.owner_id) for t in engine.world.tasks}=={
        "task-1":(TaskStatus.COMPLETED,"A"),"task-2":(TaskStatus.COMPLETED,"B"),
        "task-3":(TaskStatus.COMPLETED,"C"),"task-4":(TaskStatus.COMPLETED,"D")}
    assert [(f.request.task_id,f.applied_at) for f in engine.commit_trace]==[("task-2",3),("task-1",6),("task-3",6),("task-4",6)]
    first=lambda agent,action:next(e.tick for e in engine.execution_trace if e.agent_id==agent and e.action==action)
    assert [first(a,"arrived") for a in "ABCD"]==[8,5,8,11]
    assert [first(a,"execution started") for a in "ABCD"]==[9,6,9,12]
    assert [first(a,"completed") for a in "ABCD"]==[12,9,13,15]
    assert next(e.tick for e in engine.execution_trace if e.action=="movement started")==8
    assert all(a.availability is Availability.AVAILABLE and a.task_id is None for a in engine.world.agents)
    assert {c.subject:c.value for c in engine.world.conditions}=={"fire":"severe","gas":"present","person":"possible","hazard":"clear"}
    for agent in "ABCD":
        outcome=engine.execution_snapshot(agent).outcome
        assert outcome.success and outcome.findings and all(o.source_id==agent for o in outcome.findings)
        assert "hazards unchanged" in outcome.reason


def test_movement_speed_distance_energy_and_location_transition(scenario):
    engine=engine_for(scenario); initial=engine.world
    engine.run(8)
    own=lambda:next(a for a in engine.world.agents if a.identity.agent_id=="D")
    start=own().position
    assert start==Position("corridor",5,5)
    assert engine.execution_snapshot("D").phase is EP.IDLE
    engine.run(1); first=own().position
    assert distance(start,first)==pytest.approx(1.5) and first.location_id=="corridor"
    assert own().battery==pytest.approx(0.985)
    engine.run(1)
    assert own().position.location_id=="room-4" and own().battery==pytest.approx(0.97)
    engine.run(2)
    assert own().position==Position("room-4",8,8) and engine.execution_snapshot("D").phase is EP.ARRIVED
    route=engine.execution_snapshot("D").route
    length=sum(distance(a,b) for a,b in zip(route,route[1:]))
    assert own().battery==pytest.approx(1-length*0.01)
    assert all(point_allowed(scenario.floor_plan,e.position) for e in engine.execution_trace)


@pytest.mark.parametrize("speed", [0.5,1,1.5,3,10])
def test_configured_speed_changes_arrival_not_total_travel_energy(scenario,speed):
    engine=engine_for(scenario,configuration=ExecutionConfiguration(speed_units_per_tick=speed),ticks=40)
    engine.run(40)
    state=engine.execution_snapshot("D")
    length=sum(distance(a,b) for a,b in zip(state.route,state.route[1:]))
    arrival=next(e.tick for e in engine.execution_trace if e.agent_id=="D" and e.action=="arrived")
    assert arrival==8+ceil(length/speed)-1
    own=next(a for a in engine.world.agents if a.identity.agent_id=="D")
    assert own.battery==pytest.approx(1-length*0.01-2*0.01)
    assert state.phase is EP.COMPLETED


def test_battery_event_is_not_forced_failure_and_low_battery_stop_is_clean(scenario):
    engine=engine_for(scenario)
    def low(sim,event):
        own=next(a for a in sim.world.agents if a.identity.agent_id=="D")
        sim.update_agent(replace(own,battery=0.03,updated_at=9))
    engine.schedule(DynamicEvent("low",9,EventKind.RESOURCES_CHANGED,"D","controlled fixture"),low)
    engine.run(16)
    d=next(a for a in engine.world.agents if a.identity.agent_id=="D")
    b=next(a for a in engine.world.agents if a.identity.agent_id=="B")
    assert d.battery==0.03 and d.availability is Availability.OFFLINE and d.task_id is None
    task=next(t for t in engine.world.tasks if t.task_id=="task-4")
    assert task.status is TaskStatus.FAILED and task.owner_id is None
    state=engine.execution_snapshot("D")
    assert state.phase is EP.FAILED and not state.outcome.success and "Battery" in state.outcome.reason
    assert not any(e.agent_id=="D" and e.action=="moved" and e.tick>=9 for e in engine.execution_trace)
    assert b.battery==pytest.approx(0.38) and engine.execution_snapshot("B").outcome.success


def test_insufficient_next_step_energy_never_goes_negative_or_partially_moves(scenario):
    engine=engine_for(scenario,configuration=ExecutionConfiguration(movement_energy_per_unit=0.7))
    engine.run(16)
    state=engine.execution_snapshot("D")
    own=next(a for a in engine.world.agents if a.identity.agent_id=="D")
    assert state.phase is EP.FAILED and own.position==Position("corridor",5,5) and own.battery==1
    assert "energy cost" in state.outcome.reason
    assert all(0<=a.battery<=1 for a in engine.world.agents)


def test_resource_consumption_exactly_once_and_continuation_credit(scenario):
    task=next(t for t in scenario.initial_world.tasks if t.task_id=="task-4")
    task=replace(task,requirements=replace(task.requirements,resources=(ResourceLevel("support-kits",1,2),)))
    world=replace(scenario.initial_world,tasks=tuple(task if t.task_id==task.task_id else t for t in scenario.initial_world.tasks))
    engine=engine_for(replace(scenario,initial_world=world)); engine.run(13)
    own=next(a for a in engine.world.agents if a.identity.agent_id=="D")
    assert next(r.amount for r in own.resources if r.name=="support-kits")==1
    assert engine.execution_snapshot("D").consumed_resources==(ResourceLevel("support-kits",1,2),)
    assert next(r.eligibility.eligible for r in engine.suitability_snapshot("D") if r.task_id=="task-4")
    engine.run(3)
    own=next(a for a in engine.world.agents if a.identity.agent_id=="D")
    assert next(r.amount for r in own.resources if r.name=="support-kits")==1
    assert engine.execution_snapshot("D").outcome.success


def test_agent_disablement_and_invalidated_task_stop_movement(scenario):
    engine=engine_for(scenario)
    def disable(sim,event):
        own=next(a for a in sim.world.agents if a.identity.agent_id=="D")
        task=next(t for t in sim.world.tasks if t.task_id==own.task_id).transition(TaskStatus.FAILED)
        disabled=replace(own,availability=Availability.FAILED,task_id=None,updated_at=9)
        sim.replace_world(replace(sim.world,agents=tuple(disabled if a.identity.agent_id=="D" else a for a in sim.world.agents),tasks=tuple(task if t.task_id==task.task_id else t for t in sim.world.tasks)))
    engine.schedule(DynamicEvent("disable",9,EventKind.AGENT_FAILED,"D","disabled fixture"),disable)
    engine.run(16)
    state=engine.execution_snapshot("D")
    assert state.phase is EP.FAILED and "disabled" in state.outcome.reason
    assert not any(e.agent_id=="D" and e.action=="moved" and e.tick>=9 for e in engine.execution_trace)


def test_unowned_movement_command_rejected_without_inventing_assignment(scenario):
    engine=engine_for(scenario,autonomous=False)
    engine.register_controller("D",lambda v:(command(v,"navigate","task-1",{"task_base_digest":"x","waypoints":[]}),) if v.time.tick==0 else ())
    engine.run(2)
    assert engine.execution_snapshot("D").phase is EP.IDLE
    assert next(a.position for a in engine.world.agents if a.identity.agent_id=="D")==Position("corridor",5,5)
    assert all(t.status is TaskStatus.PENDING for t in engine.world.tasks)
    assert any(e.action=="command rejected" and "confirmed" in e.reason for e in engine.execution_trace)


@pytest.mark.parametrize("action", ["begin","work","complete"])
def test_execution_without_mission_acceptance_or_arrival_cannot_succeed(scenario,action):
    engine=engine_for(scenario,autonomous=False)
    engine.register_controller("D",lambda v:(command(v,action,"task-4",{} if action=="begin" else {"observation_ids":["fake"]}),) if v.time.tick==7 else ())
    engine.run(10)
    assert engine.execution_snapshot("D").phase is EP.FAILED
    assert next(t.status for t in engine.world.tasks if t.task_id=="task-4") is TaskStatus.FAILED
    assert "Mission not accepted" in engine.execution_snapshot("D").outcome.reason


def test_begin_before_arrival_fails_owned_task_and_stops_route(scenario):
    engine=engine_for(scenario,autonomous=False)
    def controller(view):
        if view.time.tick==7: return (command(view,"navigate","task-4",nav_data(view,scenario,"task-4")),)
        if view.time.tick==8: return (command(view,"begin","task-4"),)
        return ()
    engine.register_controller("D",controller); engine.run(16)
    assert engine.execution_snapshot("D").phase is EP.FAILED
    assert "arrival" in engine.execution_snapshot("D").outcome.reason
    assert len([e for e in engine.execution_trace if e.agent_id=="D" and e.action=="moved"])==1


def test_invalid_wall_shortcut_command_fails_without_pose_update(scenario):
    engine=engine_for(scenario,autonomous=False)
    def controller(view):
        if view.time.tick!=7: return ()
        data=nav_data(view,scenario,"task-4")
        data["waypoints"]=[data["waypoints"][0],data["waypoints"][-1]]
        return (command(view,"navigate","task-4",data),)
    engine.register_controller("D",controller); engine.run(10)
    assert engine.execution_snapshot("D").phase is EP.FAILED
    assert "doorway" in engine.execution_snapshot("D").outcome.reason
    assert next(a.position for a in engine.world.agents if a.identity.agent_id=="D")==Position("corridor",5,5)


def test_current_capability_loss_prevents_execution(scenario):
    engine=engine_for(scenario)
    def capabilities(sim,event):
        own=next(a for a in sim.world.agents if a.identity.agent_id=="A")
        sim.update_agent(replace(own,capabilities=Capabilities(operational=own.capabilities.operational),updated_at=10))
    engine.schedule(DynamicEvent("capability-loss",10,EventKind.RESOURCES_CHANGED,"A","capability fixture"),capabilities)
    engine.run(16)
    assert engine.execution_snapshot("A").phase is EP.FAILED and "thermal" in engine.execution_snapshot("A").outcome.reason


def test_valid_work_duration_is_not_automatic_completion(scenario):
    engine=engine_for(scenario,autonomous=False)
    def controller(view):
        if view.time.tick==4: return (command(view,"navigate","task-2",nav_data(view,scenario,"task-2")),)
        if view.time.tick==5: return (command(view,"begin","task-2"),)
        if view.time.tick in {6,7}: return (command(view,"work","task-2",{"observation_ids":report_ids(view,"gas","gas")}),)
        return ()
    engine.register_controller("B",controller); engine.run(16)
    state=engine.execution_snapshot("B")
    assert state.progress_ticks==2 and state.phase is EP.EXECUTING and state.outcome is None
    assert next(t.status for t in engine.world.tasks if t.task_id=="task-2") is TaskStatus.IN_PROGRESS


def test_premature_completion_and_fake_evidence_never_succeed(scenario):
    engine=engine_for(scenario,autonomous=False)
    def controller(view):
        if view.time.tick==4: return (command(view,"navigate","task-2",nav_data(view,scenario,"task-2")),)
        if view.time.tick==5: return (command(view,"begin","task-2"),)
        if view.time.tick==6: return (command(view,"complete","task-2",{"observation_ids":report_ids(view,"gas","gas")}),)
        return ()
    engine.register_controller("B",controller); engine.run(10)
    assert engine.execution_snapshot("B").phase is EP.FAILED
    assert "duration" in engine.execution_snapshot("B").outcome.reason


def test_changed_environment_requires_new_evidence_not_timer_success(scenario):
    engine=engine_for(scenario)
    def condition(sim,event):
        sim.replace_world(replace(sim.world,conditions=tuple(replace(c,value="absent") if c.subject=="fire" else c for c in sim.world.conditions)))
    engine.schedule(DynamicEvent("fire-changes-again",11,EventKind.HAZARD_CHANGED,"room-1","condition fixture"),condition)
    engine.run(16)
    state=engine.execution_snapshot("A")
    assert state.phase is EP.FAILED and "fresh sensing" in state.outcome.reason
    assert next(t.status for t in engine.world.tasks if t.task_id=="task-1") is TaskStatus.FAILED


def test_missing_environment_condition_is_unknown_not_success(scenario):
    world=replace(scenario.initial_world,conditions=tuple(c for c in scenario.initial_world.conditions if c.subject!="hazard"))
    engine=engine_for(replace(scenario,initial_world=world));engine.run(16)
    assert engine.execution_snapshot("D").phase is EP.FAILED
    assert "No fresh own observation" in engine.execution_snapshot("D").outcome.reason


def test_dynamic_perception_and_operational_inspection_no_sensor_invention(scenario):
    engine=engine_for(scenario);engine.run(12)
    assert all(e.is_remote for e in engine.observation_history("D"))
    engine.run(4)
    direct=[e.observation for e in engine.observation_history("D") if not e.is_remote]
    assert direct and all(o.location_id=="room-4" and o.subject=="hazard" and o.sensing_capability=="operational:exploration" for o in direct)
    assert [o.observed_at for o in direct]==[12,13,14]
    assert next(a.capabilities.sensing for a in engine.world.agents if a.identity.agent_id=="D")==frozenset()
    assert any(e.is_remote and e.observation.source_id=="D" and e.observation.subject=="hazard" for e in engine.observation_history("A"))


def test_local_views_hide_world_routes_and_unknown_tasks(scenario):
    engine=engine_for(scenario)
    from coopai.models import Task
    engine.replace_world(replace(engine.world,tasks=(*engine.world.tasks,Task("secret","room-4"))))
    views=[];engine.register_controller("A",lambda v:views.append(v) or ())
    engine.run(16)
    assert all(v.execution.agent_id=="A" and all(f.agent_id=="A" for f in v.execution_feedback) for v in views)
    assert not hasattr(views[0],"world") and not hasattr(views[0],"other_routes")
    assert not hasattr(AgentExecutor("A",scenario.floor_plan,scenario_execution_specs(scenario.task_details)),"world")
    assert not any(e.task_id=="secret" for e in engine.execution_trace)
    assert next(t.status for t in engine.world.tasks if t.task_id=="secret") is TaskStatus.PENDING


def test_pause_step_resume_reset_and_replay_positions_energy_reports(scenario):
    engine=engine_for(scenario);controls=PlaybackController(engine)
    controls.play();engine.run(9);controls.pause()
    world=engine.world;events=engine.execution_trace
    assert controls.advance(50)==0 and engine.world is world and engine.execution_trace==events
    controls.single_step()
    assert engine.time.tick==10 and engine.state is RunState.PAUSED
    assert next(a.position for a in engine.world.agents if a.identity.agent_id=="D").location_id=="room-4"
    controls.play();engine.run(6)
    original=signature(engine); baseline=engine._baseline[0]
    controls.reset()
    assert engine.world==baseline and engine.execution_trace==() and all(engine.execution_snapshot(a).phase is EP.IDLE for a in "ABCD")
    engine.run(16);assert signature(engine)==original


def test_status_notices_are_typed_own_messages_and_terminal_tasks_not_reoffered(scenario):
    engine=engine_for(scenario,ticks=24);engine.run(24)
    notices=[]
    for record in engine.communication.trace:
        payload=decode_payload(record.envelope)
        if isinstance(payload,CoordinationPayload) and payload.topic==TOPIC:
            notices.append(decode_notice(payload,record.envelope))
    assert any(n.status is TaskStatus.COMPLETED and n.agent_id=="D" for n in notices)
    assert all(n.commit_reference.startswith("commit-request:") for n in notices)
    assert all(a.task.status is TaskStatus.COMPLETED for agent in "ABCD" for a in engine.known_tasks(agent))
    assert not any(e.action=="round opened" and e.tick>=10 for agent in "ABCD" for e in engine.allocation_history(agent))
    assert len(engine.commit_trace)==4


def test_execution_notice_validation_and_sender_binding():
    notice=ExecutionNotice("t","A",TaskStatus.COMPLETED,2,5,"a"*64,"commit-ref","inspected")
    kind,text=encode_payload(CoordinationPayload(TOPIC,encode_notice(notice)))
    envelope=MessageEnvelope("m","A","B",kind,5,text,9)
    assert decode_notice(decode_payload(envelope),envelope)==notice
    with pytest.raises(ValueError): decode_payload(replace(envelope,sender_id="C"))
    with pytest.raises(ValueError): replace(notice,status=TaskStatus.PENDING)
    with pytest.raises(ValueError): replace(notice,sequence=0)
    with pytest.raises(ValueError): replace(notice,task_base_digest="invalid")


def test_multiple_work_commands_cannot_accelerate_execution(scenario):
    engine=engine_for(scenario,autonomous=False)
    def controller(view):
        if view.time.tick==4: return (command(view,"navigate","task-2",nav_data(view,scenario,"task-2")),)
        if view.time.tick==5: return (command(view,"begin","task-2"),)
        if view.time.tick==6:
            c=command(view,"work","task-2",{"observation_ids":report_ids(view,"gas","gas")})
            return c,replace(c,command_id=c.command_id+"-second")
        return ()
    engine.register_controller("B",controller);engine.run(10)
    assert engine.execution_snapshot("B").progress_ticks==0 and engine.execution_snapshot("B").phase is EP.FAILED
    assert "one execution command" in engine.execution_snapshot("B").outcome.reason


def test_execution_opt_in_preserves_task9_and_setup_validation(scenario):
    legacy=scenario.create_engine(with_allocation=True);legacy.run(16)
    assert not legacy.execution_enabled and all(t.status is TaskStatus.ASSIGNED for t in legacy.world.tasks)
    engine=scenario.create_engine()
    with pytest.raises(ValueError): engine.enable_execution(scenario.floor_plan,scenario_execution_specs(scenario.task_details))
    engine=engine_for(scenario)
    with pytest.raises(ValueError): engine.enable_execution(scenario.floor_plan,scenario_execution_specs(scenario.task_details))
    engine.run(1)
    with pytest.raises(RuntimeError): engine.enable_execution(scenario.floor_plan,scenario_execution_specs(scenario.task_details))


def test_demo_actual_timing_outcomes_and_failure_replay(capsys):
    from coopai.execution_demo import main
    main();out=capsys.readouterr().out
    assert "status=completed assignment=6 movement_start=8 arrival=11 execution_start=12 completion=15" in out
    assert "battery=0.380000" in out and "failure=9" in out
    assert "execution_replay_equal=True" in out and "failure_replay_equal=True" in out


def test_fake_evidence_at_valid_duration_is_rejected(scenario):
    engine=engine_for(scenario,autonomous=False)
    def controller(view):
        if view.time.tick==4: return (command(view,"navigate","task-2",nav_data(view,scenario,"task-2")),)
        if view.time.tick==5: return (command(view,"begin","task-2"),)
        if view.time.tick in {6,7}: return (command(view,"work","task-2",{"observation_ids":report_ids(view,"gas","gas")}),)
        if view.time.tick==8: return (command(view,"complete","task-2",{"observation_ids":["made-up-reading"]}),)
        return ()
    engine.register_controller("B",controller);engine.run(12)
    state=engine.execution_snapshot("B")
    assert state.progress_ticks==2 and state.phase is EP.FAILED
    assert "own capability-supported" in state.outcome.reason and not state.outcome.success


def test_cancelled_owned_task_stops_without_illegal_failed_transition(scenario):
    engine=engine_for(scenario)
    def cancel(sim,event):
        own=next(a for a in sim.world.agents if a.identity.agent_id=="D")
        task=next(t for t in sim.world.tasks if t.task_id=="task-4").transition(TaskStatus.CANCELLED)
        own=replace(own,availability=Availability.AVAILABLE,task_id=None,updated_at=9)
        sim.replace_world(replace(sim.world,agents=tuple(own if a.identity.agent_id=="D" else a for a in sim.world.agents),tasks=tuple(task if t.task_id==task.task_id else t for t in sim.world.tasks)))
    engine.schedule(DynamicEvent("cancel-task",9,EventKind.TASK_DISCOVERED,"task-4","cancellation fixture"),cancel)
    engine.run(16)
    assert next(t.status for t in engine.world.tasks if t.task_id=="task-4") is TaskStatus.CANCELLED
    assert engine.execution_snapshot("D").phase is EP.FAILED
    assert not any(e.agent_id=="D" and e.action=="moved" and e.tick>=9 for e in engine.execution_trace)


def test_blocked_current_connection_rejects_remaining_route(scenario):
    engine=engine_for(scenario)
    def block(sim,event):
        topology=replace(sim.world.environment,connections=tuple(c for c in sim.world.environment.connections if not (c.source=="corridor" and c.target=="room-4")))
        sim.replace_world(replace(sim.world,environment=topology))
    engine.schedule(DynamicEvent("blocked-route",9,EventKind.HAZARD_CHANGED,"corridor","blocked connection fixture"),block)
    engine.run(16)
    assert engine.execution_snapshot("D").phase is EP.FAILED
    assert "Unreachable" in engine.execution_snapshot("D").outcome.reason
    assert next(a.position.location_id for a in engine.world.agents if a.identity.agent_id=="D")=="corridor"


def test_no_public_procedure_fails_confirmed_task_instead_of_leaving_busy_forever(scenario):
    engine=scenario.create_engine(with_allocation=True)
    specs=tuple(s for s in scenario_execution_specs(scenario.task_details) if s.task_id!="task-4")
    engine.enable_execution(scenario.floor_plan,specs)
    engine.run(16)
    own=next(a for a in engine.world.agents if a.identity.agent_id=="D")
    assert own.availability is Availability.AVAILABLE and own.task_id is None
    assert engine.execution_snapshot("D").phase is EP.FAILED
    assert "No public execution procedure" in engine.execution_snapshot("D").outcome.reason


def test_consumed_resource_credit_cannot_qualify_a_new_task():
    from coopai.eligibility import SelfEvaluationContext, HardEligibility
    from coopai.models import AgentState, AgentIdentity, Task
    from coopai.priority import TaskAnnouncement
    topology=Environment((Location("loc","Location"),))
    own=AgentState(AgentIdentity("A","A"),Capabilities(),Position("loc"),1,resources=(ResourceLevel("kit",0,1),))
    task=Task("t","loc",TaskRequirements(resources=(ResourceLevel("kit",1,1),)))
    ctx=SelfEvaluationContext(own,0,(TaskAnnouncement(task,"custom",0),),(),topology,(ResourceLevel("kit",1,1),))
    assert not HardEligibility().check("t",ctx).eligible


def test_execution_snapshot_terminal_invariants_and_command_json_validation():
    from coopai.execution_models import ExecutionSnapshot
    with pytest.raises(ValueError): ExecutionSnapshot("A",phase=EP.MOVING)
    with pytest.raises(ValueError): ExecutionSnapshot("A","t",phase=EP.COMPLETED,duration_ticks=1,progress_ticks=1)
    with pytest.raises(ValueError): ExecutionSnapshot("A","t",phase=EP.FAILED)
    with pytest.raises(ValueError): AgentCommand("c","A",0,"navigate","t",'[]')
    with pytest.raises(ValueError): AgentCommand("c","A",0,"navigate","t",'{"x":NaN}')


@pytest.mark.parametrize("duration", [1,2,4])
def test_configured_task_metadata_duration_controls_valid_progress_and_completion(scenario,duration):
    details=tuple(replace(d,duration_ticks=duration) if d.task_id=="task-4" else d for d in scenario.task_details)
    engine=engine_for(replace(scenario,task_details=details),ticks=28);engine.run(28)
    state=engine.execution_snapshot("D")
    assert state.duration_ticks==state.progress_ticks==duration and state.phase is EP.COMPLETED
    progress=[e for e in engine.execution_trace if e.agent_id=="D" and e.action=="progress"]
    assert len(progress)==duration
    complete=next(e.tick for e in engine.execution_trace if e.agent_id=="D" and e.action=="completed")
    assert complete==13+duration


def test_zero_energy_config_does_not_fabricate_battery_consumption(scenario):
    cfg=ExecutionConfiguration(movement_energy_per_unit=0,execution_energy_per_tick=0)
    engine=engine_for(scenario,configuration=cfg);engine.run(16)
    assert {a.identity.agent_id:a.battery for a in engine.world.agents}=={"A":1,"B":0.4,"C":0.95,"D":1}
    assert all(t.status is TaskStatus.COMPLETED for t in engine.world.tasks)


def test_no_move_without_the_agent_accepting_its_confirmed_assignment(scenario):
    engine=engine_for(scenario,autonomous=False);engine.run(16)
    assert all(t.status is TaskStatus.ASSIGNED for t in engine.world.tasks)
    assert engine.execution_trace==() and all(engine.execution_snapshot(a).phase is EP.IDLE for a in "ABCD")
    assert next(a.position for a in engine.world.agents if a.identity.agent_id=="D")==Position("corridor",5,5)


def test_peer_observation_cannot_substitute_for_actual_task_work(scenario):
    engine=engine_for(scenario,autonomous=False)
    def controller(view):
        if view.time.tick==4:return (command(view,"navigate","task-2",nav_data(view,scenario,"task-2")),)
        if view.time.tick==5:return (command(view,"begin","task-2"),)
        if view.time.tick==6:
            remote=next(e for e in view.evidence_history if e.is_remote)
            return (command(view,"work","task-2",{"observation_ids":[remote.observation.observation_id]}),)
        return ()
    engine.register_controller("B",controller);engine.run(10)
    assert engine.execution_snapshot("B").phase is EP.FAILED and "own capability-supported" in engine.execution_snapshot("B").outcome.reason


def test_failure_task_change_notice_is_only_shared_if_agent_operational(scenario):
    engine=engine_for(scenario,ticks=24)
    def cap(sim,event):
        own=next(a for a in sim.world.agents if a.identity.agent_id=="A")
        sim.update_agent(replace(own,capabilities=Capabilities(operational=own.capabilities.operational),updated_at=10))
    engine.schedule(DynamicEvent("lost-sensor",10,EventKind.RESOURCES_CHANGED,"A","capability loss"),cap)
    engine.run(24)
    assert all(next(a.task.status for a in engine.known_tasks(agent) if a.task.task_id=="task-1") is TaskStatus.FAILED for agent in "ABCD")
    assert len(engine.commit_trace)==4


def test_stale_execution_notice_does_not_overwrite_newer_local_requirements(scenario):
    engine=engine_for(scenario,ticks=24)
    def revision(sim,event):
        prior=next(a for a in sim.known_tasks("A") if a.task.task_id=="task-2")
        revised=replace(prior.task,requirements=replace(prior.task.requirements,minimum_battery=0.8))
        sim.announce_task("A",replace(prior,task=revised,announced_at=7,version=prior.version+1))
    engine.schedule(DynamicEvent("local-revision",7,EventKind.TASK_DISCOVERED,"task-2","local revision fixture"),revision)
    engine.run(24)
    known=next(a.task for a in engine.known_tasks("A") if a.task.task_id=="task-2")
    assert known.requirements.minimum_battery==0.8 and known.status is TaskStatus.ASSIGNED
    assert next(t.status for t in engine.world.tasks if t.task_id=="task-2") is TaskStatus.COMPLETED


def test_original_specialized_perception_changes_with_actual_motion(scenario):
    # Separate fixture: thermal A starts in corridor and wins fire by actual bids.
    # No assignment or sensor outcome is scripted.
    agents=tuple(replace(a,position=Position("corridor")) if a.identity.agent_id=="A" else a for a in scenario.initial_world.agents)
    fixture=replace(scenario,initial_world=replace(scenario.initial_world,agents=agents))
    engine=engine_for(fixture,ticks=32);engine.run(32)
    entered=next(e.tick for e in engine.execution_trace if e.agent_id=="A" and e.action=="moved" and e.position.location_id=="room-1")
    direct=[e.observation for e in engine.observation_history("A") if not e.is_remote]
    assert direct and all(o.subject=="fire" and o.location_id=="room-1" and o.observed_at>=entered for o in direct)
    assert min(o.observed_at for o in direct)==entered
    assert next(t.owner_id for t in engine.world.tasks if t.task_id=="task-1")=="A"
    assert engine.execution_snapshot("A").outcome.success


@pytest.mark.parametrize("multiple",[False,True])
def test_stale_completion_command_does_not_overwrite_successful_outcome(scenario,multiple):
    engine=engine_for(scenario)
    def stale(view):
        if view.time.tick!=10:return ()
        c=command(view,"complete","task-2",{"observation_ids":["stale"]})
        return (c,replace(c,command_id=c.command_id+"-duplicate")) if multiple else (c,)
    engine.register_controller("B",stale)
    engine.run(16)
    state=engine.execution_snapshot("B")
    assert state.phase is EP.COMPLETED and state.outcome.success and state.outcome.tick==9
    assert next(t.status for t in engine.world.tasks if t.task_id=="task-2") is TaskStatus.COMPLETED
    assert any(e.agent_id=="B" and e.action=="command rejected" and "terminal" in e.reason for e in engine.execution_trace)


def test_sequential_task_failure_does_not_reuse_previous_completion_proof(scenario):
    from coopai.models import Task
    from coopai.scenario import TaskDetails
    own=scenario.initial_world.agents[0]
    tasks=(Task("first","room-1",requirements=scenario.initial_world.tasks[0].requirements,urgency=0.9),
           Task("second","room-1",requirements=scenario.initial_world.tasks[0].requirements,urgency=0.1))
    fixture=replace(scenario,mission=replace(scenario.mission,duration_ticks=32),events=(),
                    initial_world=replace(scenario.initial_world,agents=(own,),tasks=tasks),
                    task_details=tuple(TaskDetails(t.task_id,"fire-verification",2) for t in tasks))
    engine=fixture.create_engine(with_allocation=True)
    specs=tuple(s for s in scenario_execution_specs(fixture.task_details) if s.task_id=="first")
    engine.enable_execution(fixture.floor_plan,specs)
    engine.run(32)
    assert {t.task_id:t.status for t in engine.world.tasks}=={"first":TaskStatus.COMPLETED,"second":TaskStatus.FAILED}
    assert engine.execution_snapshot("A").task_id=="second" and engine.execution_snapshot("A").phase is EP.FAILED
    assert engine.world.agents[0].availability is Availability.AVAILABLE
    assert len(engine.commit_trace)==2
