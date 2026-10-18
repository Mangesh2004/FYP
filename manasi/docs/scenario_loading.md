# Four-room scenario and loading — Task 3

The source of truth for initial configuration is
[`scenarios/four_room_disaster.json`](../scenarios/four_room_disaster.json).
This is a simulator configuration containing private hazards, tasks and scripted
events. It is never supplied to agent controllers.

## Floor plan and state

Coordinates use abstract layout units, with x increasing right and y increasing
down. Each room is 4 × 4. The shared corridor is 10 × 2.

```text
  Room 1 (fire)       Room 2 (gas)
  x=0,y=0            x=6,y=0
      door-1             door-2
entry ───────── shared corridor ─────────
      door-3             door-4
  Room 3 (person)     Room 4 (clear)
  x=0,y=6            x=6,y=6
```

Each room connects bidirectionally to the corridor for one tick per edge; moving
between rooms would require two edges. No movement occurs yet. An exterior entry
opens on the corridor's west boundary. Outside is not a simulated location and
entry is not a navigable graph edge. All five locations are reachable from entry.
There are eight directed graph edges, four room doors and one exterior opening.

`coopai.geometry` provides immutable Rectangle, Segment, AreaGeometry, Passage
and FloorPlan values. Area interiors cannot overlap. Passage segments must lie
strictly inside each endpoint's boundary, cannot overlap other openings, and cannot
create self-links, unknown endpoints or duplicate directed connections. An exterior
entry cannot overlap another area's boundary, even partially. Corners are excluded
as door endpoints. Axis-aligned coordinates are compared exactly; configurations
should use simple consistent values, rather than approximate independently computed
coordinates. Negative coordinates are allowed; dimensions must be positive/finite.

`floor_plan.environment()` derives a separate public `Environment` graph from
passages. `floor_plan.walls()` returns a deterministic tuple of perimeter segments
with openings removed. Identical segments are deduplicated; partially coincident
shared-wall segments may remain, which is safe to draw with consistent thickness.
Wall count for this configuration is 25. Renderer geometry contains no hazards.

Core AgentState/Task/WorldSnapshot models and the existing simulation engine are
unchanged. Spawn and task positions remain location IDs. Future rendering can use
rectangle centers and deterministic per-agent marker offsets; these display
coordinates do not represent physical robot motion.

## Agents and tasks

| Agent | Spawn | Sensing | Operational capabilities | Battery |
|---|---|---|---|---|
| A | Room 1 | thermal | exploration | 1.0 |
| B | Room 2 | gas | exploration | 0.9 |
| C | Room 3 | thermal, visual (camera) | exploration | 0.95 |
| D | corridor | none | exploration, simulated-assistance | 1.0 |

All start available, with no commitment and update tick 0. Each has a support-kits
resource capacity of 2; D has 2 kits, the others have 0. Exploration is an operational
capability, distinct from sensing. No agent has firefighting or physical rescue.

| Task | Location | Sensing requirement | Operational requirement | Urgency input | Deadline tick | Duration parameter |
|---|---|---|---|---|---|---|
| task-1 fire-verification | Room 1 | thermal | exploration | 0.9 | 8 | 2 |
| task-2 gas-inspection | Room 2 | gas | exploration | 0.5 | 12 | 2 |
| task-3 person-search | Room 3 | visual | exploration | 0.9 | 8 | 3 |
| task-4 routine-inspection | Room 4 | none | exploration | 0.1 | none | 2 |

Severity inputs are 0.9, 0.6, 0.9, 0.1 respectively. Minimum battery is 0.1, and
there are no initial consumable demands. All tasks are pending, unassigned and
created at tick 0. These are configuration inputs, not computed scores.
`TaskDetails` stores task type and positive abstract duration separately from the
reusable Task model. No progress or execution is inferred from duration.

## JSON schema version 1

The root requires `schema_version`, `mission`, `locations`, `passages`, `agents`,
`tasks`, `conditions` and `events`. Each section's fields are demonstrated in the
sample JSON. Arrays and objects are strictly typed; required fields and unknown
fields are checked. Mission uses existing MissionConfiguration fields, including
seed, duration_ticks, tick_seconds and objectives. Version must be integer 1.

- Locations: id, name, type (`room` or `corridor`), bounds (x, y, width, height).
- Passages: id, source, target (location ID or null for exterior), opening
  (x1, y1, x2, y2), travel_ticks. Interior connections are bidirectional.
- Agents: id, name, location, capabilities (sensing/operational tag arrays),
  battery, availability, resources (name/amount/capacity). No task preassignment.
- Tasks: id, type, location, requirements (capabilities, minimum_battery,
  resources), severity, urgency, deadline (integer or null), status,
  duration_ticks. This initialization schema requires pending tasks.
- Conditions: location_id, subject, value; unique (location, subject) keys.
- Events: id, tick, action, target, description plus action-specific fields.

Loader entry points:

```python
from coopai.scenario import load_scenario, scenario_from_dict

scenario = load_scenario("scenarios/four_room_disaster.json")
engine = scenario.create_engine()
# The visualization can read scenario.floor_plan and engine.world.
# Decision callbacks must receive only the engine-supplied AgentView.
engine.run(8)
```

`scenario_from_dict` constructs frozen values without retaining mutable input data.
`load_scenario` adds UTF-8 JSON reading, duplicate JSON-key rejection and nonfinite
number rejection. `ScenarioError` includes the file and/or section path plus a
validation reason. Syntax and I/O errors are reported through the same error type.

