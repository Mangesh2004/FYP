# Autonomous movement and abstract execution — Task 10

Task 10 adds owner-local execution commands and a separate deterministic physical
adapter. It does not change which agent wins allocation. The default four-room run
now completes its four inspections within the existing 16-tick horizon, with real
movement by D. A, B and C already spawn at their destinations and are not moved
unnecessarily. Fire/gas/person conditions remain in the environment after completion.
No extinguishing or rescue is simulated.

## Modules and boundaries

| Component | Responsibility | Inputs |
|---|---|---|
| `navigation.py` | Public graph route → center/door waypoints; validate geometry | Public FloorPlan/topology and own coordinates |
| `execution_models.py` | Frozen procedure/configuration/state/outcome/feedback/notice values | No world access |
| `execution.py` | Independent `AgentExecutor` confirms its own mission, plans and issues commands | Own AgentView, public plan/procedures, own peer send port |
| `execution_adapter.py` | Validate one applicant's commands, move, charge energy, validate reports and lifecycle | Trusted physical world plus only applicant's relevant evidence |
| Existing engine | Clock/phases, command queue, own feedback, reset | No winner selection or global execution planning |
| GUI | Actual poses/routes/progress/outcomes | Privileged diagnostic snapshots; no decision effects |

A controller receives only its own `execution` snapshot and `execution_feedback`.
These contain own accepted route, progress, consumed-resource quota and outcomes,
not other agents' motion plans or hidden conditions. Coordinates are own-state
information. Other task status is learned by explicit typed P2P notices, not a
truth registry backfill. Unknown world tasks remain unknown/unexecuted. The adapter
may inspect authoritative ownership and geometry to reject invalid physical requests;
it never creates an assignment, selects another agent or plans a route for them.

## Position, path and doorway model

`Position(location_id, x=None, y=None)` remains compatible with earlier models.
Coordinates must be supplied together and finite (negative map origins are valid).
Execution setup assigns location-only spawns their configured area's center once,
before sealing the replay baseline. Explicit coordinates are preserved/validated.
This is initialization, not runtime teleportation. Non-execution engines retain their
original location-only positions and marker offsets.

Task 8's `shortest_path`/`travel_ticks` now share directed Dijkstra. Positive graph
weights choose the lowest-cost location path; equal costs use lexicographic full
location sequences, independent of edge/hash order. Unknown endpoints fail and
unreachable routes return None. The existing FloorPlan supplies actual doorway
openings, rather than an invented Euclidean room-to-room connection.

Routes pass through area centers and doorway midpoints. Each transition includes
**two identical coordinate waypoints**, first labeled with the source area and then
with the target area. That zero-distance label change is permitted only inside a
real connecting doorway with a valid directed graph edge. Other location jumps fail.
Room/corridor interiors are convex rectangles; straight segments inside them are
valid. The validator also rejects outside/wall endpoints, solid-wall following
between openings, wrong starts/goals and excessive routes (>256 waypoints).
Accepted remaining routes are revalidated against current topology each motion tick.
No advanced obstacle avoidance, robot radius, dynamic collision physics or inter-agent
collision avoidance is modeled; agents are abstract points and can overlap.

A already-at-center route has one waypoint, no motion cost and an explicit arrival
event. A start inside a room but away from center travels via the center to the door;
this is a safe abstract route, not a shortest geometric path. Actual target position
is the task location's center, with configurable arrival tolerance (default 0.05 map
units). Display rings mark those actual destinations when execution is enabled.

## Clock, commands and state machine

The existing `AgentCommand` adds immutable finite JSON-object `payload`, default
`{}` for backward compatibility. Controllers emit at most one execution command per
agent/tick. Intents issued at tick t are consumed in EXECUTION at t+1; events apply
first. The physical adapter advances before other registered EXECUTION hooks;
perception follows motion, then delivered messages/local knowledge, decisions and
submission. Multiple commands in one tick fail safely instead of accelerating work.

