"""Decentralized agent controller. Webots starts one process of this file per robot.

Inputs used: wheel encoders, IMU, GPS, lidar, camera, own specialty sensor (thermal light sensors
or gas receiver), and radio packets. No supervisor API and no ground-truth files are used.
"""
from __future__ import annotations

import base64
import json
import math
import random
import sys
import traceback
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller import Robot  # noqa: E402

from swarm.allocation import PEER_TIMEOUT, make_allocator  # noqa: E402
from swarm.blueprint import Blueprint  # noqa: E402
from swarm.fusion import EvidenceTracker, localize  # noqa: E402
from swarm.io_util import atomic_write_text  # noqa: E402
from swarm.mapping import OccupancyGrid  # noqa: E402
from swarm.motion import PROXIMITY_COUNT, proximity_points, safe_command  # noqa: E402
from swarm.planning import DWA, GlobalPlanner  # noqa: E402
from swarm.radio import Radio  # noqa: E402
from swarm.tasks import TaskBook  # noqa: E402
from swarm.traffic import DoorwayTraffic  # noqa: E402

WHEEL_R = 0.0975
AXLE = 0.33
MAX_WHEEL = 12.0
THERMAL_CONFIRM = 0.3
GAS_CONFIRM_PPM = 60.0
COMMANDER_TIMEOUT = 4.0
RALLY = {"A": [-6.0, 0.5], "B": [-6.0, -0.5]}


