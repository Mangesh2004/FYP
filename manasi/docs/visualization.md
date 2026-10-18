# Interactive 2D visualization — Task 4

## Install and launch

```sh
.venv/bin/python -m pip install -e '.[test,gui]'
.venv/bin/python -m coopai.gui
# Or pass an explicit configuration path:
.venv/bin/python -m coopai.gui scenarios/four_room_disaster.json
```

Python 3.14.2 on this arm64 Mac could not install a binary wheel of standard pygame.
The optional `gui` extra therefore uses **pygame-ce >=2.5.7,<3** (Pygame Community
Edition), which uses the `pygame` import/API and supplies compatible macOS wheels.
Verified installation: pygame-ce 2.5.8, CPython 3.14 universal2 wheel.
Package references: [pygame](https://pypi.org/project/pygame/) and
[pygame-ce](https://pypi.org/project/pygame-ce/). Do not install both distributions
into one environment because they share the pygame namespace. No source compilation,
external images, NumPy, GUI framework or system SDL installation was needed.
The headless core still has no runtime dependencies; GUI installation is optional.

The default scenario path resolves the source checkout. Wheel installs should pass
an explicit JSON file, as with the earlier scenario demo.

## Architecture and information boundary

- `coopai.visualization` has no Pygame import. ScreenTransform maps scenario geometry
  to pixels with uniform scaling, padding and centering. DashboardSnapshot holds
  immutable display inputs derived from the existing world and static task metadata.
  AgentDisplay/TaskDisplay format capabilities, battery, owner and initial urgency.
- PlaybackController only calls existing engine lifecycle methods and step. Its
  fractional wall-time budget determines when a step is requested, not simulated
  time. The displayed clock is always the engine's SimulationTime.
- `coopai.gui` initializes Pygame display/fonts, loads the existing scenario and
  creates its existing engine. DashboardApp handles input and draws snapshots.
  It never assigns tasks, writes world values, produces sensor observations or
  consumes simulation RNG streams.

The renderer reads privileged conditions, tasks and peer states for debugging.
The permanent label is **SIMULATOR VIEW — NOT AGENT KNOWLEDGE**. No display inputs
are passed to agent controllers; their existing AgentView boundary is unchanged.
The GUI registers no controllers, sensing or decision systems. Tests verify that
capturing/rendering snapshots does not mutate truth or advance the clock, and that
registered agent callbacks still receive no global truth. UI control operations
advance/reset truth only through authorized engine APIs and existing event handlers.

The engine, core models, loader, geometry and scenario configuration remain unchanged.
A future Gazebo adapter can reuse the existing observation/command/time interfaces;
rendering and playback stay outside decision modules. No adapter is implemented now.

## Dashboard and controls

A 1200 × 780 dashboard places the scenario floor plan on the left and agent/task/
event panels on the right. Labels and colors come from current snapshot conditions;
geometry comes exclusively from FloorPlan. Walls exclude openings and teal lines
mark doors and exterior entry. Colored circles represent stationary agents; outlined
squares represent tasks. IDs, capability categories, battery percentages/bars,
availability, initial urgency, status and owner are shown in the side panel.
The event log lists applied engine events only and clears on reset.

| Control | Mouse | Keyboard |
|---|---|---|
| Play/resume | Play | Space |
| Pause | Pause | Space |
| Single-step, then pause | Step | Right arrow or N |
| Reset initial world/timeline | Reset | R |
| Slower/faster | - / + | - / + (or =) |
| Scroll a longer right panel | Mouse wheel/trackpad | — |
| Close | Window close | Escape |

Starts ready with tick 0. Play auto-starts ready or resumes paused engines.
Single-step works from ready, paused or running and leaves paused unless finished.
At the horizon, play/step/pause are disabled; reset restores ready. Speed options
are 0.25×, 0.5×, 1×, 2×, 4× and are retained across reset. Focus loss pauses the run.
Errors stop playback and show a message; failed engines require reset.

Normal playback uses `pygame.time.Clock` elapsed milliseconds and caps rendering
at 60 FPS. It does not advance once per frame. At 1×, one simulation step consumes
mission.tick_seconds of wall-time budget. Eight steps maximum execute per frame;
excess budget is retained rather than dropping simulation ticks. Pause, single-step,
reset and speed changes clear unused budget. The frame interval preceding a control
change is not charged to a new run or at a changed speed, so time spent idle does
not cause immediate catch-up after Play. Rendering consumes no seeded randomness.

## Expected event behavior

After processing tick 3, room-1 fire changes from present to severe and its tint
strengthens. After processing tick 5, B's battery changes from 90% to 40% and the
bar shrinks. Both changes are applied by the existing scenario handlers and logged
from engine.event_trace. Reset restores fire present, B 90%, all pending/unassigned
tasks, initial agent locations, tick 0 and an empty applied-event log.

The engine tick denotes the next tick to process outside callbacks: four single
steps process ticks 0–3 and display tick 4, while six steps process ticks 0–5 and
display tick 6 / 3.0 seconds. The event log retains event ticks 3 and 5. Displayed
initial urgency remains a scenario input, not a calculated task priority.

## Verification

```sh
.venv/bin/python -m pytest
# Offscreen integration exercise; no desktop needed:
SDL_VIDEODRIVER=dummy .venv/bin/python -m coopai.gui --smoke-test
# Native window exercise, saved tick-6 framebuffer, then automatic close:
.venv/bin/python -m coopai.gui --smoke-test --screenshot docs/task4_preview.png
```

The smoke mode uses actual Pygame mouse/key event objects through the same input
handler and a synthetic elapsed-time interval for its bounded integration check.
It verifies Play, Pause, five single steps, dynamic conditions/battery, speed and
Reset. Normal interactive mode uses real elapsed wall time. The native test ran
successfully with the macOS Cocoa display driver. An actual frame was inspected:
[Tick-6 dashboard preview](task4_preview.png).

The full tests include headless geometry/snapshot/controller tests and optional
SDL dummy-renderer integration tests. Without the gui extra, six renderer tests
skip; all core/display-controller tests remain runnable. With the extra installed,
all 208 tests passed, including the 176 earlier tests. See
[Task 4 verification](task_4_verification.md) for exact commands/results.

## Limitations and next milestone

This is an accurate debugger/demo view of scripted environment changes. Agents
remain stationary and tasks pending/unassigned. There is no perception, communication,
fusion, scoring, allocation, execution, navigation or Gazebo integration. Marker
offsets within an agent's actual location are display spacing, not physical positions.
The current task metadata covers configured tasks; later task-arrival support must
supply metadata for new IDs as part of that milestone.

Window sizing is fixed; very small displays or unusual scaling may need a future
responsive layout. Long labels are truncated with an ellipsis; the right panel
supports scrolling for additional rows. Manual review should check comfortable
readability, native mouse/trackpad behavior and window placement on the review
machine's display configuration. Automated native launch, rendering and control
handling have passed; unscripted human interaction is not claimed as tested.

Next: local perception and agent-local knowledge foundations, then peer messaging
and genuine decentralized decisions in bounded milestones. The GUI provides a
working demonstration shell without simulating those future capabilities.


## Task 5 local knowledge inspector

The GUI now creates a perception-enabled engine. Clicking a circle or pressing
1–4 selects an agent's evidence; K/Knowledge/Status toggles the right panel. This
panel reads independent local stores, not world conditions. Reset clears those
stores automatically. The left truth plan keeps its privileged label. See
[perception and knowledge](perception_and_knowledge.md) and the
[inspector preview](task5_preview.knowledge.png). Earlier verification/limitations
above describe Task 4; local perception is now implemented, while communication,
fusion and decisions remain future work.


## Task 6 communication inspector

M/Messages opens privileged sent/delivered/dropped diagnostics in a separate panel.
Selected-agent knowledge labels Direct or Remote via sender and original source.
GUI transport defaults to one-tick latency/no loss; CLI options --latency-ticks,
--loss-probability and --ttl-ticks configure it. --no-communication preserves isolated
perception. Reset clears all communication state and histories. See
[communication](communication.md) for current contracts and previews. Earlier
milestone test counts remain historical; Task 6 verification is recorded separately.


## Task 7 priority inspector

P/Priority opens selected-agent calculated priorities without redesigning the dashboard.
1–4 or circle selection retains this mode. It shows announced tasks, calculated
scores/factors, initial urgency, source and evaluation tick with brief explanations.
Rendering only reads cached results; it cannot query truth for scoring. Before start
or after reset, announced tasks await evaluation. A native frame was inspected:
[priority preview](task7_preview.priority.png). See [formula](task_priority.md).


## Task 9 allocation inspector

Default GUI communication now enables independent decentralized allocation. **L**/
Allocation displays privileged authoritative owners/status separately from the
selected agent's own round state, received bids, provisional/confirmed owners,
phase deadlines and rejection/expiry/message trace. It does not merge bidders'
private scores. Scroll for further rounds and events. Rendering never negotiates.
P/E/K/M and lifecycle controls remain available; reset clears all rounds/assignments.
`--no-allocation` retains the Task 8 behavior; `--no-communication` also disables
allocation. The native default smoke captures provisional ownership after tick 3,
confirmed ownership after tick 8, and verifies reset; configured latency/failure
smoke checks preserve safety without asserting successful communication consensus.
Agents remain stationary and tasks never advance past ASSIGNED in this milestone.
See [protocol](decentralized_allocation.md).


## Task 10 actual movement and execution

The default GUI now enables execution when allocation is enabled. X/Execute shows
selected-agent position, accepted waypoints, destination, execution phase/progress,
battery, observation-backed outcome and event history. Markers use actual x/y poses;
accepted paths and center destinations are drawn, with teal/red completion/failure
rings. Hazard backgrounds remain independent. Tick movement is rendered directly;
there is no fabricated animation or physical interpolation. P/E/L/M/K, selection,
lifecycle and speed controls remain available. `--no-execution` preserves Task 9;
no-allocation/no-communication also disable execution. Native smoke reaches tick 16,
saves intermediate movement and final completion frames, then verifies full reset.
See [movement/execution](movement_execution.md).


Task 11 adds H / Recovery in the title row; R remains Reset. Recovery diagnostics
show objective state separately from failed attempt history, previous/current owners,
progress, local priority range, rounds, measured timing and peer events. Runtime task
metadata is rendered safely without disclosing it to agent decisions. Existing
controls/inspectors remain available. Use --recovery-fixture failure, new-task or
disruption for extended fixtures; --no-recovery retains Task 10 behavior.
See [recovery GUI/limits](dynamic_reassessment_reallocation.md).


## Task 12 scenario selection and review interface

Default CLI launch opens the five-case menu; --scenario corridor/distributed/emergency/
failure-disruption/constrained selects directly. S pauses/opens selection, 1–5 or a
click creates a fresh configured session. Tab/task rows/destination rings inspect tasks,
1–4/circles select the agent whose stored explanations are shown, V shows summary,
J shows filtered events. All previous K/M/P/E/L/X/H and playback controls remain.
Reports distinguish authoritative ownership from selected-agent knowledge and historical
eligible bids from current terminal-task ineligibility. Unknown tasks remain unknown
to an uninformed agent. Explanations wrap and scroll rather than dropping their reasons.

Summaries appear after actual configured objectives/events complete or at the bounded
horizon, preserving explicit pending/incomplete work and failed attempt history. The
clock remains under normal controls; rendering never marks a task completed or moves
an agent. Multiple task labels/rings remain distinct at one destination. See
[review commands, screenshots and controls](task_12_scenarios.md). Legacy path/feature
flags and Task 11 --recovery-fixture remain available.
