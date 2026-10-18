"""Mission supervisor: episode randomization, fault injection, gas-sensor physics, metrics logging.

It never sends task, map or hazard information to the agents. Its only outputs to robots are:
  * power-on configuration in each robot's customData (name, radio loss rate, noise scale, method)
  * the gas-sensor bus (channel 7) that emulates Agent B's chemical sensor from the plume model
  * fault effects applied to the world (power kill, commander radio loss, debris in a doorway)

Environment variables (all optional):
  FYP_SEED, FYP_CONDITION, FYP_METHOD, FYP_RUN_DIR, FYP_MAX_TIME, FYP_BATCH=1 (quit at the end)
  FYP_FAULT_DOOR (door for door_blocked), FYP_SNAPSHOTS="30,60" (sim times of 3D-view JPEGs),
  FYP_MOVIE=path.mp4 and FYP_MOVIE_SPEED (Webots movie recorder; GUI mode only), FYP_TELEMETRY=1
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller import Supervisor  # noqa: E402

from truth.episode import debris_pose, make_episode  # noqa: E402
from truth.gas_model import GasModel, wall_segments_from_world  # noqa: E402
from truth.metrics import score  # noqa: E402
from truth.motion_audit import MotionAudit  # noqa: E402

HIDE_Z = -20.0


class MissionSupervisor:
    def __init__(self) -> None:
        self.sv = Supervisor()
        self.ts = int(self.sv.getBasicTimeStep())
        seed_env = os.environ.get("FYP_SEED")
        self.seed = int(seed_env) if seed_env else int(time.time()) % 100000
        self.condition = os.environ.get("FYP_CONDITION", "random")
        self.method = os.environ.get("FYP_METHOD", "cbba")
        self.max_time = float(os.environ.get("FYP_MAX_TIME", "300"))
        self.batch = os.environ.get("FYP_BATCH", "0") == "1"
        run_dir = os.environ.get("FYP_RUN_DIR")
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(run_dir) if run_dir else ROOT / "runs" / f"{stamp}_s{self.seed}_{self.condition}_{self.method}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        if not self.batch:
            (ROOT / "runs").mkdir(exist_ok=True)
            (ROOT / "runs" / "latest.txt").write_text(str(self.run_dir), encoding="utf-8")
        self.events: list[dict] = []
        self.ep = make_episode(self.seed, self.condition, self.method, self.max_time,
                               fault_door=os.environ.get("FYP_FAULT_DOOR") or None)
        world_text = (ROOT / "worlds" / "facility.wbt").read_text(encoding="utf-8")
        self.gas = GasModel(wall_segments_from_world(world_text))
        self.gas_tx = self.sv.getDevice("gas_bus")
        self.gas_rng = random.Random(self.seed * 7 + 1)
        self.agents = {"A": self.sv.getFromDef("AGENT_A"), "B": self.sv.getFromDef("AGENT_B")}
        self.commander = self.sv.getFromDef("COMMANDER")
        self.alive = {"A": True, "B": True}
        self.gas_lag = 0.0
        self.traj = {"A": [], "B": []}
        self.true_dist = {"A": 0.0, "B": 0.0}
        self.last_pos = {}
        self.audit = MotionAudit(json.loads((ROOT/'config/blueprint.json').read_text())['doors'])

    # ----------------------------------------------------------- helpers
    def t(self) -> float:
        return self.sv.getTime()

    def log(self, ev: str, **kw) -> None:
        rec = {"t": round(self.t(), 3), "ev": ev, **kw}
        self.events.append(rec)
        with (self.run_dir / "supervisor_events.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        print("SUPERVISOR", json.dumps(rec), flush=True)

    def place(self, def_name: str, xy, yaw: float = 0.0, z: float = 0.0) -> None:
        node = self.sv.getFromDef(def_name)
        if node is None:
            raise RuntimeError(f"missing DEF {def_name}")
        node.getField("translation").setSFVec3f([float(xy[0]), float(xy[1]), z])
        node.getField("rotation").setSFRotation([0, 0, 1, float(yaw)])
        node.resetPhysics()

    # ----------------------------------------------------------- episode setup
    def setup(self) -> None:
        ep = self.ep
        for h in ep["hazards"]:
            self.place(h["id"], h["pos"], h["yaw"])
            if h["type"] == "fire":
                light = self.sv.getFromDef(f"{h['id']}_LIGHT")
                light.getField("on").setSFBool(True)
                light.getField("intensity").setSFFloat(3.5 * h["strength"])
        for v in ep["victims"]:
            self.place(v["id"], v["pos"], v["yaw"], z=1.27)
        for d in ep["distractors"]:
            z = 0.44 if d["id"] == "DISTRACT_3" else 0.0
            self.place(d["id"], d["pos"], d["yaw"], z=z)
        for i, door in enumerate(ep["blocked_doors"]):
            xy, yaw = debris_pose(door)
            self.place(f"DEBRIS_{i}", xy, yaw)
        for n in ("A", "B"):
            sx, sy = ep["spawns"][n]
            node = self.agents[n]
            node.getField("translation").setSFVec3f([sx, sy, 0.0975])
            node.getField("rotation").setSFRotation([0, 0, 1, ep["spawn_yaw"][n]])
            node.resetPhysics()
        cfg_common = {"run_dir": str(self.run_dir), "loss": ep["packet_loss"], "noise": ep["noise_scale"],
                      "perception_trace": os.environ.get('FYP_PERCEPTION_TRACE')=='1',
                      "method": ep["method"], "seed": self.seed, "max_time": self.max_time,
                      "telemetry": not self.batch or os.environ.get("FYP_TELEMETRY") == "1"}
        for n in ("A", "B"):
            self.agents[n].getField("customData").setSFString(json.dumps({**cfg_common, "name": n}))
        self.commander.getField("customData").setSFString(json.dumps({
            **cfg_common, "name": "CMD", "incident_report": ep["incident_report"]}))
        (self.run_dir / "truth.json").write_text(json.dumps(ep, indent=2), encoding="utf-8")
        self.log("episode_start", seed=self.seed, condition=self.condition, method=self.method,
                 hazards=[(h["type"], h["room"]) for h in ep["hazards"]],
                 victims=[v["room"] for v in ep["victims"]], blocked=ep["blocked_doors"],
                 loss=ep["packet_loss"], noise=ep["noise_scale"], faults=ep["faults"])

    # ----------------------------------------------------------- faults
    def apply_fault(self, f: dict) -> None:
        kind = f["kind"]
        if kind == "robot_killed":
            n = f["target"]
            self.agents[n].getField("controller").setSFString("powered_off")
            self.alive[n] = False
            self.log("fault_robot_killed", target=n)
        elif kind == "commander_cut":
            self.commander.getField("controller").setSFString("powered_off")
            self.log("fault_commander_cut")
        elif kind == "door_blocked":
            door = f["door"]
            slot = len(self.ep["blocked_doors"])
            xy, yaw = debris_pose(door)
            self.place(f"DEBRIS_{slot}", xy, yaw)
            self.log("fault_door_blocked", door=door)

    # ----------------------------------------------------------- gas sensor bus
    def gas_step(self) -> None:
        if not self.alive["B"]:
            return
        pos = self.agents["B"].getPosition()
        sources = [h for h in self.ep["hazards"] if h["type"] == "gas"]
        c = self.gas.concentration((pos[0], pos[1]), sources)
        noise = self.gas_rng.gauss(0.0, 6.0 * self.ep["noise_scale"]) + 0.04 * c * self.gas_rng.gauss(0, 1)
        self.gas_lag += (c - self.gas_lag) * 0.25
        reading = max(0.0, self.gas_lag + noise + 4.0)
        self.gas_tx.send(json.dumps({"ppm": round(reading, 2)}).encode())

    # ----------------------------------------------------------- end of episode
    def all_complete(self) -> bool:
        done = []
        for n in ("A", "B"):
            if not self.alive[n]:
                continue
            p = self.run_dir / f"status_{n}.json"
            try:
                done.append(json.loads(p.read_text(encoding="utf-8")).get("mission_complete", False))
            except (OSError, json.JSONDecodeError):
                done.append(False)
        return bool(done) and all(done)

    def finish(self, timed_out: bool) -> None:
        res = score(self.run_dir, self.ep, {"t": round(self.t(), 2), "timed_out": timed_out,
                                            "true_distance": {k: round(v, 2) for k, v in self.true_dist.items()}})
        res['safety'] = self.audit.summary()
        (self.run_dir/'safety.json').write_text(json.dumps(res['safety'],indent=2))
        (self.run_dir / "metrics.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
        (self.run_dir / "trajectory.json").write_text(json.dumps(self.traj), encoding="utf-8")
        self.log("episode_end", timed_out=timed_out, metrics=res)

    def run(self) -> None:
        self.sv.step(self.ts)
        self.setup()
        faults = sorted(self.ep["faults"], key=lambda f: f["t"])
        snaps = sorted(float(s) for s in os.environ.get("FYP_SNAPSHOTS", "").split(",") if s.strip())
        movie = os.environ.get("FYP_MOVIE")
        if movie:
            # needs a rendering GUI window; acceleration = playback speed-up of the video
            self.sv.movieStartRecording(str(Path(movie).resolve()), 1280, 720, 0, 90,
                                        int(os.environ.get("FYP_MOVIE_SPEED", "4")), True)
            self.log("movie_recording", path=movie)
        step = 0
        complete_since = None
        while self.sv.step(self.ts) != -1:
            step += 1
            now = self.t()
            self.audit.sample(self.agents)
            while snaps and now >= snaps[0]:
                path = self.run_dir / f"view_t{int(snaps.pop(0)):03d}.jpg"
                self.sv.exportImage(str(path), 90)
            while faults and now >= faults[0]["t"]:
                self.apply_fault(faults.pop(0))
            if step % 4 == 0:
                self.gas_step()
            if step % 16 == 0:
                for n, node in self.agents.items():
                    p = node.getPosition()
                    self.traj[n].append([round(now, 2), round(p[0], 3), round(p[1], 3)])
                    if n in self.last_pos:
                        self.true_dist[n] += math.dist(self.last_pos[n], p[:2])
                    self.last_pos[n] = p[:2]
            if step % 31 == 0:
                if self.all_complete():
                    complete_since = complete_since or now
                    if now - complete_since > 1.0:
                        self.finish(timed_out=False)
                        break
            if now >= self.max_time:
                self.finish(timed_out=True)
                break
        if movie:
            self.sv.movieStopRecording()
            while not self.sv.movieIsReady() and self.sv.step(self.ts) != -1:
                pass
            self.log("movie_saved", path=movie, failed=bool(self.sv.movieFailed()))
        if self.batch:
            self.sv.simulationQuit(0)
        else:
            while self.sv.step(self.ts) != -1:
                if step % 4 == 0:
                    self.gas_step()
                step += 1


if __name__ == "__main__":
    try:
        MissionSupervisor().run()
    except Exception:
        traceback.print_exc()
        (ROOT / "runs").mkdir(exist_ok=True)
        (ROOT / "runs" / "supervisor_crash.txt").write_text(traceback.format_exc(), encoding="utf-8")
        raise
