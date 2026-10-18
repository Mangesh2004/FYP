from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from coopai.eligibility import (EligibilityConfiguration, EnvironmentalRestriction, HardEligibility,
                               SelfEvaluationContext, travel_ticks)
from coopai.knowledge import KnowledgeEntry
from coopai.models import (AgentIdentity, AgentState, Availability, Capabilities, Connection,
                           DynamicEvent, Environment, EventKind, Location, Observation,
                           Position, ResourceLevel, Task, TaskRequirements, TaskStatus)
from coopai.priority import TaskAnnouncement
from coopai.scenario import load_scenario
from coopai.simulation import Phase
from coopai.suitability import AgentSuitabilityState, SuitabilityConfiguration, WeightedSuitability

PATH = Path(__file__).resolve().parents[1] / "scenarios/four_room_disaster.json"
TOPOLOGY = Environment((Location("a", "A"), Location("b", "B"), Location("c", "C")),
                       (Connection("a", "b", 2), Connection("b", "c", 3), Connection("a", "c", 9)))
CAPS = Capabilities(frozenset({"thermal"}), frozenset({"exploration"}))


def context(*, agent=None, task=None, evidence=(), tick=0, topology=TOPOLOGY):
    agent = agent or AgentState(AgentIdentity("A", "A"), CAPS, Position("a"), 0.8)
    task = task or Task("t", "b", TaskRequirements(CAPS, 0.1))
    return SelfEvaluationContext(agent, tick, (TaskAnnouncement(task, "fire-verification", 0),), evidence, topology)


def evaluate(ctx=None, eligibility=EligibilityConfiguration(), configuration=SuitabilityConfiguration()):
    return WeightedSuitability().evaluate("t", ctx or context(), eligibility, configuration)


def evidence(value="severe", confidence=1, observed=0, source="A", remote=False, identity="o", received=None):
    obs = Observation(identity, source, "b", "fire", value, observed, confidence, "thermal")
    return KnowledgeEntry(obs, observed if received is None else received,
                          source if remote else None, "m" if remote else None, (source, "A") if remote else ())


def factor(result, name):
    return next(f.value for f in result.factors if f.name == name)


@pytest.mark.parametrize("caps,failed", [
    (Capabilities(), {"sensing", "operational"}),
    (Capabilities(frozenset({"gas"}), frozenset({"exploration"})), {"sensing"}),
    (Capabilities(frozenset({"thermal"}), frozenset({"simulated-assistance"})), {"operational"}),
    (Capabilities(frozenset({"exploration"}), frozenset({"thermal"})), {"sensing", "operational"}),
    (Capabilities(frozenset({"Thermal"}), frozenset({"exploration"})), {"sensing"}),
    (CAPS, set()),
])
def test_category_specific_exact_capability_matching(caps, failed):
    ctx = context()
    result = evaluate(replace(ctx, own_state=replace(ctx.own_state, capabilities=caps)))
    assert {c.name for c in result.eligibility.failed_constraints} == failed
    assert result.eligibility.eligible == (not failed)
    assert (result.score is None) == bool(failed)


def test_all_required_tags_and_no_invented_operations():
    ctx = context()
    task = replace(ctx.known_tasks[0].task, requirements=TaskRequirements(Capabilities(frozenset({"thermal", "visual"}), frozenset({"firefighting"}))))
    result = evaluate(context(task=task))
    assert {c.name for c in result.eligibility.failed_constraints} == {"sensing", "operational"}
    empty = replace(task, requirements=TaskRequirements())
    assert evaluate(context(task=empty)).eligibility.eligible


@pytest.mark.parametrize("availability", [Availability.OFFLINE, Availability.FAILED])
def test_unavailable_operational_status_blocks_suitable_sensors(availability):
    ctx = context()
    result = evaluate(replace(ctx, own_state=replace(ctx.own_state, availability=availability)))
    assert result.score is None and "availability" in {c.name for c in result.eligibility.failed_constraints}
    assert result.factors == ()


@pytest.mark.parametrize("status,owner,eligible", [
    (TaskStatus.PENDING, None, True), (TaskStatus.ASSIGNED, "A", False),
    (TaskStatus.IN_PROGRESS, "A", False), (TaskStatus.ASSIGNED, "B", False),
    (TaskStatus.COMPLETED, "A", False), (TaskStatus.FAILED, None, False),
    (TaskStatus.CANCELLED, None, False),
])
def test_task_lifecycle_and_ownership(status, owner, eligible):
    ctx = context()
    task = replace(ctx.known_tasks[0].task, status=status, owner_id=owner)
    result = evaluate(context(task=task))
    assert result.eligibility.eligible == eligible
    assert (result.score is not None) == eligible


