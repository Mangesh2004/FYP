# Task 3 verification

Environment: macOS Darwin arm64, Python 3.14.2, pytest 9.1.1.
Reused the existing virtual environment. No new dependencies, commits or pushes.

## Actual verification

- Baseline `.venv/bin/python -m pytest`: 103 passed in 0.04s.
- Final `.venv/bin/python -m pytest`: **176 passed in 0.12s**.
  All 103 earlier tests pass unchanged, plus 73 scenario/geometry/demo tests.
- `.venv/bin/python -m coopai.scenario_demo`: exit 0. Reported four rooms plus
  corridor, five openings, eight directed connections and 25 wall segments.
  Four agents and four pending/unassigned tasks loaded. At tick 3 room-1 fire
  became severe; at tick 5 B's battery became 0.4. Eight-tick reset replay
  matched full world snapshots, event trace and seeded diagnostic draws:
  `replay_equal=True seed=42 processed_ticks=8`.
- `/Volumes/Work/FYP/.venv/bin/python -m coopai.scenario_demo
  /Volumes/Work/FYP/scenarios/four_room_disaster.json --steps 8` from `/private/tmp`:
  exit 0, confirming installed module execution with an explicit external path.
- `.venv/bin/python -m coopai.demo`: exit 0; earlier engine demo still works.
- `.venv/bin/python -m compileall -q src tests`: exit 0.
- `.venv/bin/python -m pip check`: No broken requirements found.
- `git diff --check`: exit 0; all repository files remain untracked, so this command
  does not audit their content. Git history/configuration preserved.

## Files

Added:

- `src/coopai/geometry.py`
- `src/coopai/scenario.py`
- `src/coopai/scenario_demo.py`
- `scenarios/four_room_disaster.json`
- `tests/test_scenario.py`
- `docs/scenario_loading.md`
- `docs/task_3_verification.md`

Modified: `PROJECT_CONTEXT.md`, `README.md`, `docs/system_specification.md`,
`docs/simulation_engine.md`, `scenarios/README.md`.
Existing core models, agent interfaces, world models, engine, demo and tests were
preserved unchanged. No ground-truth interface correction was necessary; new tests
verify hidden conditions/tasks/events are absent from all supplied agent views.

All Task 3 completion criteria are met. Geometry is axis-aligned and abstract,
spawns are location-based, all initial tasks are pending, and task coverage is only
a setup sanity check. Event effects currently support condition updates/additions
and battery replacement; failure, urgency changes, task arrival and passage blocking
need explicit additional effect variants later. GUI, movement, perception, task
allocation, knowledge fusion and Gazebo integration remain unimplemented.

Ready for Task 4: first interactive 2D visualization using FloorPlan, immutable
world snapshots, clock, lifecycle controls and event trace. Stop for review.
