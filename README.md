# Cooperative disaster response simulation

A Webots research prototype for robot inspection, camera detection, sensor verification, task allocation and fault recovery, with live browser dashboards.

Start with [the team handover](TEAM_HANDOVER.md) for setup, commands, architecture, verification evidence and remaining work. [The environment snapshot](HANDOVER_ENVIRONMENT.txt) records the working PC's package versions; it is not a validated cross-platform lockfile.

## Project contents

- `webots_sim/`: current two-robot 3D simulation, focused one-robot fire demonstration, controllers, models, training data, tests and dashboards.
- `manasi/`: source snapshot of the earlier four-agent deterministic 2D simulator. Its existing local repository remains separate; a normal clone of this repository includes the snapshot without needing a submodule.
- `_legacy_prototype/`: historical implementation retained for reference.
- `experiments/`: selected saved reports, figures, metrics tables and verification scripts. Raw episode directories, local logs and redundant training checkpoints are excluded.
- `run_demo.bat`: existing full mission launcher. Update its Python and Webots paths before using it on another PC.

The trained detector and allocation policy are included under `webots_sim/models/`. The detector dataset is included under `webots_sim/data/`. A clone therefore downloads binary research assets as well as source.

## Quick start on Windows

Install Webots and configure a Python environment. Update controller `runtime.ini` interpreter paths and the Webots executable path in `webots_sim/tools/run_webots.py`; see the handover for details.

From `webots_sim/`, in two separate terminals using the same Python environment:

```powershell
python tools/run_fire_demo.py --gui --out runs/team_fire_01 --max-time 1800
```

```powershell
python -m dashboard.server --run runs/team_fire_01 --port 8770
```

Open http://127.0.0.1:8770/fire for the focused camera, thermal, lidar, GPS and IMU demonstration. Choose a fresh output directory for each run. The two-robot full mission uses `run_demo.bat` and http://127.0.0.1:8765/ instead.

## Tests and status

Run from `webots_sim/`:

```powershell
python -m pytest -q -m 'not webots and not single_robot and not pair_robot and not hazard_webots and not fire_demo'
```

The handover check passed 107 tests, with 40 simulation tests excluded. Run `python -m pytest -q` for the full suite after Webots is configured; it launches physical episodes and takes longer. These counts describe the handover check, not an automatic guarantee for another environment.

Before final submission: validate installation on a teammate's machine, run fresh physical regressions, update paired multi-seed comparisons after the recent fixes, and finish presentation materials. Older pilot comparisons do not establish CBBA superiority. Sensor readings are simulated; reporting a hazard does not mitigate it.
