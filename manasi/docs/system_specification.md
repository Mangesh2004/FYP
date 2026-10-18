# System specification — Tasks 1–12

## 1. Scope and architecture

Normative project context: [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). The
system investigates decentralized cooperation, not physical robotics. Task 1
implements domain values and validation. Task 11 implements bounded failure-driven recovery; later milestone sections below
qualify the earlier contract sketches. Task 2 adds headless engine mechanics; see
[engine contracts](simulation_engine.md) for implemented ordering and replay rules. The decision cycle will be mission
configuration → perception → local state → peer communication → local knowledge
update → priority → eligibility → suitability → allocation → execution → monitoring
→ reassessment. No research novelty or performance improvement is claimed.

Module boundaries planned for later milestones:

| Component | Responsibility | Permitted inputs / outputs |
|---|---|---|
| `models` (implemented) | Immutable validated domain values | No world access or side effects |
| scenario loader (Task 3) | Validate IDs, references, initial conditions | Separate public settings and private world initialization |
| simulator (Task 2 mechanics) | Clock, physical truth, event processing | Sensor/execution/transport adapters; never task selection |
| perception (Task 5) | Produce observations from sensor-accessible truth | Observations delivered only to the observing agent |
| local knowledge (Task 5, fusion future) | Each agent's evidence and beliefs | Local observations and delivered peer information |
| communication (Task 6) | Peer delivery, latency, loss, partitions | Envelopes; delivery receipts and logs |
| decisions (Tasks 7–9 policies and allocation baseline) | Replaceable priority, eligibility, suitability, allocation | Agent-local snapshots and public mission settings |
| execution (Task 10), monitoring/reassessment (Task 11 baseline) | Point movement, valid-work progress, evidence-backed outcomes | Own confirmed requests; restricted pose/outcome feedback |
| visualization (Task 4) | Display world snapshots, geometry, clock and applied events; control engine lifecycle | Trusted simulator view; never supplied to agents |
| evaluation (future) | Measure experiment outcomes | Read-only logs, including truth for evaluation only |

A centralized simulation clock is allowed; a centralized task allocator is not.
Future decision modules must not receive the simulator, its private world state,
a global task catalogue, or an aggregate knowledge store. Public topology can be
known initially; hazards and task discoveries cannot silently become public.

## 2. Four-room scenario

The future scenario configuration separates true initial conditions from beliefs:

| Location | Simulator initial condition | Initial qualitative mission importance |
|---|---|---|
| room-1 | Fire hazard | High |
| room-2 | Hazardous gas | Medium |
| room-3 | Possible trapped person | High |
| room-4 | Clear | Low, routine inspection |

Task 3 replaces the preliminary linear topology with four 4 × 4 rooms around a
10 × 2 shared corridor. Each room has a bidirectional one-tick passage to the
corridor and an exterior entry opens into the corridor. Geometry is separate from
world conditions. See [scenario configuration](scenario_loading.md) for exact
coordinates, walls, door openings and schema. All locations are entry-reachable.
Unknown conditions remain unknown to agents until sensed or communicated.

Four configured agents are A (thermal, exploration), B (gas, exploration), C
(visual/thermal, exploration), D (exploration/simulated-assistance). Their spawns
are room-1, room-2, room-3 and corridor respectively. Fire verification requires
thermal sensing and exploration; gas inspection requires gas sensing and exploration;
person search requires visual sensing and exploration; routine inspection requires
exploration. All four tasks start pending/unassigned. There is no firefighting or
physical rescue. Configuration inputs do not select an agent or compute priority.

## 3. Agent identity, capabilities, state and resources

`AgentIdentity` carries a stable opaque ID and human-readable label. IDs are
nonempty strings and labels need not be globally unique. Sensing and operational
capabilities are separate immutable tag sets. Requirements use all-of subset
matching within each category. Empty requirements match all capabilities. Tags
are scenario-defined, case-sensitive, and must use consistent spelling.