| State | Own decision / validated physical transition |
|---|---|
| IDLE | No active mission/accepted route; confirmed own allocation feedback required |
| ASSIGNED → PLANNING | Agent accepts its own confirmed task and plans on public geometry |
| MOVING | Accepted `navigate` command advances along the agent's route each tick |
| ARRIVED | Final center/tolerance reached, or initially already at destination |
| EXECUTING | Valid `begin`; task ASSIGNED→IN_PROGRESS; progress initially zero |
| EXECUTING | Explicit `work` with valid fresh evidence adds exactly one work tick |
| COMPLETED | Explicit `complete` after required work duration and report validation |
| FAILED | Invalid/unsafe owned action, resource/operational failure or invalidated task |

ASSIGNED/PLANNING are the agent's own decision states; physical snapshots remain IDLE
until the requested route is accepted. Navigation has no side effect before that
acceptance. There is no simulator-side function choosing an agent's active mission.
The public procedure list is explicitly supplied at setup; it is not obtained by
querying hidden world tasks. Task metadata alone never announces an unknown task.

Actions: `navigate` carries task fingerprint and coordinate waypoints; `begin`
requires arrival; `work`/`complete` carry own observation IDs; `fail` carries a
meaningful stop reason. Ownership must match the task/agent's one active commitment
and an accepted Task 9 commit record. Forged/unowned commands are rejected without
exposing unrelated task state. Accepted motion is persistent, so an agent need not
resubmit the same route every tick, but work/completion remain explicit decisions.

Movement uses a speed budget per tick across successive waypoint segments. It can
turn and cross a door in one tick, but total traveled path length cannot exceed
that budget. Zero-distance transitions consume no energy. A paused clock performs
no motion/work; single-step advances one tick; rendering never advances physics.
No rendering interpolation was added: every marker uses the actual deterministic
engine pose. Normal step displacement is small enough to show the accepted route
clearly. Very high configured speed can traverse multiple segments within a tick;
perception still samples the final location once per tick.

## Synthetic energy and resource model

Defaults in `ExecutionConfiguration`:

- speed: **1.5 map units per simulation tick**;
- movement cost: **0.01 battery fraction per traveled map unit**;
- valid work cost: **0.01 per work tick**;
- minimum operational battery: **0.05**;
- arrival tolerance: **0.05 units**;
- evidence maximum age: **2 ticks**;
- coarse inspection confidence: **0.60**;
- status-notification interval: **3 ticks**.

Time-step seconds control simulated/wall-time playback; configured movement speed
is explicitly per tick. This model is synthetic and uncalibrated. The adapter
retains Task 8's configured minimum battery plus task minimum: required remaining
battery is the maximum of those and the execution operating minimum. Default task
minimums are 0.1, so active work normally must leave at least 0.1. Decimal arithmetic
over numeric strings charges energy without negative battery. If a whole next motion
step/work tick cannot be funded, it fails before moving/charging that step. There
is no partial unfunded motion, idle drain, turning energy or hardware physics.

Travel suitability remains the earlier graph-tick approximation; actual motion uses
geometric distance. It is not a guaranteed remaining-energy budget for the complete
mission. Own feasibility is rechecked each tick, so a myopic eligible bid can later
fail physical execution. No hidden global optimizer repairs that weakness.

Required consumable amounts are checked and consumed **once at begin**. Capacity
is unchanged, and consumed resources are not refunded on failure. Own snapshots
record `consumed_resources`; Task 8 hard eligibility credits this explicit quota
only for continuing the same owned active task. It cannot qualify a pending/new
or competing task. Suitability reserve remains actual remaining stock. This avoids
requiring a task to consume its entire resource demand again every work tick.
Default tasks declare no consumable demands, so support-kit stock remains unchanged;
a separate kit-demand fixture verifies exact-once use and successful continuation.

## Procedures, observations and completion

