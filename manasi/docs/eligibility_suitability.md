# Hard eligibility and agent-local suitability — Task 8

`eligibility.py` and `suitability.py` implement independent pure decision policies.
Neither imports the simulator, authoritative world, transport or renderer. They
consume a validated `SelfEvaluationContext`: **own** immutable AgentState, current
integer tick, explicitly announced tasks, evidence received by that agent, and
explicitly public static Environment topology. Context validation rejects future
inputs, wrong evidence recipients/sources, unknown locations and conflicting
content for the same evidence ID. It never obtains hidden tasks or conditions.

## Hard rules

`HardEligibility.check(task_id, context, configuration)` returns identity/tick,
boolean eligibility, named constraints with explanations, passed/failed constraint
properties, directed travel cost and required battery. Every constraint must pass:

1. Every required sensing tag is present in the sensing category.
2. Every required operational tag is present in the operational category.
   Tags are exact, case-sensitive all-of requirements; sensors never imply operations.
3. Availability is AVAILABLE for a new task; OFFLINE and FAILED always fail.
4. No competing commitment. BUSY is permitted only for continuation of that exact
   ASSIGNED/IN_PROGRESS task, owned by the agent and matching its task commitment.
5. Task is PENDING, or that consistent same-task continuation. Terminal tasks and
   foreign ownership are rejected. An own active task without a matching commitment
   is also rejected. Evaluation never changes ownership or lifecycle.
6. The target is reachable through public directed topology.
7. Positive battery and battery >= `max(task.minimum_battery, configured_minimum)
   + travel_ticks * configured_battery_per_travel_tick`.
8. Each required resource amount is available; absent resource means zero. Required
   capacity is a validation bound, **not** an agent-capacity threshold. A zero demand
   needs no stock. All demands must pass.
9. Optional environmental rules pass. No rules are enabled by default. Configured
   `EnvironmentalRestriction` identifies a subject, prohibited categorical values,
   confidence threshold (default 0.5), maximum age (default 3 ticks), and explicit
   `require_known` (default false). Latest sample per original source/modality at
   the task location is considered; any usable prohibited report blocks. Conflicting
   independent sources retain the prohibition. Expired or low-confidence reports
   are unusable; unknown blocks only with `require_known=True`. This is a local
   precaution, not a claim about physical truth or guaranteed execution safety.

Default `EligibilityConfiguration`: minimum battery **0.1**, travel battery cost
**0.01 per graph tick**, no restrictions. These are public configurable abstract
simulation assumptions, not measured robot physics. Decimal arithmetic over the
configured numeric strings calculates the threshold before conversion to float,
so an exact decimal boundary such as 0.1 + 2×0.01 = 0.12 passes at battery 0.12.
No epsilon silently relaxes a hard rule. Required cost above 1 makes a task infeasible.
Unreachable paths have no cost estimate (`None`) and fail independently.

## Directed travel model

`travel_ticks(topology, source, target)` uses standard-library heap-based Dijkstra
with explicit `Connection.travel_ticks`. Edge ordering and equal-cost processing
are stable by location ID. Source=target costs zero; no path returns None; unknown
endpoints raise ValueError. Reverse edges must exist explicitly. The configured
four-room geometry's passages already produce public connections: room↔corridor
costs one, room↔other-room costs two. There is no direct traversal through walls.

Only graph effort is estimated: no within-room distance, physical movement,
collision, occupancy, hazard-dependent routing, returning to base, task duration
energy or door closure is modeled. Position remains location-based. Future execution
must validate physical requests separately; estimated feasibility is not success.

## Suitability formula and configuration

`WeightedSuitability.evaluate(...)` first checks hard eligibility. Ineligible
results have **score=None**, no factors and explicit failed constraints. It does
not accept an externally supplied eligibility flag, which could be stale. The gate
is replaceable through the `EligibilityEvaluator` protocol/injected check policy.
Suitability itself is replaceable through `SuitabilityEvaluator` on per-agent state.
Results validate owner/task/tick contracts and normalized weighted contributions.

For eligible tasks:

- B = (battery − required battery) / (1 − required battery), clamped [0,1].
  When required battery is exactly 1, reserve is 0; there is no headroom.
- T = max(0, 1 − graph travel ticks / travel_scale_ticks), default scale **4**.
- K = maximum relevant observation confidence × max(0, 1 − observation age /
  freshness_ticks), default window **4**. Task-related subject mapping is explicit
  public mission vocabulary in `task_subjects`. Only received/direct evidence at
  the task's location and subject counts. Missing/unmapped/stale evidence means
  K=0, not safe. Identical copies cannot boost the maximum. Ties select latest
  observed tick then stable evidence ID. Observation value does not determine K:
  this factor measures information quality, not hazard risk. It is not fusion or
  calibrated certainty and does not resolve conflicting reports.
- R, only when positive resource demands exist, is the minimum across demanded
  resources of (available amount − demand) / agent capacity. Eligibility already
  guarantees positive required capacity/stock for positive demands. R measures
  remaining reserve as a fraction of capacity. Unrequired resources do not improve
  the score. With no positive demands this factor is omitted, rather than fabricated.

S = sum(wᵢ × factorᵢ) / sum(wᵢ for active factors).

Defaults: battery **0.45**, travel **0.35**, information **0.20**, resource **0.20**.
With no positive resource demands the first three already sum to 1. With resource
demands all four normalize by 1.20. Nonnegative finite weights are configurable;
the always-active weights must have a positive finite sum. Each result includes
raw normalized factors, normalized weights, contributions, explanation and selected
immutable KnowledgeEntry provenance (original source/observed tick, received tick,
forwarding sender/path/message identity). `evidence_ids` is a convenience property.

