# Deterministic simulation engine — Task 2

## Mechanics and authority

`SimulationEngine` in `coopai.simulation` holds a `MissionConfiguration`, immutable
`WorldSnapshot`, event schedule, clock and named seeded random streams. It imports
no renderer, decision algorithm, hardware driver or wall-clock timing utility.
`WorldSnapshot` in `coopai.world` separates true conditions from public topology.
It checks unique entity IDs, known locations, condition keys and mutually consistent
active task/agent commitments. It allows completed tasks to retain historical owners.

Trusted scenario setup uses `register_agent`, `update_agent`, or `replace_world`.
Tasks, conditions and coupled changes are registered/updated by replacing a complete
validated snapshot. Invalid replacements leave the previous world intact. Agent
updates preserve identity and reject regressing/future timestamps. Full snapshot
replacement is an authoritative infrastructure operation, not a lifecycle policy;
trusted callers remain responsible for lawful history. Registered controller agents
cannot be removed. Setup and trusted step callbacks can edit the world; external
edits during running/paused/finished states are rejected. Later interactive inputs
should enter through a documented queued-input adapter rather than mutate truth.

No method selects an owner or checks task suitability. Explicit ownership in world
snapshots is an input, never an engine decision. Distributed claims/conflicts remain
agent-local coordination information in later milestones; a single world task owner
will describe physical execution commitment, not globally perfect belief agreement.

## Time, lifecycle and ordering

`time.tick` is the next tick to process outside callbacks and the current tick inside
callbacks. `time.seconds = tick * tick_seconds`. The mission processes ticks
0 through duration_ticks - 1. Completion leaves tick equal to duration_ticks.
No wall-clock time enters simulation decisions.

Lifecycle: ready → start → running; running → pause → paused → resume → running;
processing the horizon → finished. `step` processes exactly one tick and requires
running. `run(max_steps)` auto-starts ready engines and stops at its bound or the
horizon; it never auto-resumes paused engines. `run(0)` does nothing. A positive
run on a finished engine returns zero. Invalid lifecycle operations raise
`RuntimeError`; invalid data/schedules raise `ValueError`.

Each tick first executes due event handlers ordered lexicographically by event ID.
Then systems run in this order:

| Phase | Intended responsibility |
|---|---|
| EXECUTION | Advance prior physical actions and consume prior-tick commands |
| PERCEPTION | Future sensor adapter produces local evidence |
| DELIVERY | Future message adapter delivers due data |
| LOCAL_UPDATE | Future local state/knowledge update adapter |
| DECISION | Restricted agent controllers only, ordered by agent ID |
| SUBMISSION | Future transport/action submission adapter |
| RECORD | Trusted diagnostic/evaluation/renderer snapshot collection |

Trusted systems within a phase run in lexicographic system-ID order, independent
of registration order. The implementation contains hooks, not implementations of
movement, sensing, communication, fusion or metrics. DECISION cannot register a
world-access system; controllers use a separate restricted interface. The clock
advances only after all phases succeed. Step/reset/start/pause cannot be called
recursively from callbacks.

`schedule(DynamicEvent, handler)` pairs the existing immutable descriptor with an
explicit trusted callable defining its effect. Descriptions are never parsed as
commands. The engine does not infer effects from event kinds. Handlers validate
kind-specific target references and semantics; generic scheduling validates time,
unique ID and callable only. Task 3 supplies JSON loading for condition and battery event effects; see
[scenario loading](scenario_loading.md). The engine itself remains schema-independent. IDs cannot be
reused during a run, even after consumption. Initial events can target tick 0;
events added by callbacks must target a strictly later tick below the horizon.
This prevents infinite same-tick scheduling. Events outside the remaining horizon
are rejected. Current implementation scans a small schedule each tick, appropriate
for this scenario; no general-purpose event framework is needed.

## Agent/adaptor boundary

`coopai.interfaces` is independent of the engine and defines:

- `SimulationTime`: immutable tick and tick duration.
- `AgentView`: time, the recipient's own immutable AgentState and explicitly
  delivered observations only.
