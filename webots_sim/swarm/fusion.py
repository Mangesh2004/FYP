"""Turn per-frame detections into map-frame evidence tracks and decide what needs verification."""
from __future__ import annotations

import math

import numpy as np

KIND = {"fire": "fire", "gas_leak": "gas", "person": "victim"}
ASSUMED_HEIGHT = {"fire": 1.0, "gas": 1.5, "victim": 1.75}


def bearing_from_box(box, width: int, fov: float) -> float:
    f = (width / 2) / math.tan(fov / 2)
    u = (box[0] + box[2]) / 2
    return math.atan((width / 2 - u) / f)


def lidar_range_at(ranges: np.ndarray, angles: np.ndarray, bearing: float, max_range: float) -> float | None:
    d = np.abs(np.arctan2(np.sin(angles - bearing), np.cos(angles - bearing)))
    idx = np.argsort(d)[:5]
    vals = ranges[idx]
    vals = vals[np.isfinite(vals) & (vals < max_range * 0.98)]
    return float(vals.min()) if vals.size else None


class EvidenceTracker:
    def __init__(self, visual_needed: int = 3, merge_radius: float = 1.2) -> None:
        self.tracks: list[dict] = []
        self.need = visual_needed
        self.radius = merge_radius

    def add(self, kind: str, pos, score: float, t: float) -> dict:
        for tr in self.tracks:
            if tr["kind"] == kind and math.dist(tr["pos"], pos) < self.radius:
                n = tr["n"]
                tr["pos"] = [(tr["pos"][0] * n + pos[0]) / (n + 1), (tr["pos"][1] * n + pos[1]) / (n + 1)]
                tr["n"] = n + 1
                tr["score"] = max(tr["score"], score)
                tr["last"] = t
                return tr
        tr = {"kind": kind, "pos": list(pos), "n": 1, "score": score, "first": t, "last": t, "reported": False}
        self.tracks.append(tr)
        return tr

    def ready(self) -> list[dict]:
        """Tracks with enough independent frames that have not been acted on yet."""
        return [t for t in self.tracks if t["n"] >= self.need and not t["reported"]]


def lidar_range_in_box(ranges: np.ndarray, angles: np.ndarray, box, width: int, fov: float,
                       max_range: float) -> float | None:
    """Median lidar range over the rays that fall inside the central 60% of the box's width."""
    x0, x1 = box[0], box[2]
    c, half = (x0 + x1) / 2, 0.3 * (x1 - x0)
    b_lo = bearing_from_box([c + half, 0, c + half, 0], width, fov)
    b_hi = bearing_from_box([c - half, 0, c - half, 0], width, fov)
    wrapped = np.arctan2(np.sin(angles), np.cos(angles))
    m = (wrapped >= b_lo) & (wrapped <= b_hi)
    vals = ranges[m]
    vals = vals[np.isfinite(vals) & (vals < max_range * 0.98)]
    if vals.size == 0:
        return lidar_range_at(ranges, angles, (b_lo + b_hi) / 2, max_range)
    return float(np.median(vals))


def localize(det: dict, pose, ranges, angles, cam_width: int, cam_fov: float, lidar_max: float,
             cam_height: int = 240):
    kind = KIND.get(det["cls"])
    if kind is None:
        return None
    box = det["box"]
    b = bearing_from_box(box, cam_width, cam_fov)
    r = lidar_range_in_box(ranges, angles, box, cam_width, cam_fov, lidar_max)
    h_px = max(4.0, box[3] - box[1])
    f = (cam_width / 2) / math.tan(cam_fov / 2)
    r_box = ASSUMED_HEIGHT[kind] * f / h_px
    truncated = box[1] <= 1.5 or box[3] >= cam_height - 1.5
    if truncated:
        # a cut-off box looks shorter than the object, so r_box is only an upper bound
        if r is None or r > r_box + 0.5:
            r = min(r_box, lidar_max)
    elif r is None or abs(r - r_box) > max(0.8, 0.35 * r_box):
        r = r_box
    r = min(r + 0.15, 8.0)
    x, y, yaw = pose
    return kind, [x + r * math.cos(yaw + b), y + r * math.sin(yaw + b)], b, r
