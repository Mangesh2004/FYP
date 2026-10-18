from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path

import pytest

from coopai.communication import CommunicationConfiguration
from coopai.knowledge import KnowledgeEntry
from coopai.models import DynamicEvent, EventKind, Observation, Task, TaskStatus
from coopai.priority import (AgentPriorityState, PriorityConfiguration, PriorityContext,
                             PriorityWeights, TaskAnnouncement, TypePriorityRule, WeightedPriority)
from coopai.scenario import ScenarioError, load_scenario, scenario_from_dict


PATH = Path(__file__).resolve().parents[1] / "scenarios/four_room_disaster.json"


def announcement(**changes):
    return TaskAnnouncement(replace(Task("t", "loc", urgency=0.8, deadline=8), **changes), "fire-verification", 0)


def entry(value="present", confidence=1, observed=0, modality="thermal", identity="o", source="A", remote=False, received=None):
    obs = Observation(identity, source, "loc", "fire", value, observed, confidence, modality)
    return KnowledgeEntry(obs, observed if received is None else received,
                          source if remote else None, "message-" + identity if remote else None,
                          (source, "A") if remote else ())


def evaluate(*, evidence=(), tick=0, cfg=PriorityConfiguration(), task=None):
    task = task or announcement()
    return WeightedPriority().evaluate(task.task.task_id, PriorityContext("A", tick, (task,), evidence, cfg))


def factor(result, name):
    return next(f.value for f in result.factors if f.name == name)


@pytest.mark.parametrize("tick", [0, 3, 8, 20])
@pytest.mark.parametrize("value,confidence", [("present",1), ("severe",0.5), ("clear",1), ("other",1), ("severe",0)])
def test_bounds_explainability_and_determinism(tick, value, confidence):
    result = evaluate(evidence=(entry(value, confidence),), tick=tick)
    assert 0 <= result.score <= 1
    assert result == evaluate(evidence=(entry(value, confidence),), tick=tick)
    assert result.score == pytest.approx(sum(f.contribution for f in result.factors))
    assert sum(f.normalized_weight for f in result.factors) == pytest.approx(1)
    assert result.evaluation_tick == tick and result.agent_id == "A" and result.task_id == "t"
    assert result.explanation and result.evidence[0].entry.observation.observation_id == "o"


def test_formula_is_not_predefined_urgency():
    result = evaluate()
    assert result.score == pytest.approx(0.25*0.8 + 0.2*0 + 0.35*0.5 + 0.15*0.9 + 0.05*1)
    assert result.score != 0.8
    assert evaluate(task=announcement(urgency=0.9)).score - result.score == pytest.approx(0.025)


def test_deadline_pressure_monotone_and_clamped():
    results = [evaluate(tick=tick) for tick in (0, 4, 8, 20)]
    assert [factor(r, "deadline") for r in results] == [0, 0.5, 1, 1]
    assert results[0].score < results[1].score < results[2].score == results[3].score
    assert factor(evaluate(tick=20, task=announcement(deadline=None)), "deadline") == 0


def test_unknown_safe_severe_and_confidence():
    unknown = evaluate()
    safe = evaluate(evidence=(entry("clear"),))
    severe = evaluate(evidence=(entry("severe"),))
    assert safe.score < unknown.score < severe.score
    assert factor(safe, "environment") == factor(safe, "uncertainty") == 0
    assert unknown.evidence_status == "unknown" and safe.evidence_status == "safe_evidence"
    assert evaluate(evidence=(entry("severe", confidence=0),)).score == unknown.score
    assert factor(evaluate(evidence=(entry("severe",confidence=0.5),)), "environment") == 0.75
    assert factor(evaluate(evidence=(entry("severe",confidence=0.5),)), "uncertainty") == 0.5


def test_stale_information_returns_to_unknown_not_safe():
    strong = evaluate(evidence=(entry("severe"),))
    older = evaluate(evidence=(entry("severe"),), tick=2)
    stale = evaluate(evidence=(entry("severe"),), tick=4)
    assert factor(strong,"environment") > factor(older,"environment") > factor(stale,"environment")
    assert factor(stale,"environment") == 0.5 and stale.evidence_status == "unknown"
    assert stale.evidence[0].disposition == "stale"