`AgentState` contains identity, capabilities, location-based `Position`, battery
fraction in [0,1], resource levels, availability, optional task commitment and
last-update tick. `ResourceLevel` names a consumable and expresses amount and
capacity in scenario-defined units; 0 ≤ amount ≤ capacity, finite values only.
Resource names must be unique per state. Battery is a normalized quantity,
not a physical energy model. Task 10 adds synthetic movement/work consumption. Zero battery is a valid state, not evidence of
eligibility. Available, busy, offline and failed are distinct states; exactly busy
states carry a task commitment. Failure clears the active commitment; historical
assignments are retained in Task 11 objective/attempt records. Communication partitions are modeled by the
future transport and need not imply physical failure or stop active execution.

Frozen dataclasses and immutable collections prevent accidental mutation after
validation. Local invariants are checked now. The Task 3 loader and world snapshot check cross-object
identity uniqueness, location references, event targets and ownership consistency.
The models alone cannot enforce consistency across separate snapshots.

## 4. Tasks and lifecycle

`Task` carries identity, location, requirements, severity and urgency inputs in
[0,1], creation tick, optional deadline, status and owner. No priority score is
computed or stored. `TaskRequirements` carries capabilities, minimum battery
fraction and resource demands. For each required resource, `amount` is the demand;
`capacity` is a validation bound, not a requirement on the agent's capacity.
A future loader should use consistent resource units. Deadlines cannot precede
creation; equal ticks are allowed and mean immediate urgency.

Allowed transitions:

- Pending → assigned or cancelled.
- Assigned → in progress, pending (release), failed or cancelled.
- In progress → completed, failed, cancelled or pending (release).
- Completed, failed and cancelled are terminal.

Assigned, in-progress and completed tasks require an owner. Pending, failed and
cancelled tasks forbid an active owner. A completed task retains the successful
owner for attribution. An ownership change requires release to pending first.
Retries after terminal failure use a new task ID and Task 11 objective/predecessor provenance.
`transition` returns a new immutable value and checks only lifecycle invariants;
it neither selects an agent nor commits distributed ownership. Direct construction
represents a validated snapshot, not proof that a legal history occurred. Initial
execution assumes one agent per task and one active task per agent. Cooperative
multi-agent task execution is an explicitly deferred extension.

## 5. Environment locations and connectivity

`Environment` is static public topology: a nonempty tuple of locations with unique
IDs and directed connections. Each connection has distinct known endpoints and
positive integer travel ticks. Duplicate directed edges are invalid. Reverse
edges must be explicit. Disconnected graphs and isolated locations are valid;
reachability is a Task 8 eligibility input. This model contains no hazard truth.
Dynamic topology updates and positions in transit need future models before
movement is implemented; a location-only position must not imply teleportation. Task 10 adds paired optional finite x/y coordinates,
initialized once from spawn centers and advanced only along validated doorway routes.

## 6. Observations and local knowledge

`Observation` records a unique observation ID, original source agent ID, location,
subject, categorical string value, observed tick, confidence in [0,1] and sensing
tag. Confidence is an evidence-quality estimate, not guaranteed correctness or
a calibrated probability. Both endpoints are valid. Values are categorical strings
for this foundation; quantitative readings may be introduced with explicit units
later. Forwarding preserves original source, ID and observed tick. Envelope sender
identifies the forwarding peer and must not replace observation provenance.

Task 5 agent-local knowledge contains an evidence map keyed by observation ID,
received tick and forwarding provenance, plus latest evidence keyed by (location, subject, modality).
Fused beliefs, reliability weighting and unresolved-conflict policies remain future work. Source reliability is distinct from observation confidence. Fusion,
staleness decay and conflict resolution are future replaceable policies. Unknown
is distinct from clear/absent. Peer repetition must not become independent evidence.
Each agent owns its knowledge store; no globally shared database supplies decisions.
Local snapshots may disagree and must not be repaired using hidden ground truth.

