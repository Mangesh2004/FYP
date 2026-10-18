# Dynamic reassessment and decentralized recovery — Task 11

Task 11 closes the loop from execution failure to fresh peer negotiation and actual
replacement execution. The original four-room JSON is unchanged. Initial inspections
remain as before; extended, separate fixtures introduce failures and new work. This
is a reproducible baseline, not a claim of novelty, optimality or distributed consensus.

## Architecture and information boundaries

| Module | Responsibility | Inputs |
|---|---|---|
| `recovery_models.py` | Objective/attempt, policy, request/feedback/audit contracts; strict `recovery.v1` schema | Immutable domain data |
| `recovery.py` | Independent `AgentRecovery` for each agent: heartbeat, local suspicion, retry and availability dissemination | Own AgentView, delivered messages and tick-bound peer send port |
| `recovery_adapter.py` | Atomic objective retry admission, lease validation and attempt history | Trusted physical state; never candidate ranking |
| Existing priority/eligibility/suitability/allocation | Reevaluate local evidence/state and negotiate fresh attempts | Each agent's own announcement/evidence/evaluation state |
| Existing execution | Plan, move, work and report for newly admitted attempts | Own assignment, public geometry and learned procedure |
| `recovery_metrics.py` | Evaluation-only joins of actual audit, transport, allocation and execution events | Privileged read-only logs |
| GUI/demo | Show objective history, actual ownership/movement and measured timelines | Explicit simulator diagnostics |

No recovery policy receives the engine, global task catalogue, authoritative lease
expiry, other agents' private scores, global failures or evaluation metrics. Public
initial mission announcements and explicit per-objective recoverability are supplied
at installation. Feedback discloses only the result of the requester's submitted
attempt. The physical adapter can know a procedure without exposing its task to agents.
New procedures reach agent executors only with explicit announcements/messages.

Generic engines opt in with `scenario.create_engine(with_recovery=True)`. This enables
existing execution/allocation/communication and their prerequisites. `with_execution=True`
alone retains Task 10 behavior. The GUI enables recovery with autonomous execution;
`--no-recovery` restores terminal-only failure behavior.

## Objective and attempt lifecycle

An objective has a stable ID; the initial attempt retains its original Task ID.
Retries use `objective-id::attempt:2`, then `::attempt:3`, etc. Existing `Task.transition`
is unchanged: FAILED, COMPLETED and CANCELLED are terminal. A new pending Task is an
explicitly admitted *new attempt*, not a transition out of FAILED. It retains the
objective's requirements, original creation time and soft deadline, preserving overdue
priority pressure. Its new TaskAnnouncement starts at version 1 and its new task ID
changes task digests, round IDs, proof references, command targets and notice IDs.

| Objective/attempt situation | Meaning / permitted next action |
|---|---|
| Pending / unassigned | Existing allocation may coordinate it |
| Assigned / in progress | One authoritative owner and one active agent commitment |
| Interrupted or failed attempt | Physical stop; FAILED Task and failure outcome retained |
| Awaiting recovery | Failed current attempt, retry permitted but no accepted recovery request yet |
| Recoverable / unassigned | Fresh pending attempt admitted; agent awareness still needs announcement |
| Permanently failed | Explicit nonrecoverable policy, cancelled objective, or exhausted attempt budget |
| Completed | Successful latest attempt; never reopened |

Attempt records retain prior owner, assignment/end ticks, work progress, outcome reason
and immutable task snapshots. Progress **restarts from zero**; no partially completed
inspection is credited to another owner. Consumables are not refunded, and a fresh
attempt cannot borrow the old attempt's consumed-resource quota. Current procedures
are abstract, reversible inspections/searches; arbitrary irreversible operations
would need a separate interruption policy before enabling release.

Default installation marks its known inspection objectives recoverable. Supply
`recoverable_objectives=frozenset({...})` to `enable_recovery` for a restrictive policy;
an empty set prohibits retries. Failure alone is insufficient: admission also checks
policy, attempt budget, proof, current attempt, lifecycle and permitted requester.

