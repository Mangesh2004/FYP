import math

import numpy as np

from swarm.fusion import EvidenceTracker, bearing_from_box, localize

W, FOV, H = 320, 1.2, 240
ANG = math.pi - (np.arange(360) + 0.5) * 2 * math.pi / 360
F = (W / 2) / math.tan(FOV / 2)


def box_for(dist, bearing, height_m, width_m=0.5):
    u = W / 2 - F * math.tan(bearing)
    hp = height_m * F / dist
    wp = width_m * F / dist
    return [u - wp / 2, H / 2 - hp / 2, u + wp / 2, H / 2 + hp / 2]


def scan_with(obj_dist, obj_bearing, obj_halfwidth_rad, occluder=None):
    r = np.full(360, np.inf, np.float32)
    d = np.abs(np.arctan2(np.sin(ANG - obj_bearing), np.cos(ANG - obj_bearing)))
    r[d < obj_halfwidth_rad] = obj_dist
    if occluder:
        ob, ow, od = occluder
        r[np.abs(np.arctan2(np.sin(ANG - ob), np.cos(ANG - ob))) < ow] = od
    return r


def test_bearing_sign_matches_lidar_frame():
    assert bearing_from_box([0, 0, 20, 10], W, FOV) > 0.4
    assert bearing_from_box([300, 0, 320, 10], W, FOV) < -0.4
    assert abs(bearing_from_box([150, 0, 170, 10], W, FOV)) < 1e-6


def test_localize_uses_lidar_and_ignores_thin_occluder():
    dist, b = 4.5, 0.25
    det = {"cls": "gas_leak", "box": box_for(dist, b, 1.5, 0.6)}
    ranges = scan_with(dist, b, 0.06, occluder=(b + 0.02, 0.012, 2.1))
    kind, pos, _, r = localize(det, (1.0, 2.0, 0.3), ranges, ANG, W, FOV, 8.0, H)
    true = (1.0 + dist * math.cos(0.3 + b), 2.0 + dist * math.sin(0.3 + b))
    assert kind == "gas"
    assert math.dist(pos, true) < 0.35, (pos, true)


def test_localize_falls_back_to_box_height_when_lidar_sees_something_else():
    dist, b = 5.0, -0.1
    det = {"cls": "fire", "box": box_for(dist, b, 1.0)}
    ranges = scan_with(2.0, b, 0.2)  # a wide table is in the lidar plane in front of the flames
    _, pos, _, r = localize(det, (0, 0, 0), ranges, ANG, W, FOV, 8.0, H)
    assert abs(r - dist) < 0.5, r


def test_truncated_box_trusts_closer_lidar():
    det = {"cls": "person", "box": [100, 0, 200, 239.5]}
    ranges = scan_with(1.2, bearing_from_box([100, 0, 200, 0], W, FOV), 0.15)
    _, _, _, r = localize(det, (0, 0, 0), ranges, ANG, W, FOV, 8.0, H)
    assert abs(r - 1.35) < 0.05


def test_tracker_needs_three_frames_and_merges_nearby():
    t = EvidenceTracker()
    for i in range(2):
        t.add("fire", [1.0 + 0.1 * i, 1.0], 0.9, i)
    assert t.ready() == []
    t.add("fire", [1.05, 0.95], 0.9, 3)
    t.add("fire", [5.0, 5.0], 0.9, 3)
    ready = t.ready()
    assert len(ready) == 1 and ready[0]["n"] == 3