Existing model validation is reused for numeric bounds, timestamps, capabilities,
resource levels, uniqueness and world references. The loader adds geometry
consistency, task metadata coverage, event targets/horizon and entry reachability.
It checks that each initial task has at least one available agent covering its
all-of sensing/operational requirements, initial battery/resources and reachability.
This is a setup sanity check with no ranking, selection or assignment. It does not
account for simultaneous resource use, future failures or execution energy costs.
IDs are unique within their entity namespace; references are typed by their field.
The generic loader does not hardcode exactly four rooms or agents; the supplied
primary configuration and its tests enforce the four-room example.

## Scripted events and reproducibility

Two typed effect variants use the existing DynamicEvent scheduling API:

- `set_condition` requires subject and value, targets a known location and maps to
  HAZARD_CHANGED. It sets an existing condition or introduces a new subject.
- `set_battery` requires battery, targets a known agent and maps to
  RESOURCES_CHANGED. It replaces battery and sets the agent update tick.

The sample escalates room-1 fire from present to severe at tick 3 and lowers B's
battery from 0.9 to 0.4 at tick 5. These are external scripts, not hazard resolution
or autonomous responses. Fire escalation does not automatically alter task urgency
or priority. Changing a condition is not evidence that an agent has detected it.

`Scenario.create_engine` constructs the existing SimulationEngine and schedules
stateless bound effect handlers. Description text is never executable. Same-tick
effects use the engine's event-ID ordering; multiple effects on the same field
therefore have deterministic last-applied behavior. Reset restores the initial
world and schedule and replay reproduces both effects. Initialization uses no
randomness; future randomness comes from the engine's seeded named streams.

New task arrival, failure, task-urgency changes and passage unavailability require
additional explicitly validated effect variants in future milestones; unsupported
actions are currently rejected. Passage geometry stays static; future blocked/open
status should be separate runtime state rather than deleting wall geometry.
No failure recovery, reassessment, task scoring or reallocation is implemented.

## Boundary and visualization readiness

Scenario ground truth, task metadata and event definitions are trusted setup and
renderer inputs. Agent callbacks still receive only their own state, simulation
time and explicitly delivered observations. This milestone registers no perception
system and publishes no observations. Battery change is reflected only in B's own
state; other agents do not receive B's state. Hazards and pending world tasks remain
hidden from callbacks. Regression tests exercise these boundaries with all agents.
No correction to the existing engine interface was necessary.

Task 4 reads `floor_plan.areas`, `passages`, `walls()`, `task_details`,
`engine.world`, `engine.time`, `engine.state` and `engine.event_trace` from trusted
UI infrastructure. Play/pause/resume/reset use the existing engine methods; a single
step while paused can resume, step, then pause if the horizon was not reached.
Label truth-level hazard/task displays clearly. Rendering and UI state must not
enter decision callbacks. Task 4 adds an optional GUI dependency; see [visualization](visualization.md).

The configuration is an editable source-checkout artifact, not packaged wheel data.
The CLI's default resolves the checkout `scenarios` directory; installed wheels
should be passed an explicit configuration path. No Gazebo integration, navigation,
sensing, fusion or decentralized allocation is present yet.

Task 5 adds optional current-location sensing and local knowledge with
`create_engine(with_perception=True)`; the GUI enables it. The earlier information
boundary discussion describes the perception-free default, which remains available.


## Task 7 mission-priority configuration

An optional `priority` root section configures validated weights, deadline/freshness
windows, unknown risk, default importance and task-type/subject/value rules. Existing
files without it remain supported. The sample JSON includes the documented baseline.
`create_engine(with_priority=True)` installs perception and explicitly announces all
initial tasks to each agent; `with_communication=True` is independent. Later tasks
are not implicitly published. See [task priority](task_priority.md).


Task 10 reuses existing task type/duration metadata as explicitly public execution
procedures. `scenario.create_engine(with_execution=True)` enables perception,
announcements, priority/suitability, allocation and abstract execution. Optional
ExecutionConfiguration is supplied programmatically; no JSON schema changes or new
dependencies are needed. Known unsupported procedure types fail explicitly. Spawn
location-only positions acquire area-center coordinates before replay initialization.
The checked-in four-room JSON is unchanged. See [movement/execution](movement_execution.md).


## Task 12 autonomous mission configurations

The original file/schema/default validation are preserved. The core loader additionally
accepts optional agent position {x,y}, typed power_failure, set_connected and
discover_task events, and allow_infeasible_objectives (boolean, default false).
Explicit positions must be finite and inside their declared walkable area/door;
entry reachability remains required. Discovery uses the same Task schema with event
creation time, a valid recipient and explicit recoverability. Network changes affect
transport only. Unknown IDs/actions, duplicate discovery IDs, invalid procedure/
deadline data and inconsistent stock/capacity fail validation. The infeasibility opt-in
does not relax those structural checks.

New review files also contain demonstration and communication metadata; load them with
load_mission_scenario(path), or get_scenario(id) from coopai.mission_scenarios. This
wrapper delegates physical construction to scenario_from_dict and validates the review
vocabulary, network settings and post-run expected counts. Existing load_scenario
continues to accept the original strict physical-scenario format. No winner, path
or completion override fields exist. See [all five definitions and examples](task_12_scenarios.md). Generic create_engine now accepts communication_configuration
and uses existing public allocation windows for its latency.
