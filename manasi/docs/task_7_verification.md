# Task 7 verification

Environment: macOS arm64, Python 3.14.2, pytest 9.1.1, existing pygame-ce 2.5.8.
No dependencies installed, commits or pushes made.

## Actual commands/results

- Baseline `.venv/bin/python -m pytest`: 294 passed in 0.34s.
- Final `.venv/bin/python -m pytest`: **349 passed in 0.49s**. All previous 294
  tests preserved, plus 54 priority tests and one GUI priority test.
- `.venv/bin/python -m coopai.priority_demo`: exit 0. All four mission tasks are
  explicitly announced. Initial unknown fire priority is 0.58500 for both A/B.
  At evaluated tick 0 A has 0.63175 from local fire evidence and B remains 0.58500.
  At tick 3 A scores 0.76625 after observing escalation; B scores 0.67169 from old
  peer evidence. B receives severe-fire evidence at tick 5 and scores 0.76313.
  Deadline pressure changes with time. Reset matches complete priority/evidence
  histories: `priority_replay_equal=True assignments=NONE`.
- `.venv/bin/python -m coopai.gui --smoke-test --screenshot docs/task7_preview.png`:
  exit 0 with native Cocoa window; actual output:
  `GUI smoke passed: driver=cocoa tick=6 fire=severe B_battery=40% events=2 reset_ok=True`.
  Saved status/knowledge/messages/priority frames. The priority frame was visually
  inspected: B's known tasks, calculated scores, initial urgency, factors, sources,
  evaluation ticks and explanations were readable. Reset clears priority results
  and restores initial announcements; GUI rendering does not calculate scores.
- `.venv/bin/python -m compileall -q src tests`: exit 0.
- `.venv/bin/python -m pip check`: No broken requirements found.
- `git diff --check`: exit 0, but files remain untracked as in previous milestones;
  this does not audit untracked contents.

Coverage includes score/contribution bounds and normalization, deterministic outputs,
urgency/deadline/mission/evidence effects, confidence/freshness, unknown versus safe,
quality dominance/conflict conservatism, no duplicate confidence inflation, remote
provenance/age, explicit task visibility, selective announcements, mission updates,
version ordering, initial-urgency preservation, cache behavior, replaceable evaluator
owner contracts, controller-local outputs, JSON validation, replay and GUI display.
No new scoring component reads world/transport or private peer knowledge.

## Files

Added `src/coopai/priority.py`, `src/coopai/priority_demo.py`,
`tests/test_priority.py`, `docs/task_priority.md`, this record and four Task 7 PNGs.

Modified `src/coopai/interfaces.py`, `src/coopai/simulation.py`,
`src/coopai/scenario.py`, `src/coopai/gui.py`, `scenarios/four_room_disaster.json`,
`tests/test_gui.py`, `README.md`, `PROJECT_CONTEXT.md`,
`docs/system_specification.md`, `docs/simulation_engine.md`,
`docs/scenario_loading.md`, `docs/visualization.md`.
Core Task/Observation models, sensing, transport and prior test expectations remain
unchanged. The scenario's optional priority section adds configuration without
changing its geometry, agents, task definitions or scripted environmental events.

All Task 7 completion criteria are met. This is a transparent baseline with
illustrative weights/severity mappings, not proven novelty, optimized performance
or calibrated probabilities. Confidence/freshness comparability and conservative
tradeoff behavior require later experimental sensitivity studies. No task-message
announcement protocol or full dynamic task-arrival GUI metadata is implemented;
new objectives use explicit trusted announcements. Eligibility, suitability,
allocation, assignment/reassignment, navigation, execution, Gazebo and ML remain
unimplemented. Human mouse/trackpad/scaling review remains useful as before.

Ready for eligibility/suitability components based on known requirements, permitted
agent/peer state and these priorities. Stop for review.