- `AgentCommand`: immutable ID, actor ID, issue tick, action tag and optional target.
- `AgentController`: callable taking AgentView and returning a tuple of commands.

An agent callback receives no engine, world, conditions, global task catalogue,
peer state or RNG. It can keep its own local memory later. A trusted adapter calls
`deliver_observation(recipient, observation)` inside a step. Delivery validates
references and nonfuture timestamps; it does not manufacture observations or
validate a sensor's physical access. The adapter must filter access correctly.
Delivered evidence is retained until the recipient's next successful callback,
then removed. Other agents do not see that inbox. A future perception adapter must
check capability/range and uncertainty before delivery; it must not expose truth
through observations simply because a reference is valid.

Controller commands must match the recipient agent and current tick and have unique
IDs for the run. They are intents only. `take_commands()` inside trusted step
callbacks returns and removes only prior-tick commands, so a decision cannot cause
same-tick execution. Conventionally the EXECUTION adapter drains them. No action
is executed in Task 2, no target eligibility is checked, and no allocation is
inferred from action names. Commands issued on the final tick remain unexecuted
at the horizon. No command feedback or command rejection policy is implemented yet.

These small value contracts allow a future Gazebo adapter to publish observations
and consume commands without changing a controller signature. An adapter must map
its clock to SimulationTime and define asynchronous event/feedback semantics.
Gazebo integration and guarantees of external simulator determinism are deferred.
The review target remains Python with early 2D visualization after the environment
milestone. The engine's trusted read-only snapshots can feed that renderer.

This is an API/data-flow boundary, not isolation against malicious Python. A caller
that captures the engine in a closure or accesses private attributes can bypass it.
Trusted simulation systems have authority by design; future decision implementations
must depend on `interfaces`, not world/simulation modules. Tests verify ordinary
agent callbacks cannot obtain truth from the supplied view.

## Randomness, reset and replay

`random_stream(name)` is available inside trusted step callbacks only. Its seed is
SHA-256 of the mission seed and stream name, avoiding process-randomized Python
hashes. Named streams are independent; draws in transport cannot perturb sensor
draws. Callbacks must retrieve streams on each invocation and not retain RNG objects
across reset. Use stable iteration/order, avoid global randomness and wall time.
No cross-interpreter-version bitwise randomness guarantee is made.

The first `start` seals the initial world and event schedule. `reset` restores that
baseline, clears trace/inboxes/commands/used command IDs, resets clock to zero,
clears RNGs and returns ready. Runtime-generated events are removed and are
regenerated by the same callbacks during replay. Systems/controllers remain
registered. After reset, setup may be deliberately edited; the next start seals
the new baseline. Reset before first start preserves setup world/events but clears
runtime buffers. No observation delivery or RNG consumption occurs during setup.

Replay requires deterministic, stateless trusted callbacks or callback-owned state
explicitly reset by its owner. The engine cannot rewind external closure variables,
files, UI state or arbitrary controller memory. Agent-local memory and its reset
lifecycle belong to their later milestone. `event_trace` records successfully
applied event descriptors in order for diagnostic replay comparison; it is not
research metrics or a full serialized checkpoint.

If a callback raises, the exception propagates and state becomes FAILED. The clock
does not advance, but earlier callbacks may already have applied valid world changes
or produced side effects. There is no automatic transaction rollback or retry.
Reset restores the initial world/schedule and clears internal buffers. This explicit
fail-stop behavior avoids executing the same partially applied tick twice.

## Demo and verification

```sh
.venv/bin/python -m coopai.demo
.venv/bin/python -m pytest
```

The demo creates four-room truth, advances half-second ticks, pauses/resumes,
applies scripted fire/gas changes at tick 2 and replays from reset with matching
seeded diagnostic draws. Changes are external scripted events, not agent hazard
resolution. Its output is a simulator diagnostic view, not agent knowledge.

Task 3 now provides structured four-room scenario loading and condition/battery
event definitions. Task 4 now supplies the 2D view against snapshots and
controls; see [visualization](visualization.md). Real decentralized logic remains a subsequent bounded milestone.
No Gazebo, movement or allocation has been implemented. Task 5 perception is
described below.