def test_weak_new_safe_does_not_erase_strong_older_hazard():
    evidence = (entry("severe", confidence=0.9, identity="hazard"),
                entry("clear", confidence=0.2, observed=1, identity="safe"))
    result = evaluate(evidence=evidence, tick=1)
    assert result.evidence_status == "conflict" and factor(result,"environment") > 0.5
    assert next(e for e in result.evidence if e.disposition == "selected").entry.observation.observation_id == "hazard"
    assert evaluate(evidence=evidence[::-1], tick=1) == result


def test_stronger_fresher_safe_dominates_uncertain_old_hazard():
    result = evaluate(evidence=(entry("severe",confidence=0.2,identity="hazard"),
                                entry("clear",confidence=0.9,observed=1,identity="safe")), tick=1)
    assert result.evidence_status == "safe_evidence" and factor(result,"environment") < 0.5
    assert next(e for e in result.evidence if e.entry.observation.observation_id == "hazard").disposition == "dominated"


def test_equally_reliable_conflict_conservatively_selects_risk_not_certainty():
    result = evaluate(evidence=(entry("clear",confidence=0.8,modality="visual",identity="safe"),
                                entry("severe",confidence=0.8,identity="hazard")))
    assert result.evidence_status == "conflict"
    assert factor(result,"environment") == 0.9 and factor(result,"uncertainty") == pytest.approx(0.2)
    assert len(result.evidence) == 2


def test_duplicate_evidence_never_inflates_confidence():
    obs = entry("severe",confidence=0.7)
    assert evaluate(evidence=(obs,obs)) == evaluate(evidence=(obs,))
    assert factor(evaluate(evidence=(obs,)), "uncertainty") == pytest.approx(0.3)
    with pytest.raises(ValueError, match="conflicting content"):
        evaluate(evidence=(obs, entry("clear",confidence=0.7)))


def test_unknown_value_and_unrelated_room_do_not_become_safe():
    result = evaluate(evidence=(entry("unmapped"),))
    assert result.evidence_status == "unknown" and result.evidence[0].disposition == "unmapped"
    unrelated = replace(entry("severe"), observation=replace(entry("severe").observation, location_id="other"))
    assert evaluate(evidence=(unrelated,)).score == evaluate().score


def test_remote_provenance_and_age_are_preserved():
    remote = entry("severe", observed=0, received=2, source="B", remote=True)
    result = evaluate(evidence=(remote,), tick=2)
    assert result.evidence[0].entry.forwarded_by == "B" and result.evidence[0].age_ticks == 2
    assert factor(result,"environment") == 0.75


def test_task_visibility_and_recipient_scope():
    with pytest.raises(ValueError, match="announced"):
        WeightedPriority().evaluate("hidden", PriorityContext("A",0,(announcement(),),()))
    with pytest.raises(ValueError, match="another agent"):
        evaluate(evidence=(entry(source="B"),))
    remote = entry(source="B", remote=True)
    with pytest.raises(ValueError, match="another recipient"):
        WeightedPriority().evaluate("t", PriorityContext("C",0,(announcement(),),(remote,)))
    with pytest.raises(ValueError, match="future"):
        evaluate(evidence=(entry(observed=1),))


@pytest.mark.parametrize("changes", [{"urgency":-1}, {"deadline":float("nan")}, {"importance":True}, {"environment":float("inf")}, {"urgency":0,"deadline":0,"environment":0,"importance":0,"uncertainty":0}])
def test_invalid_weights(changes):
    with pytest.raises(ValueError):
        PriorityWeights(**changes)


@pytest.mark.parametrize("changes", [{"deadline_window_ticks":0}, {"freshness_ticks":True}, {"unknown_risk":-0.1}, {"default_importance":2}, {"rules":[]}, {"rules":(TypePriorityRule("x","fire",1,()),)*2}])
def test_invalid_configuration(changes):
    with pytest.raises(ValueError):
        PriorityConfiguration(**changes)