## 7. Peer messages

`MessageEnvelope` carries message ID, sender, one explicit peer recipient, kind,
send tick, optional expiry, correlation ID, schema version (currently 1), and an
immutable JSON-object text payload. Sender and recipient must differ. Expiry must
be later than send time; future delivery at or after expiry drops the message.
Kinds are observation, state, task and coordination. Broadcast will expand to one
envelope per recipient. Validation checks finite JSON syntax and envelope fields;
kind-specific payload schemas, duplicate handling, authentication, delivery and
ordering are deferred. Payloads must contain agent-visible information only;
JSON validity cannot enforce that trust boundary. Future decoders must reject
nonfinite numeric values and validate typed content before agent use. Task 6 adds strict observation/status/coordination decoding and simulated transport;
real networking is not implemented. See [communication](communication.md).

## 8. Mission configuration and events

`MissionConfiguration` contains mission ID, nonnegative integer seed, positive
integer duration, positive finite tick duration in seconds and public objectives.
It does not contain hidden hazards or global assignments. Future scenario files
will additionally contain world initialization, agent definitions, private event
schedules and transport settings. JSON is preferred initially to avoid a YAML
dependency. No loader or running scenario is included in Task 1.

`DynamicEvent` carries identity, scheduled tick, event kind, target ID and descriptive
intent. Kinds cover hazard change, task discovery, agent failure/recovery, resource
change and communication change. These are declarative event descriptors, not
executable commands. Task 3 typed condition/battery effect values define those executable changes;
other event types still require explicit payloads before execution is supported. Private scheduled events are visible
only to the engine; agents learn consequences through observation, feedback,
messages or local timeouts. Loader validation must reject unresolved targets and
out-of-horizon events according to a documented policy.

## 9. Decision interfaces (Tasks 7–9 policies and allocation)

All calls use a read-only `LocalDecisionContext`: own state, locally known task
snapshots, locally known peer states with timestamps, local beliefs, public topology,
public mission settings and current tick. No global peer truth is included. Results
must include explanatory reasons and evidence references for experimental analysis.
Task 7 implements a weighted priority contract with explicit task announcements and
agent-local PriorityContext. Task 8 implements hard eligibility and agent-local suitability. Task 9 implements
bounded agent-local allocation with explicit protocol-backed ownership admission. See [task priority](task_priority.md) and
[eligibility/suitability](eligibility_suitability.md).

| Interface | Inputs | Output and invariant |
|---|---|---|
| `priority.evaluate(task, context)` | Locally known task, beliefs, mission and time | Finite comparable score plus explanation; normalization/policy decided later |
| `eligibility.check(agent_snapshot, task, context)` | Known capabilities, availability, resources, topology, freshness | Boolean plus explicit failed/unknown constraints; unknown hard constraints fail conservatively |
| `suitability.evaluate(eligible_agent, task, context)` | Eligible local snapshot and known cost/context | Finite score plus factors; reject ineligible candidates, no assignment |
| `allocation.on_update(context, delivered_messages)` | Local scores, tasks and received coordination evidence | Outgoing peer envelopes and provisional local claims/releases; no global winner selection |

Capability matching in Task 1 is a set primitive, not complete eligibility.
Task 8 eligibility includes battery, resource demand, reachability, commitments,
availability and optional fresh local-evidence restrictions. Suitability must not relax hard requirements. Task
priority is distinct from agent-task suitability. Allocation protocols must specify
claim IDs, versions, acknowledgements, lease/timeout semantics and deterministic
tie breaking. Partitions can create conflicting local claims; instantaneous global
agreement is not assumed. The simulator records conflicts but cannot resolve them
by secretly choosing a winner. Execution adapters can reject invalid physical
requests and report outcomes; they cannot allocate tasks. Task 9 selects a Contract Net-inspired baseline; other comparison strategies
remain future research work.

## 10. Execution, monitoring and reassessment interfaces