## Task 5 lifecycle extension

Registered LocalKnowledge stores are cleared automatically by reset, including
histories/latest evidence and pending local updates. This lifecycle applies only to
these registered stores; arbitrary callback closures still require owner resets.
LOCAL_UPDATE records delivered local observations before decisions; AgentView now
includes a default-empty immutable `knowledge` tuple for its recipient only. Generic
engines remain perception-free unless explicitly configured. See
[perception and knowledge](perception_and_knowledge.md) for the adapter boundary.


## Task 6 transport hooks

DELIVERY routes due peer messages before trusted phase callbacks. LOCAL_UPDATE records
local samples then processes recipient endpoint queues; DECISION runs separate
per-agent sharing policies with tick-bound ports before ordinary command callbacks.
SUBMISSION drains/schedules outgoing messages after trusted callbacks, with at least
one tick latency. AgentView adds own receipts/delivered peer statuses, never global
transport/world access. Reset clears transport/endpoint caches and calls each
registered policy's reset; agent knowledge is cleared as before. Communication
registration fixes the endpoint roster. See [communication](communication.md).


## Task 7 priority hooks

Priority state is separately registered per agent. Initial task announcements are
explicit mission inputs sealed at start; runtime world tasks are never implicitly
published. Start computes unknown-evidence baselines; DECISION updates cached local
priorities before sharing/command callbacks using only own announced tasks and own
recorded evidence history. AgentView adds default-empty known_tasks, evidence_history
and priorities tuples. Reset restores initial announcements/settings and clears
results/history/cache. Time/observation/mission changes invalidate deterministic
signatures. See [priority contracts](task_priority.md). No allocation is performed.


## Task 9 protocol-backed ownership admission

Opt-in allocation runs independent owner-only policies in DECISION after current
priority/suitability updates. Its own messages use existing P2P queues. Received
ownership notifications update only the recipient's versioned announcements and
refresh that recipient's decision results before ordinary controllers run. This
keeps AgentView task/status/result inputs consistent. AgentView adds only own
`allocation_feedback`, never an authoritative global ownership map.

After SUBMISSION transport, a separate trusted commit adapter validates exact
bid/award/accept/commit references and actual processed peer delivery, then applies
already-selected outcomes in stable request-ID order. Atomic task/agent updates
preserve WorldSnapshot invariants. It never chooses a winner, compares candidates
or discloses unrelated records. Own accepted/rejected feedback is available on the
next decision tick. Reset clears protocol/admission state and restores announcements
and the baseline world. These mechanics and their explicit centralized physical
admission assumption are documented in [allocation](decentralized_allocation.md).


## Task 10 physical commands and own feedback

Execution is opt-in and requires existing decentralized allocation. Setup initializes
coordinate spawns before sealing the replay baseline. Own controllers receive only
own execution snapshots/feedback; they decide their routes and work commands.
Commands issued at t are applied at t+1 in EXECUTION, after scheduled events and
before other trusted EXECUTION hooks. Motion precedes PERCEPTION, so sensing uses
the actual updated location. LOCAL_UPDATE then records those observations before
decisions. Status notices use normal P2P submission/delivery, not direct peer updates.
Completed/failed outcomes atomically preserve WorldSnapshot task/commitment invariants.
Reset clears every motion/controller/command/notice/outcome state and restores poses,
energy and initial task awareness. See [Task 10 contracts](movement_execution.md).


Task 11 opt-in recovery runs independent local heartbeat/failure/availability policies
at DECISION before existing local priority/evaluation/allocation/execution. Restricted
views add own recovery feedback and explicit own mission announcements. Submission
applies already-selected task commits, then validated retry/release requests; no
replacement ranking occurs in the engine. RECORD updates evaluation-only attempt
history. Trusted runtime introduce_objective discloses a new task to one recipient.
Reset also clears dynamic procedure registrations, retry/lease/announcement state and
recovery histories. See [recovery contracts](dynamic_reassessment_reallocation.md).