class Agent:
    def __init__(self) -> None:
        self.robot = Robot()
        self.ts = int(self.robot.getBasicTimeStep())
        self.dt = self.ts / 1000.0
        self.cfg = self._wait_config()
        self.name = self.cfg["name"]
        self.run_dir = Path(self.cfg["run_dir"])
        self.noise = float(self.cfg.get("noise", 1.0))
        self.rng = random.Random(int(self.cfg.get("seed", 0)) * 31 + ord(self.name))
        self.telemetry = bool(self.cfg.get("telemetry", True))
        self._init_devices()
        self.bp = Blueprint()
        self.traffic = DoorwayTraffic(self.name, self.bp.doors, self.cfg.get('peer_ids',['A','B']))
        self.last_traffic_t = -1e9
        self.nav_target = None
        self.grid = OccupancyGrid(self.bp.bounds, 0.1)
        self.planner = GlobalPlanner(self.grid)
        self.dwa = DWA()
        self.book = TaskBook(self.bp)
        self.method = self.cfg.get("method", "cbba")
        self.alloc = make_allocator(self.method, self.name, self.capability, self.book)
        self.radio = Radio(self.tx, self.rx, self.name, float(self.cfg.get("loss", 0.0)), self.rng.randrange(1 << 30))
        self.tracker = EvidenceTracker()
        self.detector = None
        self.peers: dict[str, dict] = {}
        self.cmd_heard = -1e9
        self.mode = "starting"
        self.state = "IDLE"
        self.task: str | None = None
        self.job: dict = {}
        self.path: list | None = None
        self.wp = 0
        self.goal = None
        self.last_plan_t = -1e9
        self.progress_ref = (0.0, None)
        self.stuck_count = 0
        self.recover_until = -1.0
        self.complete = False
        self.odom_dist = 0.0
        self.thermal_min = None
        self.heat = 0.0
        self.heat_bearing = 0.0
        self.gas_ppm = 0.0
        self.specialty_t = -1e9
        self.last_dets: list[dict] = []
        self.last_rgb = None
        self.inspected: list[str] = []
        self.events_path = self.run_dir / f"agent_{self.name}.jsonl"
        self.log("config", method=self.method, capability=self.capability, loss=self.radio.base_loss)

    # ------------------------------------------------------------------ setup
    def _wait_config(self) -> dict:
        while True:
            data = self.robot.getCustomData()
            if data:
                return json.loads(data)
            if self.robot.step(self.ts) == -1:
                sys.exit(0)

    def _init_devices(self) -> None:
        r = self.robot
        self.lm, self.rm = r.getDevice("left wheel"), r.getDevice("right wheel")
        for m in (self.lm, self.rm):
            m.setPosition(float("inf"))
            m.setVelocity(0.0)
        self.le, self.re = r.getDevice("left wheel sensor"), r.getDevice("right wheel sensor")
        self.le.enable(self.ts)
        self.re.enable(self.ts)
        self.imu = r.getDevice("imu")
        self.imu.enable(self.ts)
        self.gps = r.getDevice("gps")
        self.gps.enable(self.ts)
        self.lidar = r.getDevice("lidar")
        self.lidar.enable(self.ts * 4)
        self.n_rays = self.lidar.getHorizontalResolution()
        self.lidar_fov = self.lidar.getFov()
        self.lidar_max = self.lidar.getMaxRange()
        # Webots orders lidar rays from the left edge of the field of view clockwise
        self.angles = self.lidar_fov / 2 - (np.arange(self.n_rays) + 0.5) * self.lidar_fov / self.n_rays
        self.cam = r.getDevice("camera")
        self.cam.enable(self.ts * 8)
        self.cam_w, self.cam_h, self.cam_fov = self.cam.getWidth(), self.cam.getHeight(), self.cam.getFov()
        self.proximity = [r.getDevice(f"proximity_{i:02d}") for i in range(PROXIMITY_COUNT)]
        for sensor in self.proximity:
            sensor.enable(self.ts)
        self.low_points = np.zeros((0, 2))
        self.motion_ranges = np.full(self.n_rays, np.nan, dtype=np.float32)
        self.motion_scan_t = -1e9
        self.tilt = 0.0
        self.last_command = (0.0, 0.0)
        self.safety_stops = 0
        self.tx = r.getDevice("radio_tx")
        self.rx = r.getDevice("radio_rx")
        self.rx.enable(self.ts)
        names = [r.getDeviceByIndex(i).getName() for i in range(r.getNumberOfDevices())]
        self.thermal = []
        self.gas_rx = None
        if "thermal_front" in names:
            self.capability = "thermal"
            for n, yaw in (("thermal_front", 0.0), ("thermal_left", math.pi / 2),
                           ("thermal_back", math.pi), ("thermal_right", -math.pi / 2)):
                s = r.getDevice(n)
                s.enable(self.ts * 2)
                self.thermal.append((s, yaw))
        elif "gas_sensor" in names:
            self.capability = "gas"
            self.gas_rx = r.getDevice("gas_sensor")
            self.gas_rx.enable(self.ts)
        else:
            self.capability = "none"

    # ------------------------------------------------------------------ logging
    def now(self) -> float:
        return self.robot.getTime()

    def log(self, ev: str, **kw) -> None:
        rec = {"t": round(self.now(), 3), "ev": ev, "agent": self.name, **kw}
        with self.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        print(f"AGENT_{self.name}", json.dumps(rec), flush=True)

    def write_status(self) -> None:
        st = {"t": round(self.now(), 2), "name": self.name, "pose": [round(v, 3) for v in self.pose],
              "state": self.state, "task": self.task, "mode": self.mode, "method": self.method,
              "mission_complete": self.complete, "odom_dist": round(self.odom_dist, 2),
              "inspected": self.inspected, "done": sorted(self.book.done), "findings": list(self.book.findings.values()),
              "blocked": sorted(self.book.blocked), "heat": round(self.heat, 4), "gas_ppm": round(self.gas_ppm, 1),
              "nav_target": self.nav_target, "goal": self.goal,
              "traffic": dict(state=self.traffic.state,request=self.traffic.request),
              "last_command": self.last_command,
              "peers": {k: {"pos": v["pos"], "alive": v["alive"], "task": v.get("task")} for k, v in self.peers.items()},
              **self.radio.stats()}
        atomic_write_text(self.run_dir / f"status_{self.name}.json", json.dumps(st))

    # ------------------------------------------------------------------ sensing
    def init_pose(self) -> None:
        g = self.gps.getValues()
        self.pose = [g[0], g[1], self.imu.getRollPitchYaw()[2]]
        self.enc = (self.le.getValue(), self.re.getValue())

    def update_pose(self) -> None:
        el, er = self.le.getValue(), self.re.getValue()
        dl, dr = (el - self.enc[0]) * WHEEL_R, (er - self.enc[1]) * WHEEL_R
        self.enc = (el, er)
        ds = (dl + dr) / 2
        self.odom_dist += abs(ds)
        roll, pitch, imu_yaw = self.imu.getRollPitchYaw()
        if not all(math.isfinite(v) for v in (roll, pitch, imu_yaw)):
            self.tilt = float("nan")
            return  # retain last valid pose and let drive() stop on invalid attitude
        self.tilt = math.acos(max(-1.0, min(1.0, math.cos(roll) * math.cos(pitch))))
        yaw = imu_yaw + self.rng.gauss(0, 0.003 * self.noise)
        x = self.pose[0] + ds * math.cos(yaw)
        y = self.pose[1] + ds * math.sin(yaw)
        g = self.gps.getValues()
        gx, gy = g[0] + self.rng.gauss(0, 0.02 * self.noise), g[1] + self.rng.gauss(0, 0.02 * self.noise)
        self.pose = [x + 0.08 * (gx - x), y + 0.08 * (gy - y), yaw]

    def scan(self) -> np.ndarray:
        r = np.array(self.lidar.getRangeImage(), dtype=np.float32)
        r = r + np.random.normal(0, 0.01 * self.noise, r.shape).astype(np.float32)
        self.motion_ranges = r
        self.motion_scan_t = self.now()
        # An invalid low scan fails closed in drive(); it is never treated as free space.
        try:
            self.low_points = proximity_points([s.getValue() for s in self.proximity])
        except ValueError:
            self.low_points = np.array([[np.nan, np.nan]])
        return r

    def read_thermal(self) -> None:
        vals = np.array([s.getValue() for s, _ in self.thermal]) + np.random.normal(0, 0.004 * self.noise, len(self.thermal))
        self.thermal_min = vals if self.thermal_min is None else np.minimum(self.thermal_min, vals)
        heat = np.clip(vals - self.thermal_min, 0, None)
        self.heat = float(heat.sum())
        yaws = np.array([y for _, y in self.thermal])
        self.heat_bearing = float(math.atan2((heat * np.sin(yaws)).sum(), (heat * np.cos(yaws)).sum())) if self.heat > 0 else 0.0
        self.specialty_t = self.now()

    def read_gas(self) -> None:
        while self.gas_rx.getQueueLength() > 0:
            try:
                self.gas_ppm = float(json.loads(self.gas_rx.getBytes().decode())["ppm"])
                self.specialty_t = self.now()
            except (ValueError, KeyError, UnicodeDecodeError):
                pass
            self.gas_rx.nextPacket()

    def specialty_value(self) -> float:
        return self.heat if self.capability == "thermal" else self.gas_ppm

    def obstacles_robot_frame(self, ranges: np.ndarray) -> np.ndarray:
        r = ranges[::2]
        a = self.angles[::2]
        ok = np.isfinite(r) & (r < 2.5) & (r > 0.12)
        pts = np.stack([r[ok] * np.cos(a[ok]), r[ok] * np.sin(a[ok])], axis=1)
        pts = np.vstack([pts, self.low_points])
        x, y, yaw = self.pose
        st = self.grid.static_points(x, y, 2.5)
        if len(st):
            c, s = math.cos(-yaw), math.sin(-yaw)
            dx, dy = st[:, 0] - x, st[:, 1] - y
            pts = np.vstack([pts, np.stack([c * dx - s * dy, s * dx + c * dy], axis=1)])
        return pts

    # ------------------------------------------------------------------ perception
    def load_detector(self) -> None:
        try:
            from swarm.perception import DEFAULT_WEIGHTS, Detector
            if DEFAULT_WEIGHTS.exists():
                self.detector = Detector(DEFAULT_WEIGHTS, score_thresh=0.55)
                self.log("detector_loaded", weights=str(DEFAULT_WEIGHTS.name), device=str(self.detector.device))
            else:
                self.log("detector_unavailable", reason="weights not trained yet")
        except Exception as exc:  # noqa: BLE001
            self.log("detector_error", error=repr(exc))
            self.detector = None

    def perceive(self, ranges: np.ndarray) -> None:
        if not self.detector or not self.detector.available:
            return
        from swarm.perception import bgra_to_rgb
        img = self.cam.getImage()
        if not img:
            return
        rgb = bgra_to_rgb(img, self.cam_w, self.cam_h)
        self.last_rgb = rgb
        dets = self.detector.detect(rgb)
        self.last_dets = dets
        t = self.now()
        localized = []
        for d in dets:
            loc = localize(d, self.pose, ranges, self.angles, self.cam_w, self.cam_fov, self.lidar_max, self.cam_h)
            if loc is None:
                continue
            kind, pos, _, rng_m = loc
            localized.append(dict(kind=kind,pos=pos,range=rng_m))
            if rng_m > 6.5:
                continue
            tr = self.tracker.add(kind, pos, d["score"], t)
            if kind=='victim' and d['box'][3]-d['box'][1]>=self.cam_h*.5 and tr.get('detail_t')!=t:
                tr['detail_t']=t
                tr['detailed']=[q for q in tr.get('detailed',[]) if t-q['t']<=2.0]+[dict(t=t,pos=pos)]
            if self.state == "VERIFY" and self.job.get("hazard") == kind and math.dist(pos, self.job["pos"]) < 1.8:
                # Camera bearing is current evidence; an old map estimate may
                # point at a wall or put the real object outside the camera FOV.
                self.job['aim'] = self.pose[2] + loc[2]
                self.job['seen_t'] = t
                self.job['visual_pos'] = pos
                if self.job.get('hit_t') != t:
                    self.job["visual_hits"] = self.job.get("visual_hits", 0) + 1
                    self.job['hit_t'] = t
                    if self.job['phase']=='measure':
                        self.job.setdefault('visual_positions',[]).append(pos)
                        if d['box'][3]-d['box'][1]>=self.cam_h*.5:
                            self.job['visual_quality_hits']=self.job.get('visual_quality_hits',0)+1
                            self.job.setdefault('quality_positions',[]).append(pos)
                            self.job['quality_seen_t']=t
        if self.cfg.get('perception_trace') and self.state=='VERIFY':
            import cv2
            folder=self.run_dir/'verification_frames'
            folder.mkdir(exist_ok=True)
            frame=folder/f'{self.name}_{t:.3f}.jpg'
            cv2.imwrite(str(frame),cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR))
            self.log('verification_frame',pose=self.pose,phase=self.job['phase'],target=self.job['pos'],
                     hazard=self.job['hazard'],sensor=self.specialty_value(),detections=dets,
                     localized=localized,image=str(frame))
        for tr in self.tracker.ready():
            room = self.bp.room_of(*tr["pos"])
            if tr["kind"] == "victim":
                detailed=tr.get('detailed',[])
                if len(detailed)<3 or t-detailed[-1]['t']>=1.0:
                    continue
                pos=np.median([q['pos'] for q in detailed],axis=0).tolist()
                tr['reported']=True
                f = {"kind": "victim", "pos": [round(v, 2) for v in pos], "confirmed": True, "by": self.name,
                     "t": round(t, 2), "room": self.bp.room_of(*pos), "evidence": f"detailed camera frames x{len(detailed)}"}
                if self.book.add_finding(f):
                    self.log("finding", **f)
            else:
                tr['reported']=True
                f = {"kind": tr["kind"], "pos": [round(v, 2) for v in tr["pos"]], "confirmed": False,
                     "by": self.name, "t": round(t, 2), "room": room, "evidence": f"visual x{tr['n']}"}
                self.book.add_finding(f)
                tid = self.book.add_verify(tr["kind"], tr["pos"], room, self.name, t)
                if tid:
                    self.log("suspected_hazard", kind=tr["kind"], pos=f["pos"], room=room, verify_task=tid)

    # ------------------------------------------------------------------ radio
    def handle_radio(self) -> None:
        now = self.now()
        for msg in self.radio.poll():
            src = msg.get("src")
            typ = msg.get("type")
            if src == "CMD":
                self.cmd_heard = now
                if typ == "cmd":
                    for room, u in msg.get("priorities", {}).items():
                        self.book.urgency[room] = u
                    self.alloc.on_command(msg, now)
                continue
            if typ == 'traffic':
                self.traffic.receive(msg,now)
                continue
            if typ == "hb":
                pose = msg.get("pose")
                if pose:
                    # Moving peers are observed by low range sensors. A 1-second-old
                    # heartbeat must not leave a solid ghost in the robot's path.
                    self.grid.clear_static(f"peer:{src}")
                self.peers[src] = {"pos": msg.get("pose", [0, 0, 0])[:2], "cap": msg.get("cap"), "heard": now,
                                   "task": msg.get("task"), "state": msg.get("state"), "alive": True}
                new = self.book.merge(msg)
                for tid in new:
                    self.log("learned_task", task=tid, from_agent=src)
                self.alloc.on_peer(msg, now)
            elif typ == "map":
                self.grid.apply_peer_delta(msg)

    def send_heartbeat(self) -> None:
        msg = {"type": "hb", "t": round(self.now(), 2), "pose": [round(v, 2) for v in self.pose], "cap": self.capability,
               "state": self.state, "task": self.task, "mode": self.mode, **self.book.gossip(), **self.alloc.outgoing()}
        self.radio.send(msg)

    def send_map(self) -> None:
        d = self.grid.encode_delta()
        if d:
            self.radio.send({"type": "map", **d})

    def update_links(self) -> None:
        now = self.now()
        for k, p in self.peers.items():
            alive = now - p["heard"] < PEER_TIMEOUT
            if p["alive"] and not alive:
                self.log("peer_lost", last_heard=round(p["heard"], 2))
                if p.get("pos"):
                    # a silent robot is most likely stopped where it last reported; its obstacle stays
                    self.log("peer_obstacle_marked", peer=k, pos=[round(v, 2) for v in p["pos"][:2]])
                    self.grid.set_static(f"peer:{k}", *p['pos'][:2], .3)
            p["alive"] = alive
        linked = now - self.cmd_heard < COMMANDER_TIMEOUT
        mode = "commander_linked" if linked else "peer_only"
        if mode != self.mode:
            self.log("mode", mode=mode)
            self.mode = mode

    def alive_caps(self) -> set[str]:
        caps = {self.capability}
        caps |= {p["cap"] for p in self.peers.values() if p["alive"] and p.get("cap")}
        return caps

    # ------------------------------------------------------------------ motion
    def drive(self, v: float, w: float) -> None:
        requested = (v, w)
        if self.now() - self.motion_scan_t > 0.25 or not math.isfinite(self.tilt) or self.tilt > math.radians(12):
            v, w = 0.0, 0.0
        else:
            v, w = safe_command(v, w, self.obstacles_robot_frame(self.motion_ranges))
        self.last_command = (v, w)
        if requested != (v, w):
            self.safety_stops += 1
            if hasattr(self, "dwa"):
                self.dwa.v, self.dwa.w = v, w
        wl = (v - w * AXLE / 2) / WHEEL_R
        wr = (v + w * AXLE / 2) / WHEEL_R
        s = max(1.0, abs(wl) / MAX_WHEEL, abs(wr) / MAX_WHEEL)
        self.lm.setVelocity(wl / s)
        self.rm.setVelocity(wr / s)

    def set_goal(self, xy) -> None:
        self.goal = list(xy)
        self.path = None
        self.wp = 0
        self.last_plan_t = -1e9
        if getattr(self, "job", None):
            self.job["plan_failures"] = 0
        self.progress_ref = (self.now(), self.pose[:2])

    def nav_step(self, ranges: np.ndarray) -> str:
        if hasattr(self,'traffic'):
            target = self.service_traffic()
            if self.traffic.state=='blocked':
                self.drive(0,0)
                return 'failed'
            effective = target if target is not None else self.goal
            if effective is None:
                self.drive(0,0)
                return 'moving'
            if self.nav_target is None or math.dist(effective,self.nav_target)>.05:
                self.path = None
                self.last_plan_t = -1e9
                self.nav_target = list(effective)
                self.progress_ref = (self.now(),self.pose[:2])
            result = self._nav_step(ranges,effective)
            if target is not None:
                if result=='failed':
                    self.traffic.reject_target(effective)
                    self.path=None
                    self.last_plan_t=-1e9
                    if self.traffic.state=='blocked':
                        return 'failed'
                if result=='arrived':
                    self.progress_ref = (self.now(),self.pose[:2])
                return 'moving'
            return result
        return self._nav_step(ranges,self.goal)

    def service_traffic(self):
        remaining = self.path[self.wp:] if self.path else None
        goal = self.goal if self.state in ('INSPECT','VERIFY','RETURN') else None
        if self.job.get('phase') in ('spin','face','measure','search'):
            goal = None
        target = self.traffic.update(self.pose,goal,remaining,self.now(),clear=self.clear_staging_point)
        if self.now()-self.last_traffic_t>=.128:
            self.radio.send(self.traffic.message())
            self.last_traffic_t = self.now()
        while self.traffic.events:
            self.log('doorway',**self.traffic.events.pop(0))
        return target

    def clear_staging_point(self,point):
        cx,cy=self.grid.to_cell(*point)
        radius=int(math.ceil(.36/self.grid.res))
        if cx<radius or cy<radius or cx+radius>=self.grid.w or cy+radius>=self.grid.h:
            return False
        occupied=self.grid.occupied()
        for dy in range(-radius,radius+1):
            for dx in range(-radius,radius+1):
                if dx*dx+dy*dy<=radius*radius and occupied[cy+dy,cx+dx]:
                    return False
        return True

    def _nav_step(self, ranges: np.ndarray, goal) -> str:
        """Returns 'moving', 'arrived' or 'failed'."""
        now = self.now()
        if now < self.recover_until:
            self.drive(-0.15, 0.6)
            return "moving"
        if math.dist(self.pose[:2], goal) < 0.35:
            self.drive(0, 0)
            return "arrived"
        if (self.path is None and now - self.last_plan_t > 1.0) or now - self.last_plan_t > 3.0:
            path = self.planner.plan(self.pose[:2], goal)
            self.last_plan_t = now
            if path is None:
                self.drive(0, 0)
                self.path = None
                self.job["plan_failures"] = self.job.get("plan_failures", 0) + 1
                return "failed" if self.job["plan_failures"] >= 4 else "moving"
            self.job["plan_failures"] = 0
            if self.path is not None and len(path) > 1:
                old_len = sum(math.dist(a, b) for a, b in zip(self.path[self.wp:], self.path[self.wp + 1:]))
                new_len = sum(math.dist(a, b) for a, b in zip(path, path[1:]))
                if new_len > old_len + 2.0:
                    self.log("replan", goal=[round(v, 2) for v in self.goal], old_len=round(old_len, 2),
                             new_len=round(new_len, 2))
            # A planner fallback is an approach point, not a completed mission goal.
            self.path, self.wp = path, min(1, len(path) - 1)
        if self.path is None:
            self.drive(0, 0)
            return "moving"
        while self.wp < len(self.path) - 1 and math.dist(self.pose[:2], self.path[self.wp]) < 0.4:
            self.wp += 1
        target = self.path[self.wp]
        if self.wp==len(self.path)-1 and math.dist(self.pose[:2],target)<.25 and math.dist(target,goal)>.35:
            self.drive(0,0)
            return 'failed'
        obstacles=self.obstacles_robot_frame(ranges)
        close = len(obstacles) and np.linalg.norm(obstacles,axis=1).min()<.34
        escape = None
        if close:
            direction=1 if (target[0]-self.pose[0])*math.cos(self.pose[2])+(target[1]-self.pose[1])*math.sin(self.pose[2])>=0 else -1
            for speed in [.08*direction,-.08*direction]:
                if safe_command(speed,0,obstacles)==(speed,0):
                    escape=(speed,0)
                    break
        if escape:
            v,w=escape
            self.dwa.v,self.dwa.w=v,w
        else:
            v, w, _ = self.dwa.command(self.pose, target, obstacles, self.dt)
        self.drive(v, w)
        t0, p0 = self.progress_ref
        if p0 is None or math.dist(p0, self.pose[:2]) > 0.25:
            self.progress_ref = (now, self.pose[:2])
        elif now - t0 > 6.0:
            self.stuck_count += 1
            points = self.obstacles_robot_frame(ranges)
            near = points[np.argsort(np.linalg.norm(points,axis=1))[:8]].tolist() if len(points) else []
            self.log("stuck_recovery", count=self.stuck_count, pose=[round(v, 2) for v in self.pose],
                     target=target,goal=goal,command=self.last_command,nearest=near)
            self.recover_until = now + 1.2
            self.progress_ref = (now + 1.2, self.pose[:2])
            self.path = None
            if self.stuck_count % 3 == 0:
                return "failed"
        return "moving"

    def spin_step(self) -> bool:
        yaw = self.pose[2]
        d = math.atan2(math.sin(yaw - self.job["spin_last"]), math.cos(yaw - self.job["spin_last"]))
        self.job["spin_acc"] += abs(d)
        self.job["spin_last"] = yaw
        if self.job["spin_acc"] >= self.job["spin_target"]:
            self.drive(0, 0)
            return True
        self.drive(0.0, 0.8 * self.job.get("spin_dir", 1))
        return False

    def start_spin(self, angle: float) -> None:
        self.job.update(spin_acc=0.0, spin_last=self.pose[2], spin_target=angle,
                        spin_dir=-self.job.get("spin_dir", 1))

    # ------------------------------------------------------------------ door blockage detection
    def check_doors(self) -> None:
        occ = self.grid.fused() > 0.7  # lidar evidence only; a peer standing in a doorway is not debris
        for did, d in self.bp.doors.items():
            if did in self.book.blocked:
                continue
            x, y = d["pos"]
            if d["wall"] == "h":
                x0, x1, y0, y1 = x - 0.35, x + 0.35, y - 0.7, y + 0.7
            else:
                x0, x1, y0, y1 = x - 0.7, x + 0.7, y - 0.35, y + 0.35
            c0x, c0y = self.grid.to_cell(x0, y0)
            c1x, c1y = self.grid.to_cell(x1, y1)
            patch = occ[int(c0y):int(c1y), int(c0x):int(c1x)]
            if patch.size and patch.mean() > 0.12 and math.dist(self.pose[:2], d["pos"]) < 5.0:
                self.book.blocked.add(did)
                self.log("door_blocked_detected", door=did, occupied_fraction=round(float(patch.mean()), 3))

    # ------------------------------------------------------------------ tasks
    def verification_viewpoints(self, pos, room, close=False):
        """Room-safe views from own occupancy and public geometry, never scene labels."""
        rect=self.bp.rooms.get(room,{}).get('rect',self.bp.bounds)
        x0,y0,x1,y1=rect
        heading=math.atan2(self.pose[1]-pos[1],self.pose[0]-pos[0])
        candidates=[]
        for radius in ((1.3,1.6,1.8) if close else (2.0,2.4,1.8)):
            for offset in range(16):
                angle=heading+offset*math.pi/8
                p=[pos[0]+radius*math.cos(angle),pos[1]+radius*math.sin(angle)]
                if not (x0+.4<p[0]<x1-.4 and y0+.4<p[1]<y1-.4):
                    continue
                if self.clear_staging_point(p):
                    candidates.append(p)
        candidates.sort(key=lambda p:math.dist(p,self.pose[:2]))
        views=[]
        for p in candidates:
            if all(math.dist(p,q)>1.0 for q in views):
                views.append(p)
        return views[:3]

    def next_verification_view(self, reason):
        j=self.job
        self.log('verification_retry',task=self.task,reason=reason,view=j['view_index'])
        j['view_index']+=1
        if j['view_index']>=len(j['views']):
            self.log('verification_inconclusive',task=self.task,reason=reason)
            self.finish_task('inconclusive')
            return
        j.update(phase='goto',samples=[],visual_positions=[],quality_positions=[],quality_seen_t=-1e9,
                 visual_hits=0,visual_quality_hits=0,seen_t=-1e9,leg_t0=self.now())
        self.set_goal(j['views'][j['view_index']])

    def start_task(self, tid: str) -> None:
        task = self.book.tasks[tid]
        self.task = tid
        self.job = {"task": tid, "kind": task["kind"], "t0": self.now()}
        if hasattr(self.alloc, "set_executing"):
            self.alloc.set_executing(tid)
        if task["kind"] == "inspect":
            vps = self.bp.viewpoints(task["room"])
            vps.sort(key=lambda p: math.dist(p, self.pose[:2]))
            self.job.update(room=task["room"], queue=vps, phase="goto", frontier_tries=0, spins=0)
            self.set_goal(self.job["queue"][0])
            self.state = "INSPECT"
        else:
            p = task["pos"]
            views=self.verification_viewpoints(p,task.get('room'))
            self.job.update(pos=p, hazard=task["hazard"], phase="goto" if views else 'giveup',
                            samples=[], visual_hits=0, views=views,view_index=0,seen_t=-1e9,leg_t0=self.now())
            if views:
                self.set_goal(views[0])
            self.state = "VERIFY"
        self.log("task_start", task=tid)

    def finish_task(self, result: str) -> None:
        tid = self.task
        self.book.mark_done(tid, self.name, round(self.now(), 2), result)
        self.log("task_failed" if result in ('unreachable','inconclusive') else "task_done", task=tid, result=result, duration=round(self.now() - self.job["t0"], 1))
        if self.job.get("kind") == "inspect" and result == "ok":
            self.inspected.append(self.job["room"])
            self.log("room_inspected", room=self.job["room"],
                     known_fraction=round(self.grid.known_fraction(self.bp.rooms[self.job["room"]]["rect"]), 3))
        self.task, self.job, self.state = None, {}, "IDLE"
        if hasattr(self.alloc, "set_executing"):
            self.alloc.set_executing(None)
        self.drive(0, 0)

    def abort_task(self, reason: str) -> None:
        self.log("task_abort", task=self.task, reason=reason)
        self.task, self.job, self.state = None, {}, "IDLE"
        if hasattr(self.alloc, "set_executing"):
            self.alloc.set_executing(None)
        self.drive(0, 0)

    def inspect_step(self, ranges: np.ndarray) -> None:
        j = self.job
        room = j["room"]
        if j["phase"] == "goto":
            res = self.nav_step(ranges)
            if res == "arrived":
                j["phase"] = "spin"
                self.start_spin(2 * math.pi if j["spins"] == 0 else math.pi)
            elif res == "failed" or self.now() - j.get("leg_t0", j["t0"]) > 60:
                self.log("viewpoint_skipped", room=room, goal=[round(v, 2) for v in self.goal])
                self._next_viewpoint()
        elif j["phase"] == "spin":
            if self.spin_step():
                j["spins"] += 1
                self._next_viewpoint()

    def _next_viewpoint(self) -> None:
        j = self.job
        j["queue"].pop(0)
        j["plan_failures"] = 0
        j["leg_t0"] = self.now()
        rect = self.bp.rooms[j["room"]]["rect"]
        if j["queue"]:
            j["phase"] = "goto"
            self.set_goal(j["queue"][0])
            return
        frac = self.grid.known_fraction(rect)
        if frac < 0.7 and j["frontier_tries"] < 2:
            fr = self.grid.frontiers(rect)
            if len(fr):
                d = np.hypot(fr[:, 0] - self.pose[0], fr[:, 1] - self.pose[1])
                pick = fr[int(np.argmin(np.where(d < 0.8, 99, d)))]
                j["frontier_tries"] += 1
                j["queue"] = [list(map(float, pick))]
                j["phase"] = "goto"
                self.log("frontier_goal", room=j["room"], known_fraction=round(frac, 3),
                         goal=[round(float(pick[0]), 2), round(float(pick[1]), 2)])
                self.set_goal(j["queue"][0])
                return
        if j["spins"] == 0:
            self.log("room_unreachable", room=j["room"])
            self.finish_task("unreachable")
        else:
            self.finish_task("ok")

    def verify_step(self, ranges: np.ndarray) -> None:
        j = self.job
        p = j["pos"]
        if j["phase"] == "goto":
            res = self.nav_step(ranges)
            if res == "arrived":
                j["phase"] = "face"
                j['face_t0']=self.now()
            elif res == "failed" or self.now() - j["leg_t0"] > 70:
                self.next_verification_view('route_failed')
        elif j["phase"] == "face":
            if self.now()-j['face_t0']>10:
                self.next_verification_view('unable_to_face')
                return
            seen=self.now()-j.get('seen_t',-1e9)<1.0
            want = j['aim'] if seen else math.atan2(p[1] - self.pose[1], p[0] - self.pose[0])
            err = math.atan2(math.sin(want - self.pose[2]), math.cos(want - self.pose[2]))
            if abs(err) < 0.12:
                self.drive(0, 0)
                if seen:
                    j["phase"] = "measure"
                    j["m_t0"] = self.now()
                    j["visual_hits"] = 0
                    j['visual_quality_hits']=0
                    j['samples']=[]
                    j['visual_positions']=[]
                    j['quality_positions']=[]
                    j['quality_seen_t']=-1e9
                else:
                    j['phase']='search'
                    j['search_t0']=self.now()
                    self.start_spin(2*math.pi)
            else:
                self.drive(0, max(-1.0, min(1.0, 1.5 * err)))
        elif j['phase']=='search':
            if self.now()-j.get('seen_t',-1e9)<.75:
                j['phase']='face'
                j['face_t0']=self.now()
            elif self.spin_step() or self.now()-j['search_t0']>12:
                self.next_verification_view('camera_not_reacquired')
        elif j["phase"] == "measure":
            self.drive(0, 0)
            if self.now()-j.get('sample_t',-1e9)>=.128:
                value=self.specialty_value() if self.now()-self.specialty_t<.5 else float('nan')
                j["samples"].append(value)
                j['sample_t']=self.now()
            if self.now() - j["m_t0"] > 4.0:
                self.decide_verification()
        elif j["phase"] == "giveup":
            self.log("verify_unreachable", task=self.task)
            self.finish_task("unreachable")

    def decide_verification(self) -> None:
        j = self.job
        samples=np.asarray(j['samples'],dtype=float)
        samples=samples[np.isfinite(samples)]
        s = float(np.median(samples)) if len(samples) else 0.0
        thr = THERMAL_CONFIRM if self.capability == "thermal" else GAS_CONFIRM_PPM
        sensor_ok = len(samples)>=8 and s > thr
        visual_ok = len(j.get('quality_positions',[]))>=3 and self.now()-j.get('quality_seen_t',-1e9)<1.0
        confirmed = sensor_ok and visual_ok
        j['strong_sensor_seen']=j.get('strong_sensor_seen',False) or sensor_ok
        self.log('verification_attempt',task=self.task,view=j['view_index'],pose=self.pose,
                 target=j['pos'],sensor=s,threshold=thr,sample_count=len(samples),
                 visual_frames=j.get('visual_hits',0),close_visual_frames=j.get('visual_quality_hits',0),
                 camera_fresh=self.now()-j.get('seen_t',-1e9)<1.0,accepted_visual=visual_ok,confirmed=confirmed)
        if not confirmed and j['view_index']+1<len(j['views']):
            if j.get('visual_positions') and j.get('visual_quality_hits',0)<3:
                # Scale-only range is tentative. Refine the estimate and move
                # closer until the camera supplies a sufficiently detailed view.
                refined=np.median(j['visual_positions'],axis=0).tolist()
                room=self.bp.room_of(*refined)
                views=self.verification_viewpoints(refined,room,close=True)
                if views:
                    j['pos']=refined
                    remaining=len(j['views'])-j['view_index']-1
                    j['views']=j['views'][:j['view_index']+1]+views[:remaining]
            self.next_verification_view('sensor_camera_disagree' if sensor_ok else 'insufficient_sensor_evidence')
            return
        if not visual_ok or len(samples)<8 or (j['strong_sensor_seen'] and not confirmed):
            self.next_verification_view('insufficient_fresh_evidence')
            return
        pos=np.median(j['quality_positions'],axis=0).tolist() if confirmed else j['pos']
        room = self.bp.room_of(*pos)
        f = {"kind": j["hazard"], "pos": [round(v, 2) for v in pos], "confirmed": confirmed, "by": self.name,
             "t": round(self.now(), 2), "room": room, "sensor": round(s, 3), "sensor_threshold": thr,
             "visual_hits": j.get("visual_hits", 0),
             "evidence": f"{self.capability}={s:.3f} (thr {thr}) + camera hits {j.get('visual_hits', 0)}"}
        if confirmed:
            self.book.add_finding(f)
        else:
            for k, g in list(self.book.findings.items()):
                if g["kind"] == j["hazard"] and math.dist(g["pos"], j["pos"]) < 1.2 and not g.get("confirmed"):
                    g["rejected"] = True
        self.log("finding", **f)
        self.finish_task("confirmed" if confirmed else "rejected")

    # ------------------------------------------------------------------ decisions
    def decide(self) -> None:
        if self.complete:
            # a peer may still discover a verification task this agent is needed for
            if self.state in ("RETURN", "DONE") and any(
                    self.book.eligible(t, self.capability) and t["kind"] == "verify" for t in self.book.open_tasks()):
                self.complete = False
                self.state = "IDLE"
                self.log("mission_reopened", reason="new verification task")
            else:
                return
        tid = self.alloc.choose(self.now(), self.pose[:2], self.peers)
        if self.state in ("INSPECT", "VERIFY"):
            if self.task in self.book.done:
                self.abort_task("done_by_peer")
            elif tid != self.task and self.method in ("cbba", "greedy", "central", "learned"):
                progress = self.job.get("spins", 0)
                if tid is not None and progress == 0:
                    self.abort_task(f"reallocated_to_{tid}")
                elif tid is None and self.method == "central":
                    pass
            return
        if self.state == "RETURN":
            return
        if tid:
            self.start_task(tid)
        elif self.alloc.my_scope_done(self.alive_caps()):
            self.state = "RETURN"
            self.job = {"t0": self.now()}
            self.set_goal(RALLY[self.name])
            self.complete = True
            self.log("mission_complete", inspected=self.inspected, findings=len(self.book.findings),
                     known_rooms_done=sorted(t for t in self.book.done if t.startswith("inspect")))

    def return_step(self, ranges) -> None:
        res = self.nav_step(ranges)
        if res in ("arrived", "failed") or self.now() - self.job["t0"] > 90:
            self.drive(0, 0)
            self.state = "DONE"
            self.log("at_rally_point", pose=[round(v, 2) for v in self.pose])

    # ------------------------------------------------------------------ telemetry
    def write_telemetry(self, with_images: bool) -> None:
        import cv2
        tele = {"t": round(self.now(), 2), "name": self.name, "pose": self.pose, "state": self.state, "task": self.task,
                "mode": self.mode, "goal": self.goal, "path": self.path, "heat": self.heat, "gas_ppm": self.gas_ppm,
                "capability": self.capability, "findings": list(self.book.findings.values()),
                "dets": self.last_dets, "peers": {k: {"pos": v["pos"], "alive": v["alive"]} for k, v in self.peers.items()},
                "blocked": sorted(self.book.blocked), "done": sorted(self.book.done),
                "tilt_deg": math.degrees(self.tilt), "safety_stops": self.safety_stops,
                "motion_command": self.last_command, **self.radio.stats()}
        if with_images:
            f = self.grid.fused()
            img = np.full(f.shape, 205, np.uint8)
            img[f < -0.7] = 255
            img[f > 0.7] = 40
            img = cv2.cvtColor(np.flipud(img), cv2.COLOR_GRAY2BGR)
            ok, buf = cv2.imencode(".png", img)
            tele["map_png"] = base64.b64encode(buf).decode() if ok else None
            tele["map_meta"] = {"bounds": self.bp.bounds, "res": self.grid.res}
            if self.last_rgb is not None:
                bgr = cv2.cvtColor(self.last_rgb, cv2.COLOR_RGB2BGR)
                for d in self.last_dets:
                    x0, y0, x1, y1 = map(int, d["box"])
                    col = {"fire": (0, 80, 255), "gas_leak": (60, 200, 60), "person": (255, 120, 0), "debris": (60, 120, 160)}.get(d["cls"], (200, 200, 200))
                    cv2.rectangle(bgr, (x0, y0), (x1, y1), col, 2)
                    cv2.putText(bgr, f"{d['cls']} {d['score']:.2f}", (x0, max(10, y0 - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.38, col, 1)
                ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 75])
                tele["camera_jpg"] = base64.b64encode(buf).decode() if ok else None
        atomic_write_text(self.run_dir / f"telemetry_{self.name}.json", json.dumps(tele))

    # ------------------------------------------------------------------ main loop
    def run(self) -> None:
        self.robot.step(self.ts)
        self.init_pose()
        self.load_detector()
        ranges = self.scan()
        step = 0
        diag_done = False
        while self.robot.step(self.ts) != -1:
            step += 1
            self.update_pose()
            self.handle_radio()
            if self.thermal and step % 2 == 0:
                self.read_thermal()
            if self.gas_rx:
                self.read_gas()
            if step % 4 == 0:
                ranges = self.scan()
                if step % 8 == 0:
                    self.grid.integrate_scan(self.pose[0], self.pose[1], self.pose[2], ranges, self.angles, self.lidar_max)
                if not diag_done:
                    diag_done = True
                    q = self.n_rays // 4
                    self.log("lidar_diag", pose=[round(v, 3) for v in self.pose],
                             r0=float(ranges[0]), r_q1=float(ranges[q]), r_half=float(ranges[2 * q]), r_q3=float(ranges[3 * q]),
                             fov=self.lidar_fov, rays=self.n_rays)
                # Always answer doorway requests, including during inspection and idle.
                self.service_traffic()
            if step % 16 == 0 and self.state in ("INSPECT", "VERIFY", "IDLE"):
                self.perceive(ranges)
            if step % 31 == 0:
                self.update_links()
                self.decide()
                self.send_heartbeat()
                self.write_status()
            if step % 62 == 0:
                self.send_map()
                self.check_doors()
                if self.telemetry:
                    self.write_telemetry(with_images=(step % 124 == 0))
            if self.traffic.state in ('yield','waiting','crossing') and self.traffic.target is not None:
                self.nav_step(ranges)
                if 'spin_last' in self.job:
                    self.job['spin_last'] = self.pose[2]
                if self.state=='VERIFY' and self.job.get('phase') in ('face','measure'):
                    self.job['phase']='goto'
            elif self.state == "INSPECT":
                self.inspect_step(ranges)
            elif self.state == "VERIFY":
                self.verify_step(ranges)
            elif self.state == "RETURN":
                self.return_step(ranges)
            else:
                self.drive(0, 0)


if __name__ == "__main__":
    agent = None
    try:
        agent = Agent()
        agent.run()
    except Exception:
        traceback.print_exc()
        where = agent.run_dir if agent is not None and getattr(agent, "run_dir", None) else ROOT / "runs"
        name = agent.name if agent is not None and getattr(agent, "name", None) else "unknown"
        Path(where).mkdir(parents=True, exist_ok=True)
        (Path(where) / f"crash_{name}.txt").write_text(traceback.format_exc(), encoding="utf-8")
        raise