Future `execution.request(agent_id, committed_task_id, action, tick)` validates
physical feasibility against simulator truth and returns agent-visible acceptance
or rejection without disclosing unrelated truth. `execution.advance(tick)` updates
abstract movement, resources and progress. `monitor.observe(local_feedback,
context)` returns continuation/reassessment reasons. Output includes progress,
completion, failure, battery and resource changes accessible to the involved agent.
Physical execution success is separate from distributed assignment belief.

Reassessment triggers include new evidence/task discovery, changed mission inputs,
resource depletion, missed deadlines, execution failure, stale ownership leases,
peer silence, recovery and communication restoration. Each agent evaluates only
locally observable triggers. Future stability policies specify switching costs,
hysteresis and cooldowns to avoid unnecessary reassignment. Releasing a claim,
reopening a task and retrying after failure are distinct operations. Task 11 implements the bounded monitoring/retry subset using own feedback, peer
heartbeats, validated release and fresh attempts; priority-driven preemption remains
deferred. See the Task 11 contracts below.

## 11. Clock, ordering and reproducibility

All model timestamps are nonnegative integer ticks, never wall-clock timestamps.
Tick length is configured separately. Future engine runs ticks 0 through
`duration_ticks - 1`; initialization occurs before tick 0. At each tick:

1. Apply scheduled world events.
2. Advance existing physical execution and produce feedback.
3. Produce local sensor observations.
4. Deliver messages due at this tick.
5. Update local state and knowledge.
6. Run each agent's decision callbacks in stable agent-ID order.
7. Enqueue outgoing messages and submit next execution requests.
8. Record metrics and snapshots.

Execution requests begin progressing on the next tick. Messages require at least
one tick latency initially to avoid recursive same-tick decisions. Events within
a phase use stable event IDs; delivery uses (delivery tick, message ID). Inputs
must have unique IDs, independent of hash/set iteration order. Randomness uses
explicit seeded generators, ideally separate sensor, communication and event
streams. Save config, seeds, interpreter/package versions and code revision.
Same code/config/runtime should reproduce the event trace. Cross-version or
cross-platform bitwise numerical identity is not promised. Wall time measures
computational cost only and cannot affect decisions.

## 12. Metrics and evaluation

Future metrics consume truth/logs in an evaluation-only component. Decisions never
read evaluation outputs. Define mission completion time as last required completion
tick minus start, censored at horizon if incomplete; completion rate as completed
required tasks / all required tasks revealed by horizon (report zero-task runs as
not applicable). Allocation latency runs from discovery by an agent to committed
ownership. Reallocation latency runs from locally detected invalidation to new
commitment; recovery time runs from injected failure to restored task progress.
Also report sent/delivered/dropped messages and bytes, resource consumption relative
to initial budgets, releases/switches without invalidation, unassigned known task
counts and overlapping ownership intervals. Report agent knowledge delay separately
from simulator event time; do not conflate failure injection and detection latency.

Compare future static, dynamic utility and distributed auction baselines under the
same scenarios, budgets, seeds and event/communication conditions. Keep tuning runs
separate from evaluation runs, use multiple seeds, report dispersion and unfinished
missions, and expose weaknesses. Vary latency/loss, failures, heterogeneity and
observation uncertainty. Task 11 records measured recovery timelines and communication/round overhead for
controlled fixtures. Comparative baselines/statistical evaluation remain future work.

## 13. Assumptions, limitations and next milestone

Core models are scenario-independent and allow opaque capability/resource tags.
Single-owner tasks, public static topology, location-based positions, categorical
observations, benign agents and abstract battery/resources are initial assumptions.
Runtime validation covers local structure; scenario loading and world snapshots validate
references and cross-object consistency. Future transport must validate received data. Frozen objects do not enforce information
security: separation must be tested at future adapter boundaries. No advanced knowledge fusion, general irreversible preemption or hardware is implemented.
Task 11 implements bounded inspection-objective recovery and new-task propagation.
Task 10 implements bounded abstract motion and inspection execution.
Task 9 implements bounded decentralized allocation.
Task 8 implements bounded eligibility and suitability policies. Task 6 adds simulated peer transport. Task 4 adds visualization
through read-only snapshots and existing engine lifecycle controls; see
[visualization contracts](visualization.md).

