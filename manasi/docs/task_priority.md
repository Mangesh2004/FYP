# Dynamic mission-aware task priority — Task 7

## Run and configure

```sh
.venv/bin/python -m coopai.priority_demo
.venv/bin/python -m coopai.gui
.venv/bin/python -m pytest
```

GUI priority is enabled automatically; P/Priority opens the selected agent's
priority inspector. Keep that inspector open and press 1–4 or click a circle to
compare agents. Generic headless engines opt in with
`scenario.create_engine(with_priority=True, with_communication=True)`. With no
communication flag, sensing/priority still work independently. No dependencies
were added.

The optional `priority` section of `scenarios/four_room_disaster.json` contains
weights, time windows, unknown-risk prior and task-type rules. Older scenario files
without it use validated defaults. Strict decoding rejects unknown/missing fields,
nonfinite/out-of-range values, invalid windows and duplicate type/value mappings.
Rules refer to task types and condition subjects, never room IDs.

## Information boundaries and task announcements

The simulator's Task registry remains authoritative physical state. It is not
supplied to the evaluator or callbacks. AgentPriorityState owns a separate dictionary
of explicit TaskAnnouncements for one agent. An announcement contains a validated
Task snapshot, task type, publication tick, positive version and first-announced
initial urgency. Task snapshots provide mission instructions, not proof of hazards.

The initial scenario explicitly announces all four initial tasks to every agent at
setup, as authorized mission objectives. This grants task location/requirements/
urgency/deadline knowledge without confirming fire/gas/human conditions or allocating
any owner. `engine.announce_task(agent_id, announcement)` is an explicit trusted
mission interface, not automatic registry synchronization. A new physical task is
hidden until an authorized announcement names that recipient. Updating world truth
alone never updates task awareness or a calculated priority. Task announcements over
peer messages are not implemented; the explicit mission interface is sufficient
for this milestone. Transport TASK kind still has no payload schema.

Higher announcement versions replace known task snapshots; older/exact duplicates
are ignored, changed content at the same version is rejected, and newer versions
cannot regress publication time. The first announcement's initial urgency is retained
for GUI comparison while the scoring U factor uses currently announced urgency.
Initial announcements are sealed at start, restored by reset, and runtime task or
mission-setting announcements are discarded/regenerated during replay.

AgentView now contains own known_tasks, own immutable evidence_history and own
calculated priorities. Existing fields and empty defaults remain compatible. Full
local evidence history is needed to avoid losing a strong fresh hazard report when
the simple latest-evidence index selects a newer weak/contradictory report. It contains
only that recipient's recorded direct/peer evidence, not another store's private
history. PriorityContext validates recipient ownership and rejects future evidence
and announcements; evaluating an unknown task ID raises ValueError.

`priority.py` imports no simulator, world, renderer, transport or RNG. The engine
invokes independent per-agent state objects in DECISION using each one's local view.
This common clock orchestration is not a global scorer reading all truth. Scores
can legitimately differ between agents. No capabilities, batteries, travel costs,
agent rankings, bids or assignments are computed here.

## Baseline mathematical formulation

For each known active task, define five factors in [0,1]:

- **U:** currently announced base mission urgency.
- **D:** deadline pressure = clamp(1 − (deadline − current_tick) / W, 0, 1).
  D = 0 when there is no deadline. W defaults to 8 ticks.
- **E:** environmental risk estimated from local evidence, defined below.
- **I:** mission importance from the configured task-type rule; unknown types
  use default importance 0.5 and unknown environmental risk.
- **Q:** remaining uncertainty = 1 − effective confidence of the selected evidence;
  Q = 1 with no usable evidence.

Normalize nonnegative weights by their positive finite sum. Default score:

```text
P = 0.25 U + 0.20 D + 0.35 E + 0.15 I + 0.05 Q
```

The baseline weight sum is 1; any valid configured sum is normalized. Scores are
bounded [0,1] and are comparative importance, not probabilities. Completed, failed
and cancelled task announcements gate P to zero. Raw factor contributions remain
visible for explanation but do not sum to the gated terminal score. Overdue active
tasks retain D=1 rather than expiring silently.

Weight rationale: environmental risk receives the largest share so sensed escalation
can matter; urgency anchors announced mission intent; deadline pressure expresses
time sensitivity; task-type importance captures mission emphasis; a smaller positive
uncertainty term preserves the need to inspect unknown conditions without allowing
uncertainty alone to dominate. These are transparent illustrative baseline choices,
not empirically optimized weights. W=8 matches a short 16-tick demonstration horizon,
but is independent configuration in ticks, not a hardcoded room or simulation rate.

Configured mission importance is fire verification 0.90, gas inspection 0.65,
person search 1.00 and routine inspection 0.15, reflecting the stated human/hazard
mission emphasis. Supported condition severities are explicit JSON mappings: fire
present 0.80/severe 1.00, gas present 0.70/severe 1.00, person possible 0.90/present
1.00, and mapped clear/cleared/absent values zero. Possible human presence is a
conservative search-importance input, not confirmation of a person. Unmapped values
remain unknown and never become safe by default.

## Confidence, freshness and conflicts

For a relevant sample (same announced task location and configured condition subject):

```text
age = current_tick − original_observed_tick
F = max(0, 1 − age / H)        H defaults to 4 ticks
C = observation_confidence × F
R = unknown_risk + C × (mapped_severity − unknown_risk)
```

The unknown-risk prior defaults to 0.5. Thus evidence is interpolated toward unknown
as confidence/freshness falls, never toward confirmed safety simply because it is
missing or old. At age H or more, confidence influence is zero; evidence remains in
history but is excluded. Unknown values and zero-confidence samples are also excluded.
The GUI's knowledge freshness threshold and priority decay window are separate
settings; defaults align (knowledge becomes stale after age 3, priority influence
is zero at age 4).