@pytest.mark.parametrize("status", [TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS])
def test_commitments_allow_only_consistent_same_task_continuation(status):
    ctx = context()
    busy = replace(ctx.own_state, availability=Availability.BUSY, task_id="t")
    own = replace(ctx.known_tasks[0].task, status=status, owner_id="A")
    assert evaluate(context(agent=busy, task=own)).eligibility.eligible
    assert not evaluate(context(agent=busy)).eligibility.eligible
    assert not evaluate(context(agent=replace(busy, task_id="other"), task=own)).eligibility.eligible
    assert not evaluate(context(agent=busy, task=replace(own, owner_id="B"))).eligibility.eligible


@pytest.mark.parametrize("battery,eligible", [(0, False), (0.119, False), (0.12, True), (0.8, True)])
def test_battery_reserve_plus_graph_travel_threshold(battery, eligible):
    ctx = context()
    result = evaluate(context(agent=replace(ctx.own_state, battery=battery)))
    assert result.eligibility.required_battery == pytest.approx(0.12)
    assert result.eligibility.eligible == eligible


def test_zero_battery_never_operational_and_task_minimum_overrides_default():
    ctx = context()
    zero = context(agent=replace(ctx.own_state, battery=0), task=replace(ctx.known_tasks[0].task, location_id="a", requirements=TaskRequirements()))
    assert not evaluate(zero, EligibilityConfiguration(0, 0)).eligibility.eligible
    task = replace(ctx.known_tasks[0].task, requirements=TaskRequirements(CAPS, 0.79))
    assert not evaluate(context(task=task)).eligibility.eligible
    assert evaluate(context(task=task), EligibilityConfiguration(0.1, 0)).eligibility.eligible
    assert not evaluate(context(), EligibilityConfiguration(1, 0.1)).eligibility.eligible


@pytest.mark.parametrize("amount,eligible", [(None, False), (0, False), (1.9, False), (2, True), (4, True)])
def test_resource_demand_not_capacity_requirement(amount, eligible):
    ctx = context()
    task = replace(ctx.known_tasks[0].task, requirements=TaskRequirements(CAPS, 0.1, (ResourceLevel("kit", 2, 100),)))
    resources = () if amount is None else (ResourceLevel("kit", amount, 4),)
    result = evaluate(context(agent=replace(ctx.own_state, resources=resources), task=task))
    assert result.eligibility.eligible == eligible
    if eligible:
        assert factor(result, "resource") == (amount - 2) / 4
        assert result.score == pytest.approx(sum(f.contribution for f in result.factors))


def test_zero_demand_missing_resource_is_valid_and_factor_inapplicable():
    ctx = context()
    task = replace(ctx.known_tasks[0].task, requirements=TaskRequirements(resources=(ResourceLevel("kit", 0, 0),)))
    result = evaluate(context(task=task))
    assert result.eligibility.eligible and "resource" not in {f.name for f in result.factors}


def test_travel_is_weighted_directed_shortest_path_not_euclidean():
    assert travel_ticks(TOPOLOGY, "a", "c") == 5
    assert travel_ticks(TOPOLOGY, "c", "a") is None
    assert travel_ticks(TOPOLOGY, "a", "a") == 0
    assert travel_ticks(replace(TOPOLOGY, connections=TOPOLOGY.connections[::-1]), "a", "c") == 5
    with pytest.raises(ValueError, match="endpoint"):
        travel_ticks(TOPOLOGY, "hidden", "a")
    isolated = replace(TOPOLOGY, connections=())
    assert travel_ticks(isolated, "a", "b") is None
    result = evaluate(context(topology=isolated))
    assert result.score is None and "reachability" in {c.name for c in result.eligibility.failed_constraints}


def test_four_room_distances_use_corridor_and_doors():
    topology = load_scenario(PATH).initial_world.environment
    assert travel_ticks(topology, "room-1", "room-4") == 2
    assert travel_ticks(topology, "corridor", "room-4") == 1
    assert travel_ticks(topology, "room-2", "room-2") == 0