Existing TaskDetails durations are reused (fire 2, gas 2, person 3, routine 2 valid
work ticks). Changing metadata changes actual progress duration; no new scenario
JSON fields/dependencies are needed. Public `TaskExecutionSpec` declares subject and
method. Known unsupported procedures fail explicitly rather than auto-completing.

| Task type | Required report method | Report subject |
|---|---|---|
| fire-verification | existing thermal sensor | fire |
| gas-inspection | existing gas sensor | gas |
| person-search | existing visual sensor | person |
| routine-inspection | existing exploration operation, coarse local inspection | hazard |

Specialized sensors retain Task 5's local-location/capability/confidence rules.
After movement updates `position.location_id`, normal sensing observes that actual
location only. Peer sharing continues. For routine inspection, the active exploration
procedure produces a coarse, explicitly named **`operational:exploration`** observation
only while the owner is arrived and IN_PROGRESS. This is an operation-derived
inspection report, not a newly invented thermal/gas/visual capability; D's sensing
set stays empty. It observes the configured local generic hazard subject only and
has synthetic confidence 0.60. Missing local conditions remain unknown and cannot
produce a successful report. No inspection is produced before arriving/beginning.

The controller chooses current own direct observations. Peer reports do not stand
in for personally executing a task. The adapter checks source, location, procedure
method, positive confidence, timestamps, exact stored IDs, and agreement with the
current local condition. Repetition cannot create evidence or progress. An outdated
report after a condition change fails, requiring later reassessment rather than
silently asserting success. Outcome findings retain source, observed tick, modality
and confidence. Confidence is not calibrated certainty.

`begin` establishes IN_PROGRESS at progress 0. Only funded, valid `work` commands
increment progress. Reaching duration is insufficient by itself: without explicit
`complete` and valid evidence, the task remains IN_PROGRESS. Completion atomically
marks COMPLETED (retaining owner attribution), clears the agent's active commitment
and returns it to AVAILABLE. Task conditions are not cleared, extinguished or rescued.
Deadlines retain their existing **priority-pressure** meaning, not a newly introduced
hard expiry. Fire/person complete after their desired deadline 8 in the default run;
this is reported, not presented as timely completion.

## Failure, ownership release and peer updates

Basic failure reasons cover unavailable route, invalid doorway/wall command,
unowned mission, premature execution/completion, missing/stale/unsupported evidence,
changed task requirements/location, agent disablement and insufficient energy/stock.
An unsafe owned attempt stops the route, records a failure outcome and transitions
an active owned task to FAILED. Ownership and BUSY commitment clear atomically.
Below operational battery the agent becomes OFFLINE; an externally FAILED agent
remains FAILED. Otherwise the agent can remain AVAILABLE even though that particular
task is infeasible. Externally CANCELLED/FAILED tasks are not illegally transitioned
again; the agent records that its execution stopped. Foreign commands are rejected
without failing somebody else's task or exposing its truth.

This is **execution failure plus capacity release**, not reopening/reassignment.
FAILED remains terminal under the existing lifecycle. Task 11 can consume the clean
own failure/outcome/event signals and explicitly decide retries/releases/new tasks;
no recovery or alternative owner is chosen here. Historical Task 9 round ownership
is not erased or silently changed, and unconfirmed reservations still expire.

Strict typed `execution.v1` coordination notices carry actor/task status, monotone
sequence, task-definition fingerprint and original admitted commit reference. Own
feedback updates that agent's knowledge; peers learn only by message delivery.
Operational agents repeat status notices to tolerate a lost notification. Disabled/
OFFLINE agents do not emit them; their local physical failure still exists for
future monitoring. A stale notice cannot overwrite newer requirements or reopen a
terminal task. Task 9 stops repeating obsolete assigned-owner closures for locally
terminal tasks. All learned results remain owner-local; messages never synchronize
a shared truth catalogue. The final D completion at tick 15 may still be in transit
when the 16-tick default horizon ends; longer runs deliver it normally.

