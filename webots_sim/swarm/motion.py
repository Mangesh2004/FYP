"""Calibrated low obstacle sensing and a final swept-footprint motion check.

Mount geometry is public robot configuration, not environmental ground truth.
The upper lidar is retained for mapping; the low range ring observes obstacles
and other chassis below that scan plane. Distances are in metres.
"""
from __future__ import annotations

import math

import numpy as np

PROXIMITY_COUNT = 32
PROXIMITY_MOUNT_RADIUS = 0.29
PROXIMITY_HEIGHT = 0.055
PROXIMITY_RANGE = 2.0
PROXIMITY_ANGLES = np.arange(PROXIMITY_COUNT) * (2 * math.pi / PROXIMITY_COUNT)
BODY_RADIUS = 0.30  # encloses Pioneer chassis, rear caster, and wheels about axle origin
SAFETY_MARGIN = 0.04


def proximity_points(ranges) -> np.ndarray:
    """Convert the low ring's metre-valued readings to robot-frame obstacle points."""
    r = np.asarray(ranges, dtype=float)
    if r.shape != (PROXIMITY_COUNT,) or not np.isfinite(r).all() or (r < 0).any():
        raise ValueError("invalid low range scan")
    hit = r < PROXIMITY_RANGE * 0.98
    d = r[hit] + PROXIMITY_MOUNT_RADIUS
    a = PROXIMITY_ANGLES[hit]
    return np.column_stack((d * np.cos(a), d * np.sin(a)))


def safe_command(v: float, w: float, obstacles: np.ndarray) -> tuple[float, float]:
    """Reject commands whose swept disk lacks clearance through reaction + braking.

This gate also applies to spin, reverse, and recovery paths which bypass DWA.
It is conservative for the measured points, not a guarantee about unseen objects.
"""
    obs = np.asarray(obstacles, dtype=float).reshape(-1, 2)
    if not (math.isfinite(v) and math.isfinite(w)) or not np.isfinite(obs).all():
        return 0.0, 0.0
    if not len(obs):
        return v, w
    horizon = 0.16 + abs(v) / 0.9  # 128 ms lidar sampling + one control tick, 0.9 m/s² braking
    times = np.linspace(0, horizon, max(3, math.ceil(horizon / 0.016) + 1))
    if abs(w) < 1e-6:
        px, py = v * times, np.zeros_like(times)
    else:
        px, py = v / w * np.sin(w * times), v / w * (1 - np.cos(w * times))
    distances = np.hypot(obs[:, None, 0] - px, obs[:, None, 1] - py)
    initial = distances[:, 0]
    # A robot may start within the extra margin (or sensor noise may put it
    # there). Permit only motion that increases that gap, without crossing the
    # physical envelope; otherwise an emergency stop can become permanent.
    if (initial < BODY_RADIUS).any() and (abs(w)>1e-6 or abs(v)>.081):
        return 0.0, 0.0  # allow only slow straight clearance-increasing escape here
    if abs(v) < 1e-6:
        return v, w  # rotating an enclosing disk preserves clearance outside its physical radius
    threshold = np.minimum(initial, BODY_RADIUS + SAFETY_MARGIN)
    if (distances < threshold[:, None] - 1e-6).any():
        return 0.0, 0.0
    tight = initial < BODY_RADIUS + SAFETY_MARGIN
    if (distances[tight, -1] <= initial[tight] + 1e-6).any():
        return 0.0, 0.0
    return v, w