@pytest.mark.parametrize("battery", [0.12, 0.4, 0.8, 1])
@pytest.mark.parametrize("confidence", [0, 0.4, 1])
def test_normalized_formula_and_reproducibility(battery, confidence):
    ctx = context(agent=replace(context().own_state, battery=battery), evidence=(evidence(confidence=confidence),))
    result = evaluate(ctx)
    assert 0 <= result.score <= 1
    assert result == evaluate(ctx)
    assert sum(f.normalized_weight for f in result.factors) == pytest.approx(1)
    assert result.score == pytest.approx(0.45 * (battery-0.12)/0.88 + 0.35*0.5 + 0.2*confidence)
    assert result.eligibility.passed_constraints and not result.eligibility.failed_constraints


def test_weight_configuration_and_monotonic_reserve_distance():
    cfg = SuitabilityConfiguration(battery_weight=2, travel_weight=0, information_weight=0)
    assert evaluate(configuration=cfg).score == pytest.approx((0.8-0.12)/0.88)
    ctx = context()
    nearer = context(task=replace(ctx.known_tasks[0].task, location_id="a"))
    assert evaluate(nearer).score > evaluate(ctx).score
    assert evaluate(context(agent=replace(ctx.own_state, battery=0.9))).score > evaluate(ctx).score
    higher_priority_task = replace(ctx.known_tasks[0].task, urgency=1, severity=1, deadline=0)
    assert evaluate(context(task=higher_priority_task)).score == evaluate(ctx).score


def test_information_age_provenance_and_no_repetition_boost():
    remote = evidence(source="B", remote=True, received=1)
    result = evaluate(context(evidence=(remote,), tick=1))
    assert factor(result,"information") == 0.75
    assert result.evidence == (remote,) and result.evidence_ids == ("o",)
    assert evaluate(context(evidence=(remote, remote), tick=1)).score == result.score
    stale = evaluate(context(evidence=(remote,), tick=4))
    assert factor(stale,"information") == 0 and stale.evidence == ()
    assert factor(evaluate(),"information") == 0
    unrelated = replace(remote, observation=replace(remote.observation, subject="gas"))
    assert factor(evaluate(context(evidence=(unrelated,), tick=1)),"information") == 0


def test_optional_hard_environment_restrictions_use_only_fresh_own_evidence():
    restriction = EnvironmentalRestriction("fire", frozenset({"severe"}))
    cfg = EligibilityConfiguration(restrictions=(restriction,))
    assert evaluate(eligibility=cfg).eligibility.eligible  # no default invented ban
    assert not evaluate(context(evidence=(evidence(),)), cfg).eligibility.eligible
    assert evaluate(context(evidence=(evidence(confidence=0.2),)), cfg).eligibility.eligible
    assert evaluate(context(evidence=(evidence(),),tick=4), cfg).eligibility.eligible
    strict = replace(cfg, restrictions=(replace(restriction, require_known=True),))
    assert not evaluate(eligibility=strict).eligibility.eligible
    assert evaluate(context(evidence=(evidence("clear"),)), strict).eligibility.eligible
    old = evidence(identity="old")
    fresh = evidence("clear", observed=1, identity="new")
    assert evaluate(context(evidence=(old,fresh),tick=1), strict).eligibility.eligible
    conflicting = evidence(source="B",remote=True,identity="peer",received=1)
    assert not evaluate(context(evidence=(fresh,conflicting),tick=1), strict).eligibility.eligible


@pytest.mark.parametrize("changes", [{"minimum_battery":-1}, {"minimum_battery":True}, {"battery_per_travel_tick":float("nan")}, {"restrictions":[]}])
def test_invalid_eligibility_configuration(changes):
    with pytest.raises(ValueError):
        EligibilityConfiguration(**changes)


@pytest.mark.parametrize("changes", [{"battery_weight":-1}, {"travel_weight":True}, {"information_weight":float("inf")}, {"freshness_ticks":0}, {"travel_scale_ticks":False}, {"battery_weight":0,"travel_weight":0,"information_weight":0}, {"task_subjects":[]}])
def test_invalid_suitability_configuration(changes):
    with pytest.raises(ValueError):
        SuitabilityConfiguration(**changes)


