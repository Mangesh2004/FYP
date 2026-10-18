# Disaster response simulation team handover

Prepared on 18 October 2026. This is a working research prototype with a functioning live sensor demonstration. Before submission, reproduce it on a second computer and finish the current-version comparison experiments.

## Which folder to use

| Folder | Purpose |
| --- | --- |
| `webots_sim/` | Current 3D Webots simulation, robot controllers, detector, allocation methods, tests and web dashboards. Start here. |
| `manasi/` | Separate earlier deterministic Python simulation with a Pygame dashboard and extensive milestone notes. Its four-agent results and test counts do not describe the Webots project. |
| `_legacy_prototype/` | Historical prototype. Keep for reference; do not use as the final demo entry point. |
| `experiments/` | Saved detector evaluations, training artifacts, mission outputs, validation records and older comparisons. Results belong to specific source versions. |
| `run_demo.bat` | Existing Windows launcher for the two-robot mission and general dashboard. It contains a machine-specific Python path. |

The workspace root now has a Git repository for the complete handover. `manasi/` retains its separate local repository, while its source files are also included as a normal snapshot in the root repository. Its older remote was unavailable to the signed-in account during setup; no submodule access is required to clone this handover.

## What the current system does

The full Webots mission has two robots, a commander station, rooms and doors. Robot A has thermal sensing, and Robot B has gas sensing. Robots use camera detections, mapping, navigation and radio communication to inspect rooms and verify findings. Allocation implementations include static, greedy, cbba, central and learned modes; see `swarm/allocation.py` for their actual behavior.

The focused recording demo runs only Robot A in room R1. It searches, detects fire with the trained camera detector, gathers thermal and fresh visual evidence, confirms the finding and stops. This demonstrates sensing and verification. Use the two-robot mission to demonstrate cooperation and fault recovery.

Thermal sensing uses Webots light sensors as a heat proxy. Gas readings use a simulated plume and sensor bus. These are simulated devices, not calibrated physical temperature or chemical instruments. Camera inference uses Faster R-CNN MobileNetV3 weights. Confirmation and reporting do not extinguish fire, clear debris or physically rescue a person.

## Software and files needed on another computer

The current machine uses Windows, Webots R2025a and Python 3.12.9 in a Conda environment at `C:\Users\mange\anaconda3\envs\pytorch\python.exe`. A fresh environment recipe has not yet been validated on a second machine.

The companion `HANDOVER_ENVIRONMENT.txt` records selected installed distribution versions as a reference. It is not a portable lockfile: Torch GPU builds and drivers must match the destination machine. The usual runtime modules are numpy, torch, torchvision, cv2, PIL, fastapi, uvicorn and websockets. Testing adds pytest; analysis adds scipy and matplotlib; learning uses stable-baselines3, gymnasium and pettingzoo. The earlier Pygame simulator has its own `pyproject.toml`.

Keep these model files when transferring:

- `webots_sim/models/detector_frcnn_mnv3.pt` (about 76 MB): trained visual detector.
- `webots_sim/models/task_policy.pt`: deployed learned allocation policy.

Also keep `webots_sim/config/`, `worlds/`, `controllers/`, `swarm/`, `truth/`, `perception/`, `learning/`, `dashboard/`, `tools/` and `tests/`. Preserve relevant evaluation records under `experiments/`. Training data under `webots_sim/data/` is needed to reproduce detector training; confirm its completeness before claiming training reproducibility.

Before launching on another PC:

1. Install Webots and create a Python environment with the required packages.
2. Change `COMMAND` in controller `runtime.ini` files to that environment's Python executable. Search all controllers, including fire demo and supervisor controllers.
3. Change `WEBOTS` in `webots_sim/tools/run_webots.py` if the Webots executable is elsewhere.
4. Change `PY` and the Webots executable in `run_demo.bat` as needed.
5. Run the quick tests below, then a fresh native fire demo and the full mission.

Webots provides the Python `controller` module when it launches controllers. Do not substitute an unrelated PyPI package named controller.

## Run the sensor demonstration

Use PowerShell. Replace the project root and Python path for the destination PC. Run the simulation and dashboard in separate terminals, using the SAME run directory. Choose a NEW directory each time; the launcher intentionally refuses an existing nonempty output folder.

Terminal 1:

```powershell
Set-Location D:\FYP\webots_sim
$py = 'C:\Users\mange\anaconda3\envs\pytorch\python.exe'
& $py tools/run_fire_demo.py --gui --out runs/team_fire_01 --max-time 1800
```