## Detection, heartbeat and lease

Default RecoveryConfiguration: heartbeat every **2 ticks**, lease **8 ticks**, request
retry every **3 ticks**, availability repeat every **3 ticks**, maximum **4 attempts**
including the original. Invalid/zero intervals and lease ≤ heartbeat are rejected.

An operational owner emits a typed heartbeat with task ID, admitted commit reference
and issue tick. Its own validated heartbeat intention renews the authoritative lease;
peer receipt is independently subject to the existing simulated network. The validator
checks BUSY state, matching active ownership and admitted proof before renewing.
Assignment creates an initial bounded lease even before the first heartbeat. Peers
retain actual message issue timestamps, not reception time, so delays do not extend
freshness. A heartbeat arriving before an ownership closure can be buffered for an
already announced pending task; suspicion requires locally learned ownership.

Operational agents detect their **own** execution failure feedback immediately. They
request recovery and the existing execution controller sends typed failure notices.
Peers can act on those notices. OFFLINE/FAILED agents send no heartbeat, failure notice
or recovery request. Other agents eventually suspect silence after their heartbeat
window (or a grace window from first learning ownership if no heartbeat was received).
They request recovery through a restricted local policy, rather than reading global
failure logs. Repeated/late messages cannot renew expired old-attempt ownership.

A peer timeout is a **suspicion**, not proof of physical death. The validator rejects
release while the real owner's locally renewed lease remains live. Therefore a
healthy owner can continue under network partition. A completed/cancelled objective
also rejects recovery, even if completion messages are lost. Failed attempts may
be recovered promptly on explicit failure reports; silence-based recovery waits for
lease expiry. In all cases the request must refer to the current admitted attempt.

The lease measures simulated ticks: pause stops clock advancement and expiry. It is
an admission contract, not a guarantee of network failure detection within eight
wall-clock seconds. Lost initial closure/heartbeat messages can increase detection
latency. Python API encapsulation is not a hostile-code sandbox.

## Safe release and fresh negotiation

Execution failure already stops motion and atomically clears the task owner and
agent commitment. Recovery admission checks the already failed attempt without
releasing it a second time. For explicit release or an expired still-active attempt,
the validator first invokes the physical stop/failure path. Successful retries append
one new pending task and notify **only the requesting agent**; competing duplicate
requests fail the current-attempt compare-and-set check. A deterministic requester
order resolves duplicate admission, but does not select a replacement owner.

The requester repeats availability over actual P2P envelopes; receiving agents can
forward it. Semantic duplicates are idempotent. Older objective generations are ignored;
changed content for an existing attempt is rejected. Availability never downgrades a
locally assigned/terminal current task. An accepted successor announcement lets a peer
mark its predecessor failed; failed history is retained independently of the new task.

Priority, hard eligibility and suitability recompute through their existing modules.
The existing rotating Contract Net coordinators open fresh rounds at public epoch
boundaries. Only those agents compare **received** bids. Candidates still recheck
capacity and eligibility at acceptance/admission. Failed/offline owners cannot bid,
and reconnecting does not restore the old task/proof. Stale bids/ACKs/commands are
confined to their old attempt; the original task remains terminal. There is at most
one active attempt per objective and one active commitment per agent.

Task 9's centralized simulator admission and Task 11's retry validator maintain
strict authoritative consistency. Neither calculates a global optimum nor chooses a
replacement. This is an explicit **centralized state-validation assumption**, not
Byzantine authentication or distributed consensus under arbitrary partitions.

## New task arrival and dynamic reassessment

`introduce_objective(task, spec, recipient_id, recoverable=True)` is a trusted runtime
discovery-event interface. Initial tasks belong in scenario setup. It validates/registers
physical truth and the procedure, then explicitly announces to **one agent**. That
agent's recovery policy shares the typed mission announcement; other agents learn
only on delivery. Merely adding a world task does not disclose it to every agent.
GUI metadata supports runtime tasks without changing decision visibility.