def test_scope_and_result_consistency_validation():
    ctx = context()
    with pytest.raises(ValueError, match="announced"):
        WeightedSuitability().evaluate("hidden",ctx)
    with pytest.raises(ValueError, match="another agent"):
        context(evidence=(evidence(source="B"),))
    with pytest.raises(ValueError, match="future"):
        context(evidence=(evidence(observed=1),))
    with pytest.raises(ValueError, match="future"):
        context(agent=replace(ctx.own_state, updated_at=1))
    with pytest.raises(ValueError, match="location"):
        context(agent=replace(ctx.own_state, position=Position("hidden")))
    result = evaluate(ctx)
    with pytest.raises(FrozenInstanceError):
        result.score = 2
    with pytest.raises(ValueError, match="disagrees"):
        replace(result.eligibility, eligible=False)
    with pytest.raises(ValueError, match="inconsistent"):
        replace(result, score=0)
    with pytest.raises(ValueError, match="identity"):
        replace(result, agent_id="B")
    ineligible = evaluate(context(agent=replace(ctx.own_state, battery=0)))
    with pytest.raises(ValueError, match="ineligible"):
        replace(ineligible, score=0.5)


def test_replaceable_local_evaluators_enforce_owner_contract():
    class CountingGate:
        def __init__(self): self.calls = 0
        def check(self, task_id, ctx, cfg):
            self.calls += 1
            return HardEligibility().check(task_id,ctx,cfg)
    gate = CountingGate()
    evaluator = WeightedSuitability(gate)
    state = AgentSuitabilityState("A", TOPOLOGY, evaluator=evaluator)
    state.update(context())
    assert gate.calls == 1 and state.results[0] == evaluate()
    state.start()
    state.configuration = replace(state.configuration, battery_weight=1)
    state.reset()
    assert state.configuration == SuitabilityConfiguration() and state.history == ()
    class Spoofed:
        def evaluate(self,task_id,ctx,ecfg,cfg):
            good = evaluate(ctx)
            return replace(good,agent_id="B",eligibility=replace(good.eligibility, agent_id="B"))
    invalid = AgentSuitabilityState("A",TOPOLOGY,evaluator=Spoofed())
    with pytest.raises(ValueError,match="identity"):
        invalid.update(context())
    assert invalid.results == ()


def test_runtime_boundary_hidden_tasks_peer_states_and_world_hazards():
    engine = load_scenario(PATH).create_engine(with_suitability=True)
    hidden = Task("hidden","room-4")
    engine.replace_world(replace(engine.world,tasks=(*engine.world.tasks,hidden)))
    views = []
    engine.register_controller("B",lambda view: views.append(view) or ())
    engine.run(4)
    assert all(r.agent_id == "B" for view in views for r in view.suitability)
    assert all("hidden" not in {r.task_id for r in view.suitability} for view in views)
    assert not hasattr(views[0],"world") and not hasattr(views[0],"peer_states")
    c_fire = next(r for r in engine.suitability_snapshot("C") if r.task_id == "task-1")
    assert factor(c_fire,"information") == 0 and c_fire.evidence == ()
    assert not hasattr(SelfEvaluationContext,"world")
    assert not hasattr(WeightedSuitability(),"engine")


def test_dynamic_battery_communication_and_reset_full_histories():
    scenario = load_scenario(PATH)
    engine = scenario.create_engine(with_suitability=True,with_communication=True)
    engine.run(5)
    before = next(r for r in engine.suitability_snapshot("B") if r.task_id == "task-2")
    remote = next(r for r in engine.suitability_snapshot("C") if r.task_id == "task-1")
    assert remote.evidence and remote.evidence[0].is_remote
    engine.run(1)
    after = next(r for r in engine.suitability_snapshot("B") if r.task_id == "task-2")
    assert after.eligibility.eligible and after.evaluation_tick == 5
    assert before.score == pytest.approx(0.93) and after.score == pytest.approx(0.68)
    histories = tuple(engine.suitability_history(a) for a in "ABCD")
    engine.reset()
    assert all(engine.suitability_snapshot(a) == () for a in "ABCD")
    engine.run(6)
    assert tuple(engine.suitability_history(a) for a in "ABCD") == histories
    assert all(t.status is TaskStatus.PENDING and t.owner_id is None for t in engine.world.tasks)


