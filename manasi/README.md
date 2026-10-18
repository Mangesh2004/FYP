# CoopAI — cooperative mission simulation

Python foundations for the **Decentralized AI Framework for Cooperative Multi-Agent
Autonomous Systems**. Package name `coopai` abbreviates cooperative AI. Task 1
provides immutable validated domain models and a formal specification. Task 2 adds
a deterministic headless simulation engine and a runnable diagnostic demo.
Task 3 adds strict JSON scenario loading and a concrete four-room environment.
Task 4 adds an interactive 2D Pygame dashboard. Task 5 adds capability-limited
local perception and independent agent knowledge. Tasks 6–8 add peer communication,
agent-local priority, hard eligibility and self-suitability. Task 9 adds decentralized
bidding and protocol-backed assignments. Task 10 adds autonomous doorway movement,
synthetic energy consumption and evidence-backed inspection execution. Task 11 adds
agent-local failure detection, safe fresh attempts, decentralized recovery, runtime
task announcements and measured recovery histories. Task 12 assembles five validated
review missions with scenario selection, actual decision explanations and mission summaries.

Read [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) and
[system specification](docs/system_specification.md) for scope and future contracts.

## Run the Multi-Agent Simulation

```sh
.venv/bin/python -m coopai.gui                         # choose a mission in the menu
.venv/bin/python -m coopai.gui --scenario corridor     # primary review demonstration
.venv/bin/python -m coopai.gui --scenario distributed
.venv/bin/python -m coopai.gui --scenario emergency
.venv/bin/python -m coopai.gui --scenario failure-disruption
.venv/bin/python -m coopai.gui --scenario constrained
.venv/bin/python -m coopai.scenarios_demo --all
.venv/bin/python -m pytest -q
```

For the October 21, 2026 review, start **Corridor Deployment**: all four agents begin
in the corridor, negotiate through real peer messages, move through doors, inspect
and complete their objectives. Press **Space** to play/pause, **Right** to step,
**R** to reset and **+/-** for speed. **S** selects a fresh scenario; **Tab** or a
clicked task row/ring opens decision explanations; **1–4** selects an agent;
**V** shows summary; **J** shows significant events. K/M/P/E/L/X/H remain available.

The five cases demonstrate corridor deployment, mismatched starts/direct evidence,
new urgent work after hazard escalation, physical failure with a distinct peer outage,
and resource constraints. Actual outcomes are **4/4, 4/4, 5/5, 4/4 recovered, and 3/4**
respectively. The remaining constrained objective is explicitly pending/unowned.
No allocation winner, replacement or movement sequence is configured.

Task 12 verification: **741 tests passed**, preserving all 633 earlier tests. All five
headless cases report deterministic replay equality. Native macOS scenario selection,
movement, summary and reset smoke passed; no dependencies installed. Advanced priority
preemption remains Task 13 work; Task 14 comparisons and Task 15 final validation were
not implemented here. Inspections report conditions and do not remove hazards.

[Five scenario definitions, actual outcomes and controls](docs/task_12_scenarios.md) ·
[Actual verification and changed-file inventory](docs/task_12_verification.md) ·
[Scenario menu](docs/task12_menu.png) · [Corridor movement](docs/task12_corridor.movement.png)

Previous milestone sections below retain their historical commands/results. The original
scenario remains available via `python -m coopai.gui scenarios/four_room_disaster.json`;
Task 10/11 demonstrations and legacy feature/network flags remain supported.

## Setup and tests

Python 3.11+ is required. The headless core has no runtime dependencies.
The optional GUI uses a bundled native Pygame wheel; no Docker is needed.
On macOS Apple Silicon, use a native arm64 Python installation:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest
```

Activation is optional when using `.venv/bin/python` directly. Installation needs
package-index access. setuptools is the build backend; pytest is the only direct
test dependency. The GUI extra adds Pygame Community Edition; no ML packages
are needed.

Example after installation:

```python
from coopai.models import AgentIdentity, AgentState, Capabilities, Position