def test_weights_normalize_and_mission_rules_are_location_independent():
    cfg = replace(PriorityConfiguration(), weights=PriorityWeights(urgency=5,deadline=0,environment=0,importance=0,uncertainty=0))
    assert evaluate(cfg=cfg).score == 0.8
    rule = TypePriorityRule("fire-verification","fire",0.1,(("severe",1),))
    lowered = evaluate(cfg=replace(PriorityConfiguration(),rules=(rule,)))
    assert lowered.score < evaluate().score
    renamed = TaskAnnouncement(replace(announcement().task, location_id="renamed"),"fire-verification",0)
    assert evaluate(task=renamed).score == evaluate().score


def test_terminal_announced_task_scores_zero_without_allocating():
    task = announcement(status=TaskStatus.CANCELLED)
    assert evaluate(task=task).score == 0
    assert "Terminal" in evaluate(task=task).explanation


def test_announcement_version_ordering_and_stable_cache():
    state = AgentPriorityState("A",PriorityConfiguration())
    original = announcement(deadline=None)
    state.announce(original,0)
    state.start()
    state.update(0,())
    results = state.results
    state.update(1,())
    assert state.results is results and len(state.history) == 1
    update = replace(original, task=replace(original.task, urgency=0.9), version=2, announced_at=1)
    state.announce(update,1)
    state.update(1,())
    assert state.results[0].score > results[0].score
    assert not state.announce(original,1)
    with pytest.raises(ValueError, match="version reused"):
        state.announce(replace(update, task=replace(update.task,urgency=0.1)),1)
    state.reset()
    assert state.known_tasks() == (original,) and state.results == () and state.history == ()


def test_hidden_task_and_world_changes_do_not_publish_implicitly():
    scenario = load_scenario(PATH)
    engine = scenario.create_engine(with_priority=True)
    hidden = Task("hidden","room-4")
    engine.replace_world(replace(engine.world,tasks=(*engine.world.tasks,hidden)))
    views = []
    engine.register_controller("B",lambda view: views.append(view) or ())
    engine.run(4)
    assert all("hidden" not in {a.task.task_id for a in v.known_tasks} for v in views)
    assert "hidden" not in {r.task_id for r in engine.priority_snapshot("B")}
    fire = next(r for r in engine.priority_snapshot("B") if r.task_id == "task-1")
    assert fire.evidence_status == "unknown" and factor(fire,"environment") == 0.5
    assert next(c.value for c in engine.world.conditions if c.subject == "fire") == "severe"


def test_dynamic_announcement_update_mission_change_and_reset():
    scenario = load_scenario(PATH)
    engine = scenario.create_engine(with_priority=True)
    def update(sim,event):
        hidden = Task("new","room-4",urgency=0.6,created_at=1)
        sim.replace_world(replace(sim.world,tasks=(*sim.world.tasks,hidden)))
        sim.announce_task("A",TaskAnnouncement(hidden,"routine-inspection",1))
        sim.set_priority_configuration("A", replace(scenario.priority_configuration, weights=PriorityWeights(urgency=1,deadline=0,environment=0,importance=0,uncertainty=0)))
    engine.schedule(DynamicEvent("announce",1,EventKind.TASK_DISCOVERED,"new","Explicit mission announcement"),update)
    engine.run(2)
    assert "new" in {r.task_id for r in engine.priority_snapshot("A")}
    assert "new" not in {r.task_id for r in engine.priority_snapshot("B")}
    assert next(r.score for r in engine.priority_snapshot("A") if r.task_id == "new") == 0.6
    engine.reset()
    assert "new" not in {a.task.task_id for a in engine.known_tasks("A")}
    assert engine.priority_snapshot("A") == ()


