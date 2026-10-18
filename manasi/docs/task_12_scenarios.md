# Multi-scenario autonomous missions — Task 12

Task 12 provides five self-contained JSON missions, one reusable registry, a headless
runner and an interactive scenario menu. All use the existing decentralized decision
pipeline. This milestone verifies functional behavior; it adds no comparison allocation
methods, benchmarks, learning, optimization, preemption, Gazebo or ROS2.

The academic review is **21 October 2026**. These commands assume the source checkout
and existing virtual environment at the repository root.

## Run the Multi-Agent Simulation

```sh
.venv/bin/python -m coopai.gui                         # opens scenario selection
.venv/bin/python -m coopai.gui --scenario corridor     # primary live demonstration
.venv/bin/python -m coopai.gui --scenario distributed
.venv/bin/python -m coopai.gui --scenario emergency
.venv/bin/python -m coopai.gui --scenario failure-disruption
.venv/bin/python -m coopai.gui --scenario constrained

.venv/bin/python -m coopai.scenarios_demo --all
.venv/bin/python -m coopai.scenarios_demo --scenario emergency
.venv/bin/python -m pytest -q
```

Install only if setting up a new environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test,gui]'
```

Task 12 reused the existing native arm64 environment and installed no dependencies.
The headless runner needs no Pygame import or GUI interaction.

## Architecture and scenario schema

`mission_scenarios.py` contains an immutable ID → JSON path registry and a frozen
`MissionScenario` value. Each file under `scenarios/missions/` defines its complete
geometry, agents, conditions, objectives, events, seed and horizon using the existing
scenario schema. The wrapper delegates physical/domain construction to
`scenario_from_dict`; it does not duplicate decision or movement logic.

Additional fields:

- `demonstration`: ID, name, description, demonstration objective and `expected`.
- `communication`: latency, loss probability, TTL, optional directed links and
  configured disconnection intervals, using existing CommunicationConfiguration.
- Agent `position: {"x": ..., "y": ...}`: optional explicit coordinates within
  the declared `location`; omission preserves legacy center initialization.
- `allow_infeasible_objectives`: optional boolean, default false. Only the constrained
  mission opts in. Invalid structures, resources and unknown review capabilities
  are still rejected; this permits an intentionally unserviceable objective.

`expected` contains only completed/incomplete objective counts, minimum number of
agents that actually move, and whether recovery must occur. These are **post-run
assertions**. They never enter agent views, priority, eligibility, bids or execution.
No configuration contains winning/replacement agents, movement sequences, score
shortcuts or scripted completion. Initial task announcements remain explicit public
mission inputs; runtime discoveries disclose to one recipient and then use P2P delivery.

Supported external actions extend the existing typed event loader:

| Action | Effect |
|---|---|
| `set_condition` | Existing authoritative condition change; agents learn via sensing/messages |
| `set_battery` | Existing resource update |
| `power_failure` | Zero physical power; use the existing stop/failure path and release a current commitment |
| `set_connected` | Change transport connectivity only; do not change physical availability |
| `discover_task` | Validate/register a fresh pending objective/procedure and announce to one configured recipient |

Task discovery uses the same Task JSON schema as initial tasks, with creation time
set to the event tick. Its metadata also requires `recipient` and `recoverable`.
Event IDs/references, discovered task IDs, deadlines, capabilities, procedures,
coordinates, stock/capacity, mission bounds and communication settings are validated.
Unknown actions such as assignment, movement overrides and forced completion are rejected.

The review vocabulary is thermal/gas/visual sensing and exploration/simulated-assistance
operations. Unknown review tags or omitted procedure requirements are rejected. Core
domain capability tags remain extensible, and the original loader's default feasibility
checks remain in force for existing scenarios. Negative/finite coordinates are supported
when the geometry permits them; walls, outside positions and unreachable layouts fail.

`MissionScenario.create_engine()` installs existing perception, knowledge, communication,
priority, hard eligibility, suitability, allocation, execution and recovery. Public
allocation windows use `AllocationConfiguration.for_latency` for the configured network.
Each switch creates an independent engine, clock, RNG, queues, stores, local policies,
proof ledger, leases, procedures and histories.

`mission_report.py` derives summaries, significant events and selected-agent explanations
from stored evidence/evaluations/protocol/execution/recovery records. It performs no
allocation. `scenarios_demo.py` prints actual assignments and received bids, observations,
priority changes, motion/execution/recovery, final physical state/battery and replay.
Same-tick diagnostic events are grouped by category; the engine's existing phase order
remains the authoritative mechanical ordering.

## Common environment and capabilities

The existing four rectangular rooms, shared corridor, entry and validated doorways
are preserved. Destination coordinates are room centers. A has thermal + exploration;
B gas + exploration; C visual/thermal + exploration; D exploration/simulated-assistance
except in the constrained case, where D lacks exploration. Sensing does not imply
hazard mitigation. Initial objective IDs retain their existing meanings:

| ID | Procedure / room | Required sensing | Work ticks |
|---|---|---|---:|
| task-1 | Fire verification / Room 1 | thermal | 2 |
| task-2 | Gas inspection / Room 2 | gas | 2 |
| task-3 | Person search / Room 3 | visual | 3 |
| task-4 | Routine inspection / Room 4 | none; exploration inspection | 2 |

All require exploration and at least 10% battery. Initial fire/person desired deadlines
are 40 ticks; routine has none. Gas also has deadline 40. Deadlines remain soft priority
pressure, not hard expiry. The same existing procedures require actual arrival, valid
work and fresh direct reports before completion.

## Case 1 — Corridor Deployment

File: `scenarios/missions/corridor.json`; seed 42, horizon 64, half-second ticks,
latency 1, loss 0, TTL 8. No scheduled external disturbances.

| Agent | Initial position | Battery |
|---|---|---:|
| A | corridor (1,5) | 98% |
| B | corridor (3.5,5) | 90% |
| C | corridor (6,5) | 94% |
| D | corridor (8.5,5) | 100% |

All four initial tasks are pending/unassigned. Every agent moves visibly along its
own accepted route. Actual assignments are gas/B at 3 and fire/A, person/C, routine/D
at 6. Fire received bids are A **0.702388**, C **0.682163**. Routine received bids are
D **0.712500**, A **0.702388**, C **0.682163**, B **0.661938**. These come from the existing
agent-local suitability evaluator and actual peer delivery. No owner is configured.

| Task | Owner | Arrival | Execution start | Completion |
|---|---|---:|---:|---:|
| Fire | A | 14 | 15 | 18 |
| Gas | B | 9 | 10 | 13 |
| Person | C | 12 | 13 | 17 |
| Routine | D | 13 | 14 | 17 |

All four complete. Final batteries are A **86.2984%**, B **81.7459%**, C **84.2984%**,
D **89.7459%**. The summary appears once the configured work/events are finished
(next clock tick 19), so the live review need not wait through idle horizon ticks.
Rendering does not stop/change the clock: the user can pause or continue to horizon 64.

[Initial deployment](task12_corridor.initial.png) · [Actual movement](task12_corridor.movement.png) ·
[Mission summary](task12_corridor.png) · [Stored decision explanation](task12_decision.png).

## Case 2 — Distributed Deployment

File: `scenarios/missions/distributed.json`; seed 42, horizon 64; same communication.
A starts in Room 2 at (7,2), B in Room 1 at (2,2), C in Room 3 at (2.8,8.7), D in
Room 4 at (8,8), with the same battery reserves as Case 1.

A is near gas but lacks gas sensing. B is near fire but lacks thermal sensing.
C starts with real direct person evidence, contributing **0.75** information quality
to its person suitability; D is already at routine inspection. A/B must cross room
boundaries, C moves from its actual offset to the destination center, and D validates
arrival without invented movement.

Actual owners are again A/fire, B/gas, C/person, D/routine. A's fire bid **0.614773**
exceeds C **0.594318** with equal graph travel cost and greater battery reserve. C's
person bid is **0.920000**, including direct information quality; D's routine bid is
**0.800000** with zero graph travel. Completions are fire **19**, gas **15**, person
**13**, routine **12**. Final batteries: A **84.5443%**, B **77.5443%**, C **89.9370%**,
D **98%**. Three agents move; two make cross-room trips. Stationary D is a valid
already-at-destination case, not a missing animation.

[Distributed deployment](task12_distributed.initial.png) · [Summary](task12_distributed.png).

## Case 3 — Escalating Emergency

File: `scenarios/missions/emergency.json`; seed 42, horizon 64; corridor deployment.
Initially fire/gas/hazard are clear and person is absent. Fire/person task urgency
is moderate (0.3); initial observations are unknown to agents in the corridor.

At **12**, an external event changes Room 1 fire to severe. A separate event at 12
registers `emergency-verification`, requiring thermal sensing/exploration in Room 1,
urgency 1, duration 2, desired deadline 40. Only **B** initially receives that mission
announcement; A/C/D learn it by actual messages at **13**.

A enters Room 1 while still moving and directly senses severe fire at **13**; B/C/D
receive that report at **14**. Sensing is current-location based and need not wait
for arrival at the room center. Existing fire priority was **0.435** before the
change was observed. A's stored score becomes **0.54125** on fresh direct evidence;
peers' first report yields **0.5146875** with aged confidence. Unknown initial evidence
is not represented as a clear observation, despite the simulator's clear background.

There is **no priority preemption**: A finishes its current fire verification at 18;
other existing commitments also finish. Fresh allocation at 20 receives A's urgent-task
bid **0.901492** and C **0.629708**. A is admitted at **26**, validates arrival **28**,
starts **29**, completes **32**, reporting severe fire. All five objectives complete;
no attempt fails. A ends **84.2984%** battery; other reserves match Case 1.

[Initial ordinary conditions](task12_emergency.initial.png) ·
[Stored priority/evidence timestamps](task12_emergency_evidence.png) · [Summary](task12_emergency.png).
Advanced interruption policy remains Task 13 work; no active mission was silently
reassigned because a displayed priority changed.

## Case 4 — Failure + Communication Disruption

File: `scenarios/missions/failure-disruption.json`; seed 42, horizon 72; corridor
initial deployment and genuine initial negotiation as in Case 1.

| External event | Tick |
|---|---:|
| D loses physical power during movement | 10 |
| C's peer connection goes down; C stays physically operational | 11 |
| C's peer connection returns | 22 |

D stops at its actual last corridor position, becomes OFFLINE and releases the old
attempt. It sends no post-failure notifications. The reason is recorded as operational
power failure, with the configured event description. Heartbeat silence is suspected
at **17**; matching lease/failure validation admits a new attempt. A announces at **18**,
B learns at **19**, fresh coordination opens **20**. Received replacement bids are
A **0.554935**, B **0.531655**; C misses that call during its outage. A wins at **23**,
moves **25**, arrives **31**, restarts **32**, completes **35**. Four objectives finish,
with **one failed attempt and one confirmed reassignment**. D's old attempt remains
FAILED; the successor is a different task/proof ID. The replacement is not configured.

C completes its own person search at **17**, during the communication outage. Peers
can suspect healthy/completed owners while messages are missing; live leases and
completed-task admission guards reject unsafe release. C never becomes physically
failed, and its objective receives no duplicate attempt. Later messages convey true
completion. This is safe authoritative state validation, not distributed consensus.

Final batteries: A **73.8426%**, B **81.7459%**, C **84.2984%**, D **0%**. A separate
negative integration test removes all remaining replacement capacity and preserves
3/4 complete with a pending successor; no success is invented. Early failure before
peers learn ownership can also leave an explicitly awaiting-recovery objective;
heartbeat liveness still requires locally known ownership.

[Failure mission](task12_failure-disruption.png) · [Preserved recovery history](task12_recovery.png).

## Case 5 — Resource-Constrained Mission

File: `scenarios/missions/constrained.json`; seed 42, horizon 72; explicit
`allow_infeasible_objectives: true`.

| Agent | Initial position | Battery | Available support kits | Key constraint |
|---|---|---:|---:|---|
| A | Room 1 (2,2) | 8% | 0 | At fire but below execution eligibility reserve |
| B | Room 3 (2,8) | 65% | 1 | Gas sensing and enough stock for gas work |
| C | Room 2 (8,2) | 92% | 1 | Near gas but has visual/thermal, not gas |
| D | corridor (5,5) | 38% | 0 | Assistance operation but no exploration |

Gas inspection consumes one support kit; routine inspection requires two. No agent
can satisfy the routine requirements. This is a legal incomplete mission, not invalid
stock data. Actual NO_BID/award rejection records explain capability, battery,
resource and capacity failures. Agents do not attempt infeasible work.

B performs gas inspection (assignment **3**, completion **15**) and consumes its kit
once. C performs fire (assignment **6**, completion **18**) then person search
(assignment **26**, completion **40**). A can still provide thermal evidence despite
lacking execution reserve: its peer report raises C's fire priority above person
search at competing awards. That evidence-driven choice explains the later person
completion; no centralized planner reordered it. Received C bids are **0.711591**
for fire and **0.520397** for the later person round; B gas bid **0.371686** includes
its exact-resource-demand factor.

Final outcome: **3 completed, 1 pending/incomplete, 0 failed attempts**. Routine
inspection stays unowned; it is not falsely labeled completed or permanently failed.
Final battery: A **8%**, B **52.5443%**, C **65.1410%**, D **38%**. B's remaining kit stock
is 0, C retains 1, and no resources are fabricated/refunded.

[Constrained summary](task12_constrained.png) · [Actual eligibility/no-bid explanation](task12_constraints.png).

## GUI controls and live review procedure

| Control | Action |
|---|---|
| S / Scenarios | Pause and open selection; click or 1–5 chooses a fresh case |
| Space / Play / Pause | Existing playback controls |
| Right / N / Step | One real simulation tick |
| R / Reset | Exact initial world, clock, knowledge, queues, proofs and policies |
| + / − / buttons | 0.25×, 0.5×, 1×, 2×, 4× playback |
| 1–4 / agent circles | Select agent; preserve active inspector |
| Tab / Tasks | Cycle current task attempts |
| Task row / destination ring | Open task/decision explanation |
| K / M / P / E / L / X / H | Existing knowledge/messages/priority/eligibility/allocation/execution/recovery inspectors |
| J | Significant mission history; repeated heartbeats/work increments stay hidden |
| V / Summary | Current/final summary; can dismiss to overview |
| Escape | Close scenario menu; otherwise quit |

The 1200×780 layout retains actual geometry/positions, hazards, doors, accepted routes,
destinations, battery, availability, own commitment and phase. Failed/offline markers
turn red. Multiple tasks at one location use concentric destination rings and separate
labels; historical failed-attempt rings are omitted when a successor exists. No
rendering-only movement or preprogrammed paths were added.

Selecting a task shows public requirements plus **the selected agent's** current
known priority, eligibility/reasons, suitability/factors, stored BID/NO_BID, historical
bid-time evaluation, round, local owner belief and authoritative status separately.
Stored priority history and evidence timestamps/confidence explain change; unknown
tasks stay explicitly UNKNOWN to uninformed agents. Completed-task eligibility does
not erase the historical eligible bid that caused its earlier assignment.

At completion the summary distinguishes objectives from failed attempts, pending work,
permanent failure, reassignments and current failed/offline agents. Final owners are
attribution, not active commitments. Failure count is final physical unavailability,
not communication drops or a cumulative incident counter. Tick count is processed
simulation ticks; last-completion tick is reported separately. The summary does not
claim hazard removal. It can appear before the bounded horizon once all configured
work and external events are finished; the clock remains under normal playback control.

For review: launch `--scenario corridor`, show the four corridor positions, press Space,
use L or a task row to inspect real negotiation, watch movement and work, view the
completion summary, then R to reproduce it. S selects emergency/failure/constrained
cases. Use 4× speed if the full horizon is desired. [Scenario menu](task12_menu.png).

## Replay, validation and retained interfaces

Each case finishes within a deterministic bounded horizon, including intentional
incompletion. The runner asserts configured behavior and then compares complete
world/event/message/knowledge/allocation/execution/recovery, priority/suitability,
significant-event histories and metrics after reset. All five print
`deterministic_replay_equal=True`. A fresh engine also reproduces those histories.
Switching has no residual announcements, queue items, procedures, reservation or leases.

Original JSON and Task 10/11 fixtures remain available:

```sh
.venv/bin/python -m coopai.execution_demo
.venv/bin/python -m coopai.recovery_demo
.venv/bin/python -m coopai.gui scenarios/four_room_disaster.json --no-recovery
.venv/bin/python -m coopai.gui --recovery-fixture failure
.venv/bin/python -m coopai.gui scenarios/four_room_disaster.json --no-execution --smoke-test
```

Named missions use their full validated configuration; legacy disable/network flags
remain separate CLI modes. Selecting a new case from an existing session always
creates that case's complete autonomous pipeline.

## Limitations and next milestones

Positions are point agents; collisions, robot radius, SLAM and physical sensing energy
are not modeled. Public graph travel estimates guide suitability, while actual motion
uses center/door waypoints and geometric energy. A route may initially head toward an
area center rather than geometrically straight toward the target. This is the existing
safe navigation baseline, not a movement script or shortest geometric route claim.

Deadlines are soft. Repeated availability has overhead; partitions can delay knowledge,
produce harmless suspicions, or prevent liveness. The simulator's atomic ownership and
lease validator is a centralized physical consistency assumption, not distributed
consensus or Byzantine defense. Evidence confidence remains synthetic. Inspections
report fire/gas/person/clear findings; they do not extinguish hazards or rescue people.

Task 13 addresses adaptive decision refinements, including any justified safe preemption.
Task 14 addresses comparative research experiments. Task 15 addresses final validation
and presentation. None was implemented in Task 12. See [actual verification and changed
files](task_12_verification.md). No automatic commit or push.
