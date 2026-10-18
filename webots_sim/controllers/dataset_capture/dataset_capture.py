"""Offline dataset capture for the detector (not used during missions).

For each layout seed: place hazards, victims, debris, distractors and lighting at random, then take
camera frames from random collision-free poses at the agents' camera height. Bounding boxes come
from Webots Camera.recognition at capture time only.

Output: data/detector/images/*.jpg and data/detector/labels.jsonl
Env: FYP_CAPTURE_LAYOUTS (e.g. "0-169,1000-1039"), FYP_VIEWS_PER_LAYOUT (default 24)
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
import traceback
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller import Supervisor  # noqa: E402

OUT = ROOT / "data" / "detector"
CLASS_OF_MODEL = {"fire": "fire", "gas_leak": "gas_leak", "pedestrian": "person", "debris": "debris"}
BLUEPRINT = json.loads((ROOT / "config" / "blueprint.json").read_text(encoding="utf-8"))
SLOTS = json.loads((ROOT / "truth" / "slots.json").read_text(encoding="utf-8"))["rooms"]
FURN = json.loads((ROOT / "truth" / "furniture.json").read_text(encoding="utf-8"))
HIDE_Z = -20.0
REGIONS = {**{k: v["rect"] for k, v in BLUEPRINT["rooms"].items()}, "CORRIDOR": BLUEPRINT["corridor"]}


def parse_ranges(spec: str) -> list[int]:
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


class Capture:
    def __init__(self) -> None:
        self.sv = Supervisor()
        self.ts = int(self.sv.getBasicTimeStep())
        self.cam = self.sv.getDevice("camera")
        self.cam.enable(self.ts)
        self.cam.recognitionEnable(self.ts)
        self.rig = self.sv.getSelf()
        self.sun = self.sv.getFromDef("SUN")
        self.pools = {
            "fire": [f"FIRE_{i}" for i in range(4)],
            "gas": [f"GAS_{i}" for i in range(4)],
            "victim": [f"VICTIM_{i}" for i in range(4)],
            "debris": [f"DEBRIS_{i}" for i in range(3)],
            "distract": [f"DISTRACT_{i}" for i in range(4)],
            "prop": ["PROP_ROBOT_0", "PROP_ROBOT_1"],
        }
        (OUT / "images").mkdir(parents=True, exist_ok=True)

    def step(self, n: int = 1) -> None:
        for _ in range(n):
            if self.sv.step(self.ts) == -1:
                sys.exit(0)

    def place(self, d: str, xy, yaw: float, z: float) -> None:
        node = self.sv.getFromDef(d)
        node.getField("translation").setSFVec3f([float(xy[0]), float(xy[1]), z])
        node.getField("rotation").setSFRotation([0, 0, 1, float(yaw)])
        node.resetPhysics()

    def hide_all(self) -> None:
        k = 0
        for group in self.pools.values():
            for d in group:
                self.place(d, [40 + k * 3, 40], 0, HIDE_Z)
                k += 1
        for i in range(4):
            self.sv.getFromDef(f"FIRE_{i}_LIGHT").getField("on").setSFBool(False)

    def layout(self, rng: random.Random) -> list[dict]:
        self.hide_all()
        self.sun.getField("intensity").setSFFloat(rng.uniform(1.4, 3.2))
        placed: list[dict] = []
        free = [(r, p) for r, pts in SLOTS.items() for p in pts]
        rng.shuffle(free)

        def take(room_bias=None):
            for i, (r, p) in enumerate(free):
                if all(math.dist(p, q["pos"]) > 1.0 for q in placed):
                    free.pop(i)
                    jitter = [p[0] + rng.uniform(-0.2, 0.2), p[1] + rng.uniform(-0.2, 0.2)]
                    return r, jitter
            return None

        counts = {"fire": rng.randint(1, 3), "gas": rng.randint(1, 3), "victim": rng.randint(1, 3),
                  "distract": rng.randint(2, 4), "prop": rng.randint(0, 2)}
        for kind, n in counts.items():
            for d in rng.sample(self.pools[kind], n):
                got = take()
                if not got:
                    continue
                room, pos = got
                z = 1.27 if kind == "victim" else (0.44 if d == "DISTRACT_3" else 0.0975 if kind == "prop" else 0.0)
                yaw = rng.uniform(-math.pi, math.pi)
                self.place(d, pos, yaw, z)
                if kind == "fire":
                    light = self.sv.getFromDef(f"{d}_LIGHT")
                    light.getField("on").setSFBool(True)
                    light.getField("intensity").setSFFloat(rng.uniform(2.5, 4.5))
                placed.append({"def": d, "kind": kind, "room": room, "pos": pos})
        doors = [k for k, v in BLUEPRINT["doors"].items()]
        for d in rng.sample(self.pools["debris"], rng.randint(0, 2)):
            if rng.random() < 0.6:
                door = rng.choice(doors)
                dd = BLUEPRINT["doors"][door]
                x, y = dd["pos"]
                yaw = 0.0 if dd["wall"] == "v" else math.pi / 2
                if dd["wall"] == "h":
                    y += rng.choice([-0.42, 0.42])
                else:
                    x += rng.choice([-0.42, 0.42])
                pos, room = [x, y], door
            else:
                got = take()
                if not got:
                    continue
                room, pos = got
                yaw = rng.uniform(-math.pi, math.pi)
            self.place(d, pos, yaw, 0.0)
            placed.append({"def": d, "kind": "debris", "room": room, "pos": pos})
        self.step(3)
        return placed

    def camera_pose(self, rng: random.Random, placed: list[dict]):
        names = list(REGIONS)
        for _ in range(200):
            region = rng.choice(names)
            x0, y0, x1, y1 = REGIONS[region]
            x, y = rng.uniform(x0 + 0.5, x1 - 0.5), rng.uniform(y0 + 0.45, y1 - 0.45)
            if any(fx0 - 0.35 <= x <= fx1 + 0.35 and fy0 - 0.35 <= y <= fy1 + 0.35 for fx0, fy0, fx1, fy1 in FURN):
                continue
            if any(math.dist([x, y], p["pos"]) < 0.9 for p in placed):
                continue
            targets = [p for p in placed if 1.0 < math.dist([x, y], p["pos"]) < 6.5]
            if targets and rng.random() < 0.7:
                t = rng.choice(targets)
                yaw = math.atan2(t["pos"][1] - y, t["pos"][0] - x) + rng.uniform(-0.45, 0.45)
            else:
                yaw = rng.uniform(-math.pi, math.pi)
            return region, x, y, yaw
        return None

    def shoot(self, name: str, region: str, x: float, y: float, yaw: float, pitch: float) -> dict | None:
        self.rig.getField("translation").setSFVec3f([x, y, 0.3075])
        cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
        m = [[cy * cp, -sy, cy * sp], [sy * cp, cy, sy * sp], [-sp, 0, cp]]
        ang = math.acos(max(-1.0, min(1.0, (m[0][0] + m[1][1] + m[2][2] - 1) / 2)))
        if ang < 1e-6:
            rot = [0, 0, 1, 0]
        else:
            ax = [m[2][1] - m[1][2], m[0][2] - m[2][0], m[1][0] - m[0][1]]
            n = math.sqrt(sum(a * a for a in ax))
            rot = [ax[0] / n, ax[1] / n, ax[2] / n, ang]
        self.rig.getField("rotation").setSFRotation(rot)
        self.step(2)
        w, h = self.cam.getWidth(), self.cam.getHeight()
        img = np.frombuffer(self.cam.getImage(), dtype=np.uint8).reshape(h, w, 4)[:, :, :3]
        boxes = []
        for o in self.cam.getRecognitionObjects():
            cls = CLASS_OF_MODEL.get(o.getModel())
            if cls is None:
                continue
            cx, cyy = o.getPositionOnImage()
            bw, bh = o.getSizeOnImage()
            x0, y0 = max(0, cx - bw / 2), max(0, cyy - bh / 2)
            x1, y1 = min(w - 1, cx + bw / 2), min(h - 1, cyy + bh / 2)
            if (x1 - x0) < 6 or (y1 - y0) < 6 or (x1 - x0) * (y1 - y0) < 64:
                continue
            boxes.append([round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1), cls])
        cv2.imwrite(str(OUT / "images" / f"{name}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        return {"file": f"images/{name}.jpg", "region": region, "boxes": boxes,
                "pose": [round(x, 3), round(y, 3), round(yaw, 3), round(pitch, 3)]}

    def run(self) -> None:
        layouts = parse_ranges(os.environ.get("FYP_CAPTURE_LAYOUTS", "0-169,1000-1039"))
        views = int(os.environ.get("FYP_VIEWS_PER_LAYOUT", "24"))
        self.step(2)
        out = (OUT / "labels.jsonl").open("w", encoding="utf-8")
        stats: dict[str, int] = {}
        n = 0
        for lid in layouts:
            rng = random.Random(lid * 7919 + 13)
            placed = self.layout(rng)
            for v in range(views):
                pose = self.camera_pose(rng, placed)
                if pose is None:
                    continue
                region, x, y, yaw = pose
                rec = self.shoot(f"L{lid:04d}_V{v:02d}", region, x, y, yaw, rng.uniform(-0.06, 0.08))
                rec["layout"] = lid
                out.write(json.dumps(rec) + "\n")
                for b in rec["boxes"]:
                    stats[b[4]] = stats.get(b[4], 0) + 1
                n += 1
            out.flush()
            if lid % 20 == 0:
                print("CAPTURE", lid, n, stats, flush=True)
        out.close()
        (OUT / "capture_stats.json").write_text(json.dumps({"images": n, "boxes": stats}, indent=2), encoding="utf-8")
        print("CAPTURE_DONE", n, stats, flush=True)
        self.sv.simulationQuit(0)


if __name__ == "__main__":
    try:
        Capture().run()
    except Exception:
        (ROOT / "runs").mkdir(exist_ok=True)
        (ROOT / "runs" / "capture_crash.txt").write_text(traceback.format_exc(), encoding="utf-8")
        raise