Terminal 2:

```powershell
Set-Location D:\FYP\webots_sim
$py = 'C:\Users\mange\anaconda3\envs\pytorch\python.exe'
& $py -m dashboard.server --run runs/team_fire_01 --port 8770
```

Open `http://127.0.0.1:8770/fire`. The page shows camera detections, thermal readings and history, verification samples, lidar ranges, GPS, IMU, motion command and event history. Allow time for the detector to load. Check that the simulation timestamp changes before recording. After confirmation the robot deliberately holds position, while the GUI simulation may remain open.

The dashboard is a read-only observer. Its `--run` argument selects files to read; it does not launch or control Webots. Stop its terminal with Ctrl+C; close Webots when finished. If a port is already occupied, reuse its existing matching server or select another dashboard port and update the URL. Avoid starting duplicate servers.

For the session started during this handover, the output is `webots_sim/runs/record_20261018_233514`. Its `demo_result.json` records confirmation at simulation time 46.72 s, localization error about 0.766 m and zero contact steps. This is one focused demonstration, not a multi-seed benchmark.

## Run the two robot mission

After correcting the launcher paths, run `run_demo.bat` from the workspace root. It selects seed 3, cbba, the demo fault condition and a 300-second simulation horizon. It opens the general dashboard at `http://127.0.0.1:8765/`, which follows `webots_sim/runs/latest.txt`.

This is a different page from `/fire`. The fire page is designed for Robot A's focused demonstration, not all agents' specialty sensors.

The launcher uses a disposable world copy because Webots can save scene changes back into the opened world. Keep the original `worlds/facility.wbt` unchanged when running demos.

For recording the full mission's 3D view, the existing launcher supports `FYP_MOVIE`:

```powershell
$env:FYP_MOVIE = 'D:\FYP\experiments\team_demo.mp4'
.\run_demo.bat
Remove-Item Env:FYP_MOVIE
```

That recorder captures the Webots scene. Record the browser separately to include the sensor webpage. Check the resulting file before the presentation.

## Code guide

| Location inside webots_sim | Responsibility |
| --- | --- |
| `controllers/agent/agent.py` | Main robot behavior, inspection and verification flow. |
| `controllers/fire_demo_agent/` | Focused fire-search wrapper, live sensor telemetry and hold after confirmation. |
| `controllers/mission_supervisor/` | Episode setup, faults, gas simulation and observer metrics. |
| `controllers/fire_demo_supervisor/` | Focused demo scoring and saved confirmation evidence. |
| `controllers/commander/` | Commander communication and shared picture. |
| `swarm/perception.py` | Detector loading and inference. |
| `perception/detector.py` | Detector training and evaluation. |
| `swarm/fusion.py` | Evidence tracking and localization. |
| `swarm/mapping.py`, `planning.py`, `motion.py` | Mapping, route planning and motion safety. |
| `swarm/traffic.py` | Doorway coordination. |
| `swarm/tasks.py`, `allocation.py`, `radio.py` | Task state, allocation and radio protocol. |
| `swarm/live_sensors.py` | Live device snapshots for the dashboard. |
| `truth/` | Simulator truth and observer scoring; keep separate from agent decision inputs. |
| `dashboard/server.py`, `dashboard/static/` | Read-only API, websocket and browser pages. |
| `tools/run_fire_demo.py`, `run_webots.py` | Focused demo and Webots launcher. |
| `tools/run_batch.py`, `analyze.py` | Batch experiments and aggregate analysis. |
| `learning/` | Abstract learning environment, training and deployed policy runtime. |

## Verification status

Fresh check during this handover:

```powershell
Set-Location D:\FYP\webots_sim
& 'C:\Users\mange\anaconda3\envs\pytorch\python.exe' -m pytest -q -m 'not webots and not single_robot and not pair_robot and not hazard_webots and not fire_demo'
```

Result: **107 passed, 40 deselected, 6 warnings**, in 23.50 seconds. Warnings concern the learning environment's PettingZoo API conventions and a deprecated Pygame dependency. The 40 deselected tests include actual Webots physical and mission checks; they were not rerun during this handover.

For a fresh full check on the destination machine, close recording sessions first and run:

```powershell
python -m pytest -q --junitxml=runs/team_full_suite.xml
```