def test_dynamic_local_state_requirements_announcements_and_resources():
    engine = load_scenario(PATH).create_engine(with_suitability=True)
    def update(sim,event):
        own = next(a for a in sim.world.agents if a.identity.agent_id == "A")
        sim.update_agent(replace(own,position=Position("room-4"),updated_at=1))
        task = Task("new","room-4",TaskRequirements(resources=(ResourceLevel("support-kits",1,2),)),created_at=1)
        sim.replace_world(replace(sim.world,tasks=(*sim.world.tasks,task)))
        sim.announce_task("A",TaskAnnouncement(task,"routine-inspection",1))
        prior = next(a for a in sim.known_tasks("A") if a.task.task_id == "task-4")
        sim.announce_task("A",replace(prior,task=replace(prior.task,requirements=TaskRequirements(Capabilities(operational=frozenset({"firefighting"})))),version=2,announced_at=1))
    engine.schedule(DynamicEvent("own-update",1,EventKind.TASK_DISCOVERED,"new","Explicit changes"),update)
    engine.run(2)
    new = next(r for r in engine.suitability_snapshot("A") if r.task_id == "new")
    assert new.eligibility.travel_ticks == 0 and new.score is None
    assert "resource:support-kits" in {c.name for c in new.eligibility.failed_constraints}
    assert "new" not in {r.task_id for r in engine.suitability_snapshot("B")}
    assert next(r for r in engine.suitability_snapshot("A") if r.task_id == "task-4").score is None
    engine.reset()
    assert "new" not in {a.task.task_id for a in engine.known_tasks("A")}


def test_runtime_failure_reevaluates_without_reassignment():
    engine = load_scenario(PATH).create_engine(with_suitability=True)
    def fail(sim):
        if sim.time.tick == 1:
            own = next(a for a in sim.world.agents if a.identity.agent_id == "A")
            sim.update_agent(replace(own,availability=Availability.FAILED,updated_at=1))
    engine.register_system("failure",Phase.EXECUTION,fail)
    engine.run(2)
    assert all(r.score is None for r in engine.suitability_snapshot("A"))
    assert all(t.owner_id is None for t in engine.world.tasks)


def test_demo_actual_results(capsys):
    from coopai.suitability_demo import main
    main()
    output = capsys.readouterr().out
    assert "agent=A task=task-2 eligible=False" in output and "Missing sensing: gas" in output
    assert "0.930 -> 0.680" in output
    assert "self_evaluation_replay_equal=True assignments=NONE" in output


def test_resource_consumption_then_unavailability_reevaluates_locally():
    engine = load_scenario(PATH).create_engine(with_suitability=True)
    own = next(a for a in engine.world.agents if a.identity.agent_id == "D")
    task = Task("kit-task","corridor",TaskRequirements(resources=(ResourceLevel("support-kits",1,2),)))
    engine.replace_world(replace(engine.world,tasks=(*engine.world.tasks,task)))
    engine.announce_task("D",TaskAnnouncement(task,"routine-inspection",0))
    def consume(sim):
        if sim.time.tick == 1:
            sim.update_agent(replace(own,resources=(ResourceLevel("support-kits",0,2),),updated_at=1))
        elif sim.time.tick == 2:
            sim.update_agent(replace(own,availability=Availability.OFFLINE,updated_at=2))
    engine.register_system("consumption",Phase.EXECUTION,consume)
    engine.run(1)
    assert next(r for r in engine.suitability_snapshot("D") if r.task_id == "kit-task").score is not None
    engine.run(1)
    result = next(r for r in engine.suitability_snapshot("D") if r.task_id == "kit-task")
    assert result.score is None and "resource:support-kits" in {c.name for c in result.eligibility.failed_constraints}
    engine.run(1)
    assert all(r.score is None for r in engine.suitability_snapshot("D"))


def test_full_battery_exact_one_minimum_no_division_by_zero():
    ctx = context()
    task = replace(ctx.known_tasks[0].task,location_id="a",requirements=TaskRequirements(minimum_battery=1))
    result = evaluate(context(agent=replace(ctx.own_state,battery=1),task=task))
    assert result.eligibility.eligible and factor(result,"battery") == 0
    assert result.score == pytest.approx(0.35)


def test_conflicting_evidence_identity_rejected_and_foreign_recipient_rejected():
    with pytest.raises(ValueError,match="conflicting"):
        context(evidence=(evidence(),evidence("clear")))
    remote = evidence(source="B",remote=True)
    with pytest.raises(ValueError,match="another recipient"):
        context(evidence=(replace(remote,path=("B","C")),))