## Actual demonstration and replay

```sh
.venv/bin/python -m coopai.execution_demo
.venv/bin/python -m coopai.gui
.venv/bin/python -m coopai.gui --no-execution   # Task 9 behavior
.venv/bin/python -m pytest -q
```

The unedited scenario produces:

| Task / owner | Assignment | Movement start | Arrival | Execution start | Completion |
|---|---:|---:|---:|---:|---:|
| Fire / A | 6 | — | 8 | 9 | 12 |
| Gas / B | 3 | — | 5 | 6 | 9 |
| Person / C | 6 | — | 8 | 9 | 13 |
| Routine / D | 6 | 8 | 11 | 12 | 15 |

A/B/C are already at destination; arrival means their requested mission validates
that position, not a forced trip. D's actual route is `(corridor,5,5) →
(corridor,7.5,6) → (room-4,7.5,6) → (room-4,8,8)`. Distance is about 4.754135
units, requiring four movement ticks at speed 1.5. Its battery ends **0.932459**;
A ends **0.98**, B **0.38**, C **0.92**. B's existing tick-5 reduction to 0.4 leaves
sufficient reserve, so its gas inspection succeeds. These are computed outputs.

The separate controlled event reduces D to 0.03 at tick 9 during movement. D stops
at its previous pose, becomes OFFLINE, task-4 becomes FAILED/unowned, and no further
motion/reassignment occurs. Other tasks complete normally. Both normal and fault
runs compare full world/event/message/allocation/execution/pose/evidence signatures
after reset and print `execution_replay_equal=True` / `failure_replay_equal=True`.
Initial positions, clock, settings, route/progress/energy state, commands, outcomes,
notices and actor counters all reset. Hidden external mutable callback state is
still the callback owner's responsibility, as in earlier engine contracts.

## GUI and future adapters

The default GUI enables execution with allocation. **X / Execute** displays phase,
actual coordinates/location, accepted waypoints, destination, valid-work progress,
report evidence, battery and recent movement/execution events. Accepted paths and
real destination rings are drawn on the map; completed rings turn teal, failures
red. Hazard backgrounds remain unchanged. P/E/L/M/K, agent-circle/1–4 selection,
play/pause/step/reset and speed controls remain available. Selection retains the
active inspector. Rendering does not invent motion or change simulation state.

`--no-execution` retains allocation-only behavior; `--no-allocation` and
`--no-communication` also disable execution. Native smoke screenshots show
[movement](task10_preview.movement.png) and [completion](task10_preview.execution.png).

Future Gazebo integration can replace the trusted physical/inspection adapter while
retaining clock, own pose/feedback, command/waypoint and outcome contracts. No ROS2,
Gazebo, real networking, SLAM, ML, robot collisions or hardware control is implemented.
Static rectangular maps, center goals, point agents, coarse inspection, soft deadlines,
synthetic energy, per-tick sensing and benign message reporters remain limitations.
Future Task 11 can investigate reassessment/reallocation, remaining-energy estimates,
execution observation retries and appropriate deadline/recovery policies after review.


Task 11 now adds opt-in recovery without changing these physical Task transitions.
FAILED attempts stay terminal; fresh objective-linked attempts restart with new IDs,
zero progress and no consumable refunds. Use with_execution=True alone or GUI
--no-recovery to reproduce Task 10 terminal-failure behavior. Default GUI recovery
and runtime task procedures/metadata are described in
[reassessment and recovery](dynamic_reassessment_reallocation.md).


Task 12 adds configured deployments through the same navigation/inspection controllers.
All four agents move in the primary corridor mission; distributed deployments can
start some agents at their valid destinations. No movement sequence is prescribed.
Typed power outages use the physical failure/release path even immediately after
admission, while connection changes leave physical availability alone. See
[mission cases and actual timings](task_12_scenarios.md).