Deduplicate original evidence IDs first, retaining first receipt/provenance. Reusing
an ID with different content is an error. Multiple forwarding routes or modalities
never multiply confidence.

Among usable samples, discard a sample for scoring if another has **at least as
high original confidence and at least as recent an observed tick**, with at least
one strictly better. This confidence/freshness quality frontier prevents a weak
old report from overriding a stronger fresher one. A newer but low-confidence safe
report does not automatically erase an older high-confidence hazard: the quality
tradeoff remains explicit.

On the remaining frontier, choose the **maximum R**, conservatively. Ties use
higher effective confidence, then greatest evidence ID. Different mapped severities
on the frontier produce `conflict`; all-zero mapped severity produces `safe_evidence`;
other evidence produces `evidence`. No usable samples produce `unknown`. E equals
selected R and Q equals 1−selected C. A weak safe observation still leaves residual
risk/uncertainty; fresh confidence-1 safe evidence yields E=Q=0. Equally credible
safe/hazard conflicts retain the higher risk while preserving uncertainty and all
references. This is a conservative selection policy, not Bayesian fusion or a claim
that the selected estimate is a fact.

Examples: fresh confidence-0.5 severe fire gives E=0.75, Q=0.5; the same sample at
age 4 gives E=0.5, Q=1. A confidence-0.9 hazard at age 1 remains alongside a new
confidence-0.2 safe report, and conservative risk wins. A newer confidence-0.9 safe
report dominates an older confidence-0.2 hazard. These behaviors are tested directly.

## Output and reassessment lifecycle

PriorityResult contains agent/task IDs, bounded score, evaluation tick, normalized
weights/factor contributions, evidence references, evidence status and explanation.
PriorityEvidence retains original KnowledgeEntry/provenance, age, freshness,
effective confidence and disposition (selected/frontier/dominated/stale/unmapped/
zero_confidence). Alternate PriorityEvaluator implementations can implement the
same `evaluate(task_id, PriorityContext)` contract; AgentPriorityState accepts an
injected evaluator. No global context or scene-specific room assumptions are needed.

At start, each agent computes an unknown-evidence mission baseline from its initial
announcements. During DECISION, after local samples and remote messages are recorded,
each state updates before sharing policies/ordinary controllers. Callbacks receive
only that agent's results. Rendering reads the cached results and never evaluates
or mutates priority state.

A deterministic signature includes announced task versions/content, mission settings,
relevant unique local evidence, clamped evidence ages and clamped deadline remaining
time. Changed inputs, relevant evidence delivery, aging, deadlines or explicit mission
updates trigger reevaluation; identical signatures retain the prior result/history
and last-evaluation tick. This is input-triggered caching, not a separate event engine.
Frequent genuine local samples still trigger evaluation. No RNG or wall time enters
calculation. All initial/task/mission settings and caches participate in reset; the
same announcements/evidence/messages reproduce full priority histories.

Trusted runtime callbacks may explicitly call
`set_priority_configuration(agent_id, configuration)` or announce task revisions.
Each recipient receives only its own update; the engine never infers a mission rule
change from hidden environment truth. Fire escalation affects priority only after a
permitted local observation or delivered peer sample describes it.

## Actual demonstration

Two-tick communication latency was used. Calculated fire-verification examples:

| Evaluated tick | A priority | B priority | What B knows |
|---|---|---|---|
| Mission start, before sensing | 0.58500 | 0.58500 | Task announced, hazard unknown |
| 0 | 0.63175 | 0.58500 | No fire evidence delivered |
| 2 | 0.68175 | 0.65837 | A's tick-0 fire-present evidence delivered |
| 3 | 0.76625 | 0.67169 | Old fire-present evidence; A directly sees escalation |
| 5 | 0.81625 | 0.76313 | A's tick-3 severe-fire evidence delivered |

These numbers are read from the implemented formula and configuration, not scripted
scores. Priority can decrease as evidence ages even while D increases; deadline
pressure is individually monotone, not a guarantee that the combined score always
rises. Routine inspection remains comparatively lower under its announced urgency
and mission importance, while its unobserved clear condition stays unknown to agents.
The demo verifies complete priority/evidence history equality after reset and asserts
no task owner was selected.

The GUI shows known tasks ranked by score with scenario/first-announced initial
urgency, U/D/E/I/Q factors, evidence source and last evaluation tick. P toggles the
inspector; 1–4/circles select another agent while retaining priority mode. Before
start or just after reset, announcements remain but results await Play/Step. Engine
tick on the top bar is the next tick; evaluation timestamps denote processed ticks.
See [actual native priority preview](task7_preview.priority.png).

## Assumptions, limitations and next milestone

This is an explainable weighted baseline, not proven research novelty or superiority.
Weights, severities and confidence comparability are simulation assumptions requiring
later sensitivity studies and controlled baseline comparisons. Quality-frontier
selection can remain conservative on confidence/recency tradeoffs; it does not detect
malicious reports, estimate source reliability, predict sensor failures or resolve
probabilistic contradictions. Evidence histories/results are retained for the bounded
mission, without a long-run memory policy.

Physical world tasks and agent announcements can diverge; no implicit synchronization
or task discovery is added. Priority supports explicit new announcements/updates,
but dynamic new world tasks still need complete GUI TaskDetails as required by the
existing scenario renderer. Task-announcement peer schemas, eligibility, suitability,
auctions, assignment/reassignment, movement, execution, Gazebo and ML remain absent.

Ready for eligibility and suitability using hard requirements, permitted agent state,
known topology and these agent-local priorities. Priority is task importance, not
proof an agent can execute it and not a choice of owner.