Task 2 provides a deterministic engine with an explicit world/agent interface
boundary, phased step hooks, event scheduling and seeded reproducibility tests.
Event descriptors are paired with trusted handlers, not parsed descriptions.
Task 3 adds JSON scenario loading and validated condition/battery effect handlers.
Other event effects remain future work. Agent controller
hooks are available, but no decision algorithm is supplied. The
roadmap numbers in context describe finer work items; this milestone bundles only
specification and foundation work, not all of Phase 1.

Task 5 local sensing and knowledge contracts, lifecycle and limits are documented in
[perception and knowledge](perception_and_knowledge.md). Controller views contain only
their own immutable evidence snapshot; Task 6 adds explicit peer messages with preserved provenance.

Task 6 communication, policy and receipt semantics are documented in
[communication](communication.md). Agents receive only their own message receipts
and delivered peer statuses; communication diagnostics remain privileged.

Task 7 provides explicit versioned mission announcements, a bounded weighted
priority baseline and per-agent result histories. The evaluator has no world or
simulator reference; calculation follows local/peer evidence recording. See
[task priority](task_priority.md) for its formula, quality/conflict policy and limits.


Task 8 implements separate replaceable hard feasibility and weighted self-suitability
policies on owner-only contexts and explicitly public topology. Directed shortest-path
travel is a cost estimate only. Own battery/resources, lifecycle, commitments,
availability, exact capabilities and optional locally evidenced prohibitions gate
eligibility. Eligible scores use reserve, travel and relevant information quality;
resource reserve is included only when demanded. Priority is not a suitability factor.
Each agent independently reevaluates after knowledge/message updates; controller
views carry only that agent's results. E inspection and all-agent demo outputs are
privileged diagnostics, never a global ranking service. See
[eligibility/suitability contracts](eligibility_suitability.md) for formulas, defaults,
continuation and reset semantics. Task 9 implements allocation below; Task 10 implements execution afterward.


Task 9 adds independent `AgentAllocator.step(own_view, peer_port)` policies, strict
`allocation.v1` coordination messages and a separate `CommitRegistry` admission
adapter. Public task-ID hashing and epoch rotation choose temporary coordinators;
only received bids are compared by those agents. Pending/provisional, protocol
accepted and authoritative assigned ownership are distinct. One bounded reservation
per agent enforces capacity; eligibility is rechecked before commit. The simulator
accepts already-selected outcomes with exact message lineage/delivery evidence and
atomic pending/free-state guards, without ranking private scores or selecting
replacement candidates. This authoritative admission assumption provides strict
simulator single-owner consistency, not partition-tolerant distributed consensus.
Future execution requests may use confirmed commitments; Task 9 never moves agents,
executes tasks or dynamically reassigns confirmed work. See
[protocol and limitations](decentralized_allocation.md).


Task 10 adds independent own-assignment `AgentExecutor` controllers and a separate
trusted `ExecutionAdapter`. Commands issued at t apply physically at t+1 before
perception; new locations affect current-tick sensing. Optional Position coordinates
and public graph/door/center waypoints model point kinematics without wall shortcuts.
Movement persists only along an accepted own route; begin/work/complete/fail are
validated against ownership, arrival, requirements, funded energy and actual direct
evidence. Timer duration alone cannot complete work. Task failure releases active
ownership/capacity but stays terminal; completion retains owner attribution and
never clears hazards. Required resources are consumed once, with explicit same-task
consumed quota in local contexts. Execution notices use real P2P delivery and cannot
backfill unknown tasks or overwrite newer requirements. Default deadlines remain
priority-pressure inputs, not hard expiry. See [movement and execution](movement_execution.md)
for state transitions, synthetic costs, actual timing, replay and future adapter limits.
Task 11 adds bounded monitoring/reassignment below. Full preemption, ROS2/Gazebo
and physical robot navigation remain follow-up work.


