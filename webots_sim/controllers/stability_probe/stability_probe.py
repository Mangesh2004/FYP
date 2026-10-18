"""Single-robot measurement fixture. Supervisor truth is used ONLY to score motion.

Navigation uses the production Agent methods, its encoders/IMU/GPS and lidar.
No allocation, detector, commander, or second robot participates in this test.
"""
import json
import math
import os
from pathlib import Path
import random
import sys
import traceback

import numpy as np
from controller import Supervisor

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from controllers.agent.agent import Agent
from swarm.mapping import OccupancyGrid
from swarm.planning import DWA, GlobalPlanner


def main():
    cfg = json.loads(Path(os.environ["FYP_STABILITY_CONFIG"]).read_text())
    out = Path(cfg["out"])
    sv = Supervisor()
    ts = int(sv.getBasicTimeStep())
    node = sv.getSelf()
    a = Agent.__new__(Agent)
    a.robot, a.ts, a.dt = sv, ts, ts / 1000
    a.noise, a.rng = float(cfg.get("noise", 1.0)), random.Random(0)
    np.random.seed(0)
    a._init_devices()
    # Measure the stock low-mounted sonars before changing the production controller.
    sonars = [sv.getDevice(f"so{i}") for i in range(16)]
    for sensor in sonars:
        sensor.enable(ts)
    a.grid = OccupancyGrid([-12, -8, 12, 8], 0.1)
    a.planner, a.dwa = GlobalPlanner(a.grid), DWA()
    a.name = "A"
    a.run_dir, a.events_path = out, out / "navigation.jsonl"
    a.path, a.goal, a.job = None, None, {}
    a.last_plan_t, a.wp, a.stuck_count, a.recover_until = -1e9, 0, 0, -1.0
    a.progress_ref = (0.0, None)
    a.odom_dist = 0.0
    command = [0.0, 0.0]
    def drive(v, w):
        Agent.drive(a, v, w)
        command[:] = a.last_command
    a.drive = drive
    sv.step(ts)
    a.init_pose()
    samples, goals, phase = [], list(cfg.get("goals", [])), "settle"
    ranges = a.scan()
    step, nav_result = 0, "moving"
    initial = list(node.getPosition())
    log = (out / "samples.jsonl").open("w", encoding="utf-8")
    while sv.step(ts) != -1:
        t = sv.getTime()
        step += 1
        a.update_pose()
        if step % 4 == 0:
            ranges = a.scan()
            if step % 8 == 0:  # same mapping cadence as the production controller
                a.grid.integrate_scan(*a.pose, ranges, a.angles, a.lidar_max)
        if t < 2:
            a.drive(0, 0)
        elif cfg["mode"] == "primitives":
            # Repeatable motor inputs isolate chassis physics from autonomous navigation.
            schedule = [(4, 0.3, 0, "straight"), (1, 0, 0, "stop"),
                        (4, 0, 0.8, "turn"), (1, 0, 0, "stop"),
                        (3, -0.15, 0, "reverse"), (2, 0, 0, "stop")]
            u = t - 2
            phase = "finished"
            for duration, v, w, label in schedule:
                if u < duration:
                    phase = label
                    a.drive(v, w)
                    break
                u -= duration
            else:
                a.drive(0, 0)
        elif cfg["mode"] == "spin":
            phase = "turn"
            if "spin_acc" not in a.job:
                a.start_spin(math.pi)
            if a.spin_step():
                phase = "finished"
        elif cfg["mode"] == "recover":
            phase = "recover"
            if a.goal is None:
                a.set_goal(cfg["goals"][0])
                a.recover_until = t + 1.2
            nav_result = a.nav_step(ranges)
            if nav_result == "arrived":
                goals.clear()
                phase = "finished"
        else:
            phase = "navigate"
            if a.goal is None and goals:
                a.set_goal(goals[0])
            if goals:
                nav_result = a.nav_step(ranges)
                if nav_result == "arrived":
                    goals.pop(0)
                    a.goal = None
            else:
                a.drive(0, 0)
                phase = "finished"
        pos = list(node.getPosition())
        rot = node.getOrientation()
        tilt = math.acos(max(-1.0, min(1.0, rot[8])))
        contacts = [list(c.point) for c in node.getContactPoints(True)]
        # Wheel-floor contacts are expected. Above-floor contacts indicate an obstacle hit.
        hits = [p for p in contacts if p[2] > 0.035]
        sample = {"t": round(t, 3), "phase": phase, "position": pos, "tilt": tilt,
                  "pose_estimate": a.pose[:], "command": command[:], "contacts_above_floor": hits,
                  "lidar_min": float(np.nanmin(ranges)), "nav_result": nav_result,
                  "low_points": a.low_points.tolist(),
                  "sonar_raw": [s.getValue() for s in sonars]}
        log.write(json.dumps(sample) + "\n")
        samples.append(sample)
        if phase == "finished" or t >= cfg["duration"]:
            break
    a.drive(0, 0)
    log.close()
    stable = [s for s in samples if s["t"] >= 2]
    result = {"case": cfg["case"], "mode": cfg["mode"], "sim_seconds": samples[-1]["t"],
              "samples": len(samples), "initial": initial, "final": samples[-1]["position"],
              "max_tilt_deg": math.degrees(max(s["tilt"] for s in stable)),
              "z_range": [min(s["position"][2] for s in stable), max(s["position"][2] for s in stable)],
              "contact_steps": sum(bool(s["contacts_above_floor"]) for s in stable),
              "goals_remaining": len(goals), "stuck_recoveries": a.stuck_count,
              "safety_stops": a.safety_stops,
              "completed": phase == "finished"}
    (out / "result.json").write_text(json.dumps(result, indent=2))
    if cfg.get("gui"):
        sv.exportImage(str(out / "final.png"), 90)
    print("STABILITY_RESULT " + json.dumps(result), flush=True)
    sv.simulationQuit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(traceback.format_exc(), flush=True)
        raise