New/higher-risk observations, severe hazards, battery/stock changes, changed capabilities,
availability changes and announcements continue through existing per-agent priority,
eligibility and suitability reevaluation. Temporarily infeasible pending attempts
stay unassigned; restored capacity/capability permits later rotating rounds. The
simulator never repairs feasibility by selecting a hidden replacement.

## Preemption status

**Priority-driven preemption is deferred**, as permitted by the Task 11 request.
Agents do not abandon valid commitments for a small priority difference. New urgent
work waits for available capacity and is still prioritized by the existing local
protocol. Explicit operator release of current reversible inspection work is supported;
it is not claimed as an autonomous priority-preemption algorithm.

A follow-up must define interruption safety per procedure, explicit priority improvement
threshold, switching/progress cost, cooldown/hysteresis, restart/continuation semantics
and actual decentralized ownership transfer. No untested threshold-only helper was
added or presented as implemented preemption.

## Loss, partitions, timeout and liveness

Announcements repeat, local release requests retry, and allocation phases expire using
existing configured windows and rotating coordinators. Lost failure notices can be
recovered through heartbeat silence. An offline original owner cannot announce a
failure but is not needed for the replacement round. Reconnection learns fresh
availability from subsequent repeats without reclaiming the previous attempt.

Reliable enough eventual delivery, an operational coordinator, a willing eligible
candidate and sufficient physical resources are liveness assumptions. Every allocation
round is bounded; the mission horizon and attempt budget bound experiments. Waiting
pending objectives do not automatically become permanent failures just because all
bids fail. If no replacement is eligible/reachable, state remains explicitly unassigned
or awaiting recovery, with no invented completion. An eligible coordinator can use
its existing self-bid/award/accept/commit path, even when peer bids are lost; this is
part of the Task 9 baseline and must be disclosed in experiments.

## Actual demonstration results

```sh
.venv/bin/python -m coopai.recovery_demo
.venv/bin/python -m coopai.gui --recovery-fixture failure
.venv/bin/python -m coopai.gui --recovery-fixture new-task
.venv/bin/python -m coopai.gui --recovery-fixture disruption
.venv/bin/python -m pytest -q
```

Fixtures extend the horizon to 80 ticks; all original JSON/default events are retained.
The demo prints actual bids, histories, metrics and complete reset replay equality.

### A — D battery failure

| Event | Actual tick |
|---|---:|
| D battery becomes 0.03; motion stops; failed attempt releases ownership | 9 |
| A/B/C suspect heartbeat silence; validated fresh attempt created | 15 |
| A announces availability | 16 |
| B/C learn it by P2P delivery | 17 |
| Fresh allocation round opens, coordinator A | 20 |
| A wins and ownership is admitted | 23 |
| Replacement movement starts | 25 |
| A reaches Room 4 through doors/corridor | 31 |
| Inspection restarts | 32 |
| Evidence-backed completion | 35 |

Received suitability bids: **A 0.614773, C 0.584091, B 0.307955**. A has completed its
fire inspection and is free; it has the greatest battery reserve among eligible peers
with the same graph travel cost. It wins through the actual bids, not hardcoded recovery
logic. A separate test removes A's exploration ability: A still requests/coordin­ates,
but **C wins and completes**. D remains OFFLINE at its last corridor pose. Failed
attempt `task-4` stays FAILED; `task-4::attempt:2` completes under A.

Detection-to-completion is **20 ticks**; failure-to-completion is **26 ticks**. There are
2 total allocation rounds for this objective, 353 task-related wire sends and no drops
at the 80-tick horizon. D sends no failure announcement because it is offline.

### B — new urgent inspection

B alone receives the new task at **11**. A/C/D learn at **12**. Coordinator A opens the
round at **20**. D's bid **0.766229** exceeds A/C/B's bids; D has finished its original
inspection and is already in Room 4. Admission is **26**, arrival validation **28**,
execution start **29**, completion **32**. No fabricated extra movement is emitted.
The desired deadline 20 is missed by **12 ticks**, honestly recorded. Busy agents
are not preempted. There are 351 task-related wire sends at horizon 80.

### C — communication disruption