Workload/availability and exact capabilities are enforced as hard constraints rather
than redundant weighted factors. Mission context determines relevant subject mapping
and explicit restrictions. Task urgency, severity, deadline and priority do not
increase suitability. The weights are a transparent baseline, not optimized,
validated, novel or evidence that one agent is globally best.

```python
from coopai.eligibility import EligibilityConfiguration
from coopai.suitability import SuitabilityConfiguration

engine = scenario.create_engine(with_priority=True)
engine.enable_self_evaluation(
    scenario.initial_world.environment,  # explicit public topology disclosure only
    EligibilityConfiguration(minimum_battery=0.15, battery_per_travel_tick=0.02),
    SuitabilityConfiguration(battery_weight=0.5, travel_weight=0.3,
                             information_weight=0.2, travel_scale_ticks=6),
)
```

## Agent ownership, reevaluation and replay

Generic engines opt in with `scenario.create_engine(with_suitability=True)`;
this also installs existing local perception and explicit initial task announcements.
The GUI enables it. Each `AgentSuitabilityState` owns only its own results/history
and public settings/topology. It reads its owner-only context after local knowledge,
message delivery and priority processing, before sharing policies/controllers.
`AgentView.suitability` contains that agent's results and nested eligibility only.
Peer scores are not automatically exchanged. The world remains authoritative only
for trusted simulation adapters. Hidden world task registration is not announcement.
Task knowledge reuses Task 7's versioned announcement registry; the pure evaluators
can operate independently of priority scoring with explicitly provided announcements.

At mission start an initial tick-0 evaluation uses current known inputs; processing
tick 0 evaluates again after sensing. Each decision tick reevaluates the few known
tasks (no cache or general event-invalidation framework). This covers own battery,
resources, availability, commitment/location, requirement/announcement updates and
received evidence/freshness changes. Cost is small for the four-agent/four-task
scenario. Histories are chronological, results ordered by task ID, **not** globally
ranked. Reset clears results/history and restores initial own world/task knowledge
and settings; replay reproduces full outputs. No reassignment is performed.

`suitability_snapshot(agent_id)` / `suitability_history(agent_id)` are privileged
renderer/test diagnostics over already-produced results, never decision inputs
about other agents. The headless demo prints each owner's results separately with
an explicit diagnostic label; it neither compares agents nor chooses winners.
Python API separation is not a hostile-code security sandbox.

## Actual four-room examples

Command: `.venv/bin/python -m coopai.suitability_demo`.
After sensing tick 0, before any message arrives:

| Task | Eligible agents and self-scores | Travel ticks |
|---|---|---|
| Fire verification | A 0.970; C 0.599 | A 0; C 2 |
| Gas inspection | B 0.930 | B 0 |
| Person search | C 0.925 | C 0 |
| Routine inspection | A 0.625; B 0.574; C 0.599; D 0.712 | A/B/C 2; D 1 |

A rejects gas inspection (missing gas sensing); B rejects fire verification
(missing thermal sensing); D rejects person search (missing visual sensing).
D's assistance operation does not substitute for a missing sensor. All initial
agents have exploration, and no task requires assistance or support kits. The
scenario was not edited to force these outcomes. Separate fixtures cover resource,
unreachable, failure and commitment cases absent from initial configuration.

B gas inspection: at tick 4 battery 0.9, B=0.8/0.9, T=1, K=0.9,
S=0.45×(0.8/0.9)+0.35+0.20×0.9=**0.930**. The existing tick-5
battery event changes battery to 0.4: B=0.3/0.9, S=**0.680**, still eligible
above minimum 0.1. Replay comparison prints `self_evaluation_replay_equal=True`.
These are deterministic algorithm outputs, not mission performance metrics.

## Inspector and next integration

Press **E** or Eligibility, then **1–4** or click an agent. The panel shows only
that agent's known tasks, eligibility and rejection reasons, eligible scores,
travel ticks, factors/normalized weights and evaluation ticks. Priority is labeled
separately. Ineligible scores display N/A. Rendering reads cached runtime results;
it does not evaluate or influence them. Existing knowledge/message/priority panels
and lifecycle controls remain available. The separate map is simulator truth.

Future decentralized allocation can combine task priority and self-feasibility/
suitability in explicit bids sent peer-to-peer, with conflict resolution, stale bid
handling, ownership confirmation and failure recovery. No bids, winner selection,
assignment/reassignment, movement, execution, ML or Gazebo integration are implemented.
Static public topology, benign peers, categorical evidence and abstract energy remain
limitations. Large-scale history retention and dynamic topology need later designs.

Task 9 now consumes these owner-local results through explicit decentralized bidding;
see [allocation protocol](decentralized_allocation.md). Its commit adapter reuses
HardEligibility for the selected applicant only. The scoring components themselves
remain free of assignment side effects; movement/execution remain future work.


Task 10 adds paired own position coordinates and an explicit `consumed_resources`
quota to SelfEvaluationContext. HardEligibility credits that quota only for
continuation of the same owned active task, never a pending/new/competing task.
Suitability resource reserve uses actual remaining stock after unmet demands.
This permits once-at-begin consumption without repeatedly requiring/consuming the
same quota. Graph Dijkstra is shared by travel estimation and public route planning;
physical motion time/energy still uses geometry and synthetic execution costs rather
than the earlier graph-tick estimate. See [Task 10](movement_execution.md).