agent = AgentState(
    AgentIdentity("A1", "Scout"),
    Capabilities(sensing=frozenset({"thermal"}), operational=frozenset({"exploration"})),
    Position("room-1"),
    battery=1.0,
)
```

`Capabilities.meets` checks category-specific subsets only. It does not determine
full eligibility by itself. IDs are opaque, time uses integer ticks, confidence and battery use
[0,1], and connectivity is directed. Frozen dataclasses are updated by constructing
new snapshots; `Task.transition` validates local lifecycle transitions. World snapshots validate cross-object
references and active commitments. Task 9 negotiates ownership through peer messages and separate atomic admission.

## Repository

- `src/coopai/`: reusable models, no scenario constants.
- `tests/`: behavioral and invalid-input tests.
- `docs/`: specification and milestone verification.
- `scenarios/`: validated four-room JSON configuration.
- `experiments/`: reserved for evaluation work.

## Headless simulation demo

```sh
.venv/bin/python -m coopai.demo
```

The demo advances five half-second ticks, pauses/resumes, applies two scripted
world events at tick 2 and resets for deterministic replay. Its output shows
simulator diagnostics; it does not demonstrate agent perception or decisions.

See [engine contracts and replay rules](docs/simulation_engine.md) for lifecycle,
phase ordering, agent observation/command interfaces and future adapter boundaries.
No additional dependencies are required. Reset restores the world and initial
schedule; state held externally by callbacks must be reset by its owner.

## Four-room scenario demo

```sh
.venv/bin/python -m coopai.scenario_demo
.venv/bin/python -m coopai.scenario_demo scenarios/four_room_disaster.json --steps 8
```

Loads four rooms and a shared corridor, four heterogeneous agents and four pending,
unassigned tasks. Prints the geometry, capabilities, requirements and actual event
timeline. At tick 3 fire escalates; at tick 5 Agent B's battery drops. Reset replay
is compared against the original world/event/random trace. This output is simulator
truth, not agent knowledge or autonomous decision behavior.

See [scenario schema and layout](docs/scenario_loading.md). Geometry, task metadata
and world snapshots are immutable and available to the future trusted renderer.
The default demo path assumes a source checkout; pass a JSON path for wheel installs.
No additional dependencies were installed.

## Interactive 2D simulation

```sh
.venv/bin/python -m pip install -e '.[test,gui]'
.venv/bin/python -m coopai.gui
# Optional scenario path:
.venv/bin/python -m coopai.gui scenarios/four_room_disaster.json
```

The `gui` extra installs **pygame-ce** (Pygame Community Edition), using the
`pygame` API. Standard pygame has no compatible binary wheel for the current
Python 3.14 environment; pygame-ce 2.5.8 was verified on macOS arm64. Install one
Pygame distribution in the virtual environment.

Starts at tick 0. Click Play/Pause or press **Space**; **Right arrow/N** single-steps,
**R** resets, **+/-** changes speed, and **Escape** closes. Scroll the right panel
if needed. Losing window focus pauses the simulation. Reset retains selected speed.

The dashboard displays the configured rooms, corridor, walls, passages, agent
positions/capabilities/batteries/availability, task requirements/status/owners and
applied event log. It is labeled **SIMULATOR VIEW — NOT AGENT KNOWLEDGE**.
Agents remain stationary. The default GUI now negotiates assignments through
Task 9 and executes those inspections through Task 10. `--no-execution` retains
Task 9 assignments; `--no-allocation` retains pending/unassigned tasks. Four steps process
fire escalation at event tick 3; six steps process B's battery drop at event tick 5.
Reset restores initial world state and clears the event timeline.

[Dashboard preview](docs/task4_preview.png) · [Architecture and controls](docs/visualization.md)

The window is fixed at 1200 × 780; unusually small displays may need adjustment.
Long labels truncate. Tasks 9–10 add allocation, movement and task execution below. Local perception and independent knowledge are now enabled in the GUI.
See the architecture document for manual display-review notes.

Verification:

```sh
.venv/bin/python -m pytest
SDL_VIDEODRIVER=dummy .venv/bin/python -m coopai.gui --smoke-test
.venv/bin/python -m coopai.gui --smoke-test
```

The optional renderer tests skip without the gui extra. With it installed, all 208
tests passed at Task 4. Native macOS Cocoa launch, controls, dynamic rendering and reset were
verified. Task 5 adds local perception/knowledge below; peer communication and subsequent
decentralized decision milestones await review.


## Local perception and knowledge

```sh
.venv/bin/python -m coopai.perception_demo
.venv/bin/python -m coopai.gui
```

Only current-location conditions supported by an agent's sensors are observed.
A detects fire thermally, B detects gas, C retains possible-person evidence via
thermal/visual sensing, and D receives no specialized observations. Observations
preserve confidence and tick timestamps; local stores never backfill world truth.

Click an agent circle or press **1–4** to inspect that agent's recorded knowledge.
**K** or the Knowledge/Status button toggles the right panel. The simulator truth
plan and agent-local knowledge have separate labels. Empty evidence is **UNKNOWN**;
old evidence is marked **STALE** without deleting it. Reset clears all histories
and replay regenerates the same evidence. Generic scenario engines opt in with
`scenario.create_engine(with_perception=True)`; the GUI enables it automatically.

[Perception/knowledge contracts](docs/perception_and_knowledge.md) ·
[Local inspector preview](docs/task5_preview.knowledge.png)

Milestone verification: **238 tests passed**, preserving all 208 earlier tests. No new
dependencies were installed. Task 6 below adds peer communication. Tasks 7–8 add scoring below. Advanced fusion, allocation, navigation, execution
and Gazebo integration remain future work.


## Peer communication and distributed knowledge

```sh
.venv/bin/python -m coopai.communication_demo
.venv/bin/python -m coopai.gui --latency-ticks 2 --loss-probability 0.25 --ttl-ticks 8
# Retain Task 5's isolated local sensing:
.venv/bin/python -m coopai.gui --no-communication
```

The GUI now enables peer communication by default. **M** or Messages opens
simulator-wide sent/delivered/dropped activity; **1–4** selects an agent's knowledge,
which labels Direct versus Remote via sender. Remote evidence retains original
source, confidence and timestamps. No confidence is added by repeated forwarding.

The headless demo shows A→B fire evidence arriving after two ticks, an observation
dropped during D's outage, another dropped by configured loss, and identical replay
of message/knowledge histories after reset. Generic scenario engines opt in with
`create_engine(with_communication=True)`; existing demos remain compatible.

[Communication contracts](docs/communication.md) ·
[Remote evidence preview](docs/task6_preview.knowledge.png) ·
[Message inspector](docs/task6_preview.messages.png)

Milestone verification: **294 tests passed**, preserving the earlier 238 tests.
No new dependencies were installed. Transport is simulated and messages are
unreliable by configuration; no retries or real sockets are implemented. Tasks 7–8 add scoring below. Auctions, assignments, navigation, execution, Gazebo
and advanced belief fusion remain future work.


## Dynamic agent-local task priority

```sh
.venv/bin/python -m coopai.priority_demo
.venv/bin/python -m coopai.gui
```

Press **P**/Priority, then **1–4** or click circles to compare agents. The inspector
shows only explicitly announced tasks, calculated scores/factors, evidence sources
and last evaluation ticks, separately from initial urgency. The initial mission
announces its four tasks to all agents without assigning them. Priority is enabled
in the GUI; generic engines opt in with `create_engine(with_priority=True)`.

The transparent baseline uses normalized weights for urgency (0.25), deadline
pressure (0.20), evidence-derived environmental risk (0.35), mission importance
(0.15), and uncertainty (0.05). Configuration and severity mappings are in the
scenario JSON's optional `priority` section. Unknown is distinct from safe;
confidence/freshness filtering and conservative conflict selection preserve evidence
and uncertainty. No simulator hazards or other agents' private stores enter scoring.

The two-tick-latency demo reports fire priority A=0.76625 versus B=0.67169 at
evaluation tick 3, then B updates after severe-fire evidence arrives at tick 5.
Scores emerge from the algorithm; reset reproduces complete histories. No allocation
or task execution occurs.

[Formula and limitations](docs/task_priority.md) ·
[Priority inspector](docs/task7_preview.priority.png)

Milestone verification: **349 tests passed**, including all 294 previous tests.
No dependencies added. These weights are a configurable baseline, not experimentally
optimized or claimed novel. Task 8 adds eligibility/suitability below.


## Hard eligibility and agent-local suitability

```sh
.venv/bin/python -m coopai.suitability_demo
.venv/bin/python -m coopai.gui
.venv/bin/python -m pytest -q
```

Press **E**/Eligibility and select an agent with **1–4** or circles. The inspector
shows its known tasks, hard feasibility/rejection reasons, suitability scores,
travel effort and factors. Priority remains separate; ineligible scores are N/A.
The GUI enables self-evaluation; generic engines opt in with
`scenario.create_engine(with_suitability=True)`.

Capability categories, operational availability, commitments, task lifecycle,
reachability, battery and resource demands are hard constraints. The baseline
suitability uses battery reserve (0.45), graph travel ease (0.35), information
confidence/freshness (0.20), and resource reserve (0.20 only for positive demands),
normalized over active factors. Default reserve is 0.1 battery plus 0.01 per travel
tick; every threshold/weight is public and configurable. Each agent evaluates itself
using its own state/evidence/announced tasks and explicitly public topology only.
No global ranking or task allocation is performed.

The actual demo shows fire eligibility A/C, gas B, person search C, routine inspection
A/B/C/D. B's gas suitability drops **0.930 → 0.680** at tick 5 while eligibility
remains true. Reset reproduces full independent histories. No dependencies added.

[Rules, formula and limitations](docs/eligibility_suitability.md) ·
[Eligibility inspector preview](docs/task8_preview.eligibility.png) ·
[Task 8 verification](docs/task_8_verification.md)

Task 8 milestone verification: **417 tests passed**, preserving all 349 previous tests.
Native macOS Cocoa smoke and deterministic replay passed; details are in the report. Next milestone:
Task 9 below adds decentralized allocation. Movement, task execution and Gazebo
integration remain future work.


## Decentralized bidding and task allocation

```sh
.venv/bin/python -m coopai.allocation_demo
.venv/bin/python -m coopai.gui
.venv/bin/python -m pytest -q
# Keep the Task 8 dashboard behavior:
.venv/bin/python -m coopai.gui --no-allocation
# Bounded GUI/control/reset verification:
.venv/bin/python -m coopai.gui --smoke-test --screenshot docs/task9_preview.png
```

The default GUI enables negotiation with communication. Press **L**/Allocation to
see authoritative task owners alongside the selected agent's independent rounds,
received bids, provisional awards, deadlines, confirmation and rejection/expiry
history. **1–4** or circles select agents; scroll for further rounds. P/E/K/M and
play/pause/step/reset remain available. Provisional awards never appear as finalized
owners. `--no-communication` disables negotiation and retains isolated sensing.

Each agent evaluates itself and uses actual P2P CFP/bid/award/accept/commit messages.
Task-specific coordinators rotate deterministically using public IDs/clock only.
Own priority orders coordination and competing awards; received suitability bids
select a winner for one task. The separate simulator adapter validates auditable
confirmation evidence and applies atomic ownership; it never compares private
scores or chooses winners. Generic engines opt in with
`scenario.create_engine(with_allocation=True)`.

Actual normal-run owners: **fire A, gas B, person C, routine D**. B commits at tick 3;
remote winners at tick 6. At two-tick latency with explicit protocol windows,
remote commitments occur at tick 11. The controlled award-loss demo leaves tasks
pending, then rotates coordinators and recovers by tick 16 in an explicitly longer
experiment horizon. Normal and failure histories replay identically after reset.

Current verification: **493 tests passed**, preserving all 417 previous tests.
Native macOS Cocoa and headless SDL smoke passed. No dependencies added, movement
or task execution implemented. The simulator's atomic ownership guard guarantees
single-owner physical state; it is not distributed consensus under arbitrary
partitions. Partial-information greedy bidding can leave important tasks pending
under loss; that limitation is tested and documented.

[Protocol, state machine and safety assumptions](docs/decentralized_allocation.md) ·
[Provisional awards](docs/task9_preview.provisional.png) ·
[Confirmed allocation inspector](docs/task9_preview.allocation.png) ·
[Actual verification and changed files](docs/task_9_verification.md)

Task 10 below adds abstract movement/task execution. No automatic commit or push.


## Autonomous movement and task execution

```sh
.venv/bin/python -m coopai.execution_demo
.venv/bin/python -m coopai.gui
.venv/bin/python -m pytest -q
# Keep Task 9 allocation without execution:
.venv/bin/python -m coopai.gui --no-execution
# Native bounded control/movement/completion/reset verification:
.venv/bin/python -m coopai.gui --smoke-test --screenshot docs/task10_preview.png
```

The default GUI now moves agents using real coordinate state and accepted doorway
routes. **X / Execute** shows position, destination, phase, progress, battery,
evidence-backed outcome and execution events. P/E/L/M/K, agent selection, pause,
single-step, reset and speed controls remain available. Destination rings show
completed/failed status; hazard backgrounds remain unchanged.

Agents accept only their own protocol-confirmed mission and issue navigation,
begin/work/completion commands. The separate adapter validates ownership, graph/
geometry, arrival, capabilities, energy/resources and fresh direct evidence.
Graph Dijkstra selects location routes; center and door waypoints enforce wall
avoidance. Default speed is 1.5 map units/tick, movement cost 0.01/unit, valid-work
cost 0.01/tick and operating minimum 0.05 (task/eligibility minimums still apply).
Required consumables are charged once at begin, not repeatedly at each tick.

Generic engines opt in with `scenario.create_engine(with_execution=True)`, which
includes existing decentralized allocation. Existing task metadata controls duration.

| Task / owner | Assignment | Arrival | Execution start | Completion |
|---|---:|---:|---:|---:|
| Fire / A | 6 | 8 | 9 | 12 |
| Gas / B | 3 | 5 | 6 | 9 |
| Person / C | 6 | 8 | 9 | 13 |
| Routine / D | 6 | 11 | 12 | 15 |

D starts moving at tick 8; A/B/C already spawn at their task centers. All four tasks
complete within the existing 16-tick horizon. Final batteries are A 98%, B 38%,
C 92%, D approximately 93.25%. B's tick-5 reduction to 40% does not force failure.
The separate low-battery demo stops D at tick 9, marks its task failed/unowned and
clears its commitment without reassignment. Both histories replay identically.

Completion means reporting fire/gas/person/inspection findings, not eliminating
hazards or rescuing a person. D's coarse `operational:exploration` inspection adds
no specialized sensing capability. Deadlines retain the earlier priority-pressure
meaning; fire/person finish after their desired tick-8 deadlines. Energy and point
kinematics are abstract; no robot/inter-agent collision physics or interpolation is
implemented.

Current verification: **573 tests passed**, preserving all 493 earlier tests.
Native macOS Cocoa motion/completion/reset smoke passed; no dependencies added.

[Architecture, rules and limitations](docs/movement_execution.md) ·
[Movement preview](docs/task10_preview.movement.png) ·
[Execution outcome](docs/task10_preview.execution.png) ·
[Actual verification and changed files](docs/task_10_verification.md)

Task 10 verification above is historical; Task 11 recovery is described below.
No ROS2, Gazebo, hardware, ML, automatic commits or pushes were added.

## Dynamic reassessment and decentralized recovery

```sh
.venv/bin/python -m coopai.recovery_demo
.venv/bin/python -m coopai.gui --recovery-fixture failure
.venv/bin/python -m coopai.gui --recovery-fixture new-task
.venv/bin/python -m coopai.gui --recovery-fixture disruption
.venv/bin/python -m pytest -q
# Restore Task 10 terminal failure behavior:
.venv/bin/python -m coopai.gui --no-recovery
# Bounded native recovery rendering/reset verification:
.venv/bin/python -m coopai.gui --recovery-fixture failure --smoke-test --screenshot docs/task11_recovery.png
```

**H / Recovery** shows objective state, failed/current attempts, ownership history,
progress, local priority changes, allocation rounds, peer events and measured recovery
metrics. **R still resets**; K/M/P/E/L/X, selection and playback controls are preserved.
Recovery fixtures use an extended 80-tick horizon; the original JSON remains unchanged.
Generic engines opt in with `scenario.create_engine(with_recovery=True)`.

D's battery failure at tick **9** stops its old attempt and clears ownership. Peers
detect silence at **15**, learn fresh availability through messages, and negotiate
at **20**. A wins from actual bids (A 0.614773, C 0.584091, B 0.307955), commits at
**23**, moves at **25**, arrives **31**, restarts inspection **32**, completes **35**.
FAILED `task-4` is retained; `task-4::attempt:2` completes the objective. A separate
capability-loss fixture selects C, demonstrating that the replacement is not fixed.

The new urgent task is disclosed only to B at **11**, reaches peers at **12**, and
is assigned to D at **26**, completing **32**. It misses its desired deadline 20
by 12 ticks. Under temporary total message loss in [9,26), A safely uses its existing
self-bid path and completes at 35; repeated availability eventually reaches B/C at
29. This does not imply peer agreement during the partition. Recovery metrics retain
missing values, failed rounds and actual message drops.

Failure recovery reuses local priority, hard eligibility, suitability and existing
Contract Net allocation. The simulator validates matching attempts/leases and atomic
ownership; it never ranks replacements. Terminal failures remain terminal, retries
have fresh IDs, work restarts and consumables are not refunded. An offline owner
cannot announce failure. A live partitioned owner retains its valid renewed lease;
no eligible replacement means explicit waiting/unassigned state.

Priority-driven preemption is **deferred**; explicit release of current reversible
inspection work is supported. Comparative baselines and multi-seed research analysis
are next, with no superiority claim yet. No dependencies, commits or pushes added.

[Recovery architecture, measured traces and limitations](docs/dynamic_reassessment_reallocation.md) ·
[Recovery inspector](docs/task11_recovery.png) ·
[Actual verification and full changed-file inventory](docs/task_11_verification.md)

Task 11 verification: **633 tests passed** (all 573 earlier tests plus 60 new checks).
Native Cocoa recovery smoke and headless new-task/disruption/reset smoke passed.