All messages sent in **[9,26)** are lost. Failure/release behavior remains safe; A
learns its own accepted retry and self-coordinates at 20 with only its own valid bid,
commits at 23 and completes at 35. This run demonstrates safe local coordination plus
**announcement retries**, not successful peer agreement during the partition. B/C
learn availability only at **29**; a redundant later round cannot override A's current
commitment. There are 3 recorded rounds, **66 task-related dropped envelopes** and
358 sends at horizon 80. No conflicting authoritative owner is created. Tests also
cover lost release notices, live-owner partition rejection and no-eligible-replacement
fixtures that stay unassigned.

## Metrics definitions

`recovery_metrics(engine)` returns frozen per-objective measured records. Missing
observations are **None**, not guessed zeroes. Failure/detection/availability announce/
physical release/reallocation/assignment/movement/arrival/restart/completion ticks are
joined from their real logs. Failure announcement is the first actual execution-failure
wire send, distinct from later availability. Recovery duration is first local suspicion
to latest completion; a separate field measures failure-to-completion. Initial and
replacement assignment timestamps are separate. Arrival without travel is possible.

Rounds count distinct coordinator-opened round IDs across attempts, including unsuccessful
or redundant rounds. Message counts cover actual allocation/execution/recovery envelopes
associated with the objective's attempt IDs, counted once per send/drop; observation and
generic status traffic are excluded. `no_bids` counts emitted NO_BID actions, including
local actions. `rejected_bids` counts logged stale bid/award and rejected award/acceptance/
commit actions, not guessed unavailable agents. Switching counts admitted task changes by
agents with a previous different admitted task ID. Deadline lateness is max(0, completion
minus original desired deadline); absent deadline/incomplete outcome is None. Metrics
never affect allocation. Counts depend on horizon because notices/availability repeat.

## GUI and verification

**H / Recovery** opens the inspector; **R remains reset**. Recovered objectives appear
first, with current owner, prior failed owner, generation/status/progress/history,
failure/detection/release timing, replacement/completion, observed local priority range,
round/coordinator and recent peer events. Scroll for all objectives/events. Completed
attempt owners are attribution, not active BUSY commitments. Historical target rings
are omitted when a successor exists; history remains visible in the inspector/status.
The renderer uses actual positions/routes and never allocates or advances simulation.
K/M/P/E/L/X, 1–4/circle selection, play/pause/step/reset/speeds remain available.

[Recovery screenshot](task11_recovery.png) · [Actual verification](task_11_verification.md).
Reset restores world, procedures, announcements, local policies, proof/lease state,
commands, RNG streams and all runtime histories. Same configuration/runtime/seed
reproduces world, evidence, wire, allocation, execution, objective audit and metrics.
Native macOS Cocoa and headless SDL smoke tests exercise rendering and reset.

## Limitations and research readiness

Benign fixed-roster agents, shared simulation time, trusted physical admission, static
point geometry, synthetic energy and inspection-only restart semantics remain assumptions.
No automatic charging, stock replenishment, inaccessible-objective repair, irreversible
preemption, dynamic membership, Byzantine defense or real networking is supplied.
The graph travel suitability estimate does not guarantee enough energy for all geometric
movement/work. Soft deadlines can be missed. Repeated availability is intentionally
simple and has measurable communication overhead; no acknowledgment-based suppression
or advanced gossip framework was added. Indefinite partition can prevent convergence.

The loop and measured fixtures are ready for controlled comparative experiments.
Alternative allocation/priority/recovery baselines, fair multiple-seed trials, tuning/
evaluation separation and statistical analysis remain necessary before any comparative
research claim. No ROS2, Gazebo, ML, global optimization or new dependency was added.


Task 12 reuses this loop in a combined physical-failure/healthy-peer-disconnection
mission and adds an explicit pending-resource case. It does not implement preemption
or comparative experiments. Fresh sessions are selected through the GUI registry;
current roadmap is Task 13 adaptive refinements, Task 14 research comparisons, Task 15
final validation/presentation. See [Task 12 review missions](task_12_scenarios.md).