## 14. Task 11 recovery contracts

Stable objectives contain successive immutable Task attempts. Initial IDs are retained;
retries use objective-id::attempt:N and explicit predecessor provenance. Terminal
FAILED/COMPLETED/CANCELLED Task transitions are unchanged. Only explicitly recoverable
objectives within a configured attempt budget receive fresh pending attempts. Work
restarts at zero; consumables are not refunded or credited across attempts. Completed
objectives never reopen. Records distinguish physical release, local detection and
new coordination, preserving previous owners, progress, timing and reasons.

AgentRecovery uses own state/feedback, received typed recovery.v1 heartbeats and
availability messages, locally known tasks and public configuration. It has no world
or global failure access. Offline agents cannot send; peers use bounded local silence
windows. Authoritative validated owner renewals prevent a healthy partitioned owner
from being revoked merely by peer silence. RecoveryRegistry admits only matching
current-attempt release/expiry/failure requests and returns successor availability
to the requester. It neither chooses candidates nor ranks suitability. Existing
per-agent priority/eligibility/suitability and P2P Contract Net rounds select owners;
existing physical execution validates fresh-proof movement, work and completion.

New runtime objectives are registered by a trusted discovery callback and disclosed
to one recipient. Typed announcements carry validated Task requirements and procedure,
not hidden hazard truth. Repetition/forwarding never overwrites newer task state. Old
round IDs, proof references and commands cannot restore terminal earlier attempts.
The strict simulator admission guard is still centralized state validation, not
partition-tolerant distributed consensus or Byzantine authentication. If eligibility/
communication prevents recovery, the objective stays explicitly waiting/unassigned.

Evaluation-only metrics join actual logs; none are decision inputs. Reset clears
runtime retry/lease/actor state and dynamic procedures and reproduces all histories.
H/Recovery presents privileged objective/attempt diagnostics, leaving R/reset intact.
Full priority preemption is deferred; operator release of reversible inspection work
is supported with restart semantics. See [recovery specification and measured fixtures](dynamic_reassessment_reallocation.md) for defaults, timing, guarantees and limits.


## 15. Task 12 scenario and presentation contracts

An immutable scenario registry loads five full JSON missions through the existing
domain/scenario loader plus validated presentation/network metadata. Expected behavior
counts/recovery/movement assertions are post-run checks only. No agent receives the
scenario registry, private event schedule, expected outcomes or report aggregate.
Optional explicit coordinates and typed power/discovery/transport events are validated
against public geometry/identities/procedures; malformed resources and unknown review
capability tags are rejected. An explicit allow_infeasible_objectives flag permits
intentional incomplete work while retaining all structural/geometry/resource checks.

The existing algorithms remain unchanged. Initial objectives are explicitly announced;
new tasks reach one configured recipient then spread through actual messages. A
communication outage does not change physical availability. New power-failure events
use existing physical stop/release semantics, including just-admitted commitments.
Local lease requests can be rejected for healthy/completed owners. The same atomic
simulation validation assumption remains, without a distributed consensus claim.

A scenario switch replaces the complete engine/session, not just world sprites. Pure
reporting joins actual records into objective/attempt outcomes, stored own-agent
explanations and filtered events. Simulator truth and agent knowledge remain explicitly
separate. Summary completion requires actual completed configured objectives and
processed external events; rendering never advances or fabricates execution. Reset
reproduces full histories. Detailed contracts/outcomes/limitations are in
[Task 12 scenarios](task_12_scenarios.md). No preemption, new optimization, benchmarks,
ML, Gazebo or ROS2 is part of this milestone.