Run from `webots_sim/` using the configured environment. This launches slow physical episodes and requires Webots. Leave `FYP_HAZARD_DIR` and `FYP_FAULT_DIR` unset for fresh mission output; these variables can select existing experiment folders and the batch runner skips episodes with existing metrics. Save exact commands and XML with the submission.

Historical evidence:

- `experiments/fire_demo/verification.json` and `experiments/fire_demo_full_suite_final.xml`: saved 146-test result. The record explicitly notes reuse of accepted earlier mission episodes; it is not 146 newly executed episodes.
- `experiments/hazard_confirmation/verification.json`: saved hazard matrix covering eight randomized layouts and ten matrix runs, reporting 22 of 22 hazards confirmed and zero false confirmations. Its aggregate suite also reused accepted outputs.
- `experiments/detector/eval_report.json`: detector evaluation; the recorded test-layout mAP50 is about 0.857. Object detection accuracy is different from complete mission success.
- `experiments/pilot/summary.md`: older 10-seed method comparisons, including low completion rates and no demonstrated statistically significant cbba advantage. Do not present these as current-version performance after subsequent navigation and verification fixes.

## Work remaining before final submission

### Priority 1 Reproducible team setup

Replace or configure machine-specific paths, establish an installable dependency recipe, confirm model and data inclusion, and run both demos on a teammate's PC. The environment snapshot is a starting reference, not evidence that another PC can already run it.

The root repository covers the current Webots project, root launcher, handover files, model weights, detector data and earlier source snapshots. Generated runs, caches, videos and redundant experiment checkpoints are excluded. Future backups should commit and push from the workspace root; the separate `manasi/.git` does not cover its siblings.

### Priority 2 Fresh physical regression

Run the full suite against the exact version being shared. Preserve XML, run outputs, source hashes and model hashes. Investigate any failure rather than replacing fresh output with an older passing run.

### Priority 3 Updated research comparisons

Rerun paired multi-seed comparisons after the latest fixes. Include static, greedy, cbba and central; include learned allocation if learning performance is part of the submitted claim. Keep seed sets, conditions, horizons and model versions controlled. Report success, hazard recall, false confirmations, mission time, contact events and recovery, with uncertainty. The current saved pilot does not prove one method is superior.

Example batch command from `webots_sim/`, with a new output directory:

```powershell
python tools/run_batch.py --out ../experiments/team_final --seeds 0-9 --conditions normal loss20 commander_cut robot_killed door_blocked --methods static greedy cbba central --workers 1 --max-time 600
```

This is substantial simulation work, not a quick smoke test. Review fault-door selection when comparing blocked-door runs; the fault tests deliberately select a door used by the baseline run. Do not casually mix different fault configurations across methods. Check `python tools/analyze.py --help` for analysis arguments.

### Priority 4 Presentation and scope agreement

Agree whether the final deliverable is the Webots simulator alone or also the earlier 2D implementation. Explain the two-robot full mission and one-robot recording demo correctly. Prepare an architecture diagram, short demonstration recording, results tables, references and limitations. Clearly separate simulation findings from real-world deployment claims.

If adaptive priority preemption or the older Tasks 13 to 15 remain in the official requirements, reconcile them explicitly. `manasi/README.md` says they were deferred in that implementation; work in the sibling Webots project does not automatically complete those milestones.

## Suggested team ownership

- Setup owner: portable paths, dependency recipe, clean-machine launch and transfer package.
- Simulation owner: full physical tests, navigation, faults and recovery investigation.
- Evaluation owner: fresh paired comparisons, detector/model provenance and result tables.
- Presentation owner: recording, architecture explanation, report and scope checklist.

## Troubleshooting

- Empty sensor page: check that Webots is playing, the controller interpreter path is valid, and the dashboard follows the exact current output directory.
- No detections: check model presence, detector startup logs and camera timestamps. Missing weights can leave the detector unavailable.
- Launcher rejects output directory: choose a fresh name; do not mix old and live telemetry.
- Apparently instantaneous experiment success: check whether the batch runner reused existing `metrics.json` files.
- Browser reachable but frozen: confirm simulation time advances; a dashboard can display saved files after Webots closes.
- Different test counts: distinguish the earlier 2D project, historical source versions, quick tests and slow simulation markers.

Before sharing, include this file, `HANDOVER_ENVIRONMENT.txt`, the code and model files, selected validation evidence, and the recordings. Teammates should start with the focused demo, then the two-robot mission, then the full regression suite.