def test_actual_communication_drives_priority_and_replays():
    scenario = load_scenario(PATH)
    engine = scenario.create_engine(with_priority=True)
    engine.enable_communication(CommunicationConfiguration(latency_ticks=2,ttl_ticks=8))
    engine.start()
    initial = next(r for r in engine.priority_snapshot("A") if r.task_id == "task-1")
    engine.run(1)
    a = next(r for r in engine.priority_snapshot("A") if r.task_id == "task-1")
    b = next(r for r in engine.priority_snapshot("B") if r.task_id == "task-1")
    assert a.score > initial.score == b.score and b.evidence_status == "unknown"
    engine.run(2)
    b = next(r for r in engine.priority_snapshot("B") if r.task_id == "task-1")
    assert b.evidence and all(e.entry.is_remote for e in b.evidence)
    engine.run(1)
    a = next(r for r in engine.priority_snapshot("A") if r.task_id == "task-1")
    b = next(r for r in engine.priority_snapshot("B") if r.task_id == "task-1")
    assert a.score > b.score
    assert next(e.entry.observation.value for e in a.evidence if e.disposition == "selected") == "severe"
    assert not any(e.entry.observation.value == "severe" for e in b.evidence)
    engine.run(4)
    histories = tuple(engine.priority_history(agent) for agent in "ABCD")
    engine.reset()
    engine.run(8)
    assert tuple(engine.priority_history(agent) for agent in "ABCD") == histories
    assert all(t.owner_id is None for t in engine.world.tasks)


def test_json_priority_validation_and_old_scenarios_remain_loadable():
    config = json.loads(PATH.read_text())
    assert scenario_from_dict(config).priority_configuration.weights.environment == 0.35
    invalid = json.loads(PATH.read_text())
    invalid["priority"]["weights"]["environment"] = -1
    with pytest.raises(ScenarioError,match="priority"):
        scenario_from_dict(invalid)
    config.pop("priority")
    assert isinstance(scenario_from_dict(config).priority_configuration,PriorityConfiguration)


def test_demo_reports_actual_scores_and_replay(capsys):
    from coopai.priority_demo import main
    main()
    output = capsys.readouterr().out
    assert "mission-start A:" in output and "mission-start B:" in output
    assert "agent=B" in output and "status=unknown" in output
    assert "priority_replay_equal=True assignments=NONE" in output


def test_task_updates_preserve_initial_urgency_and_require_explicit_recipient():
    state = AgentPriorityState("A",PriorityConfiguration())
    state.announce(announcement(),0)
    newer = TaskAnnouncement(replace(announcement().task,urgency=0.95),"fire-verification",1,2)
    state.announce(newer,1)
    assert state.known_tasks()[0].initial_urgency == 0.8
    assert state.known_tasks()[0].task.urgency == 0.95
    engine = load_scenario(PATH).create_engine(with_priority=True)
    with pytest.raises(ValueError,match="recipient"):
        engine.announce_task("missing",announcement())
    with pytest.raises(ValueError,match="future"):
        engine.announce_task("A",replace(announcement(),announced_at=1))
    engine.run(1)
    with pytest.raises(RuntimeError):
        engine.announce_task("A",announcement())


def test_evaluator_has_no_world_or_transport_dependency():
    evaluator = WeightedPriority()
    assert not hasattr(evaluator,"world") and not hasattr(evaluator,"engine")
    result = evaluate()
    with pytest.raises(FrozenInstanceError):
        result.score = 2
    with pytest.raises(ValueError):
        replace(result,score=float("nan"))


def test_controller_receives_only_its_own_computed_results():
    engine = load_scenario(PATH).create_engine(with_priority=True)
    views = []
    engine.register_controller("A",lambda view: views.append(view) or ())
    engine.run(1)
    assert len(views[0].priorities) == 4
    assert all(r.agent_id == "A" for r in views[0].priorities)
    assert views[0].priorities == engine.priority_snapshot("A")
    assert not hasattr(views[0],"world") and not hasattr(views[0],"global_priorities")


def test_replaceable_evaluator_preserves_owner_contract_and_cache():
    class CountingEvaluator:
        def __init__(self): self.calls = 0
        def evaluate(self,task_id,context):
            self.calls += 1
            return WeightedPriority().evaluate(task_id,context)
    evaluator = CountingEvaluator()
    state = AgentPriorityState("A",PriorityConfiguration(),evaluator)
    state.announce(announcement(deadline=None),0)
    state.update(0,())
    state.update(1,())
    assert evaluator.calls == 1
    class InvalidEvaluator:
        def evaluate(self,task_id,context):
            return replace(WeightedPriority().evaluate(task_id,context),agent_id="another-agent")
    invalid = AgentPriorityState("A",PriorityConfiguration(),InvalidEvaluator())
    invalid.announce(announcement(),0)
    with pytest.raises(ValueError,match="identity"):
        invalid.update(0,())
    assert invalid.results == ()
