"""Gas concentration model used only by the supervisor to drive Agent B's gas sensor.

Steady-state plume approximation: concentration decays exponentially with distance from the leak
and each wall crossed on the straight line attenuates it. Walls come from the generated world.
"""
from __future__ import annotations

import math


def _segments_intersect(p1, p2, q1, q2) -> bool:
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    d1, d2 = orient(q1, q2, p1), orient(q1, q2, p2)
    d3, d4 = orient(p1, p2, q1), orient(p1, p2, q2)
    return (d1 * d2 < 0) and (d3 * d4 < 0)


class GasModel:
    def __init__(self, wall_segments: list[tuple[tuple[float, float], tuple[float, float]]],
                 decay_m: float = 1.6, wall_factor: float = 0.15, peak_ppm: float = 900.0):
        self.walls = wall_segments
        self.decay = decay_m
        self.wall_factor = wall_factor
        self.peak = peak_ppm

    def concentration(self, pos, sources) -> float:
        total = 0.0
        for s in sources:
            sp = s["pos"]
            d = math.dist(pos, sp)
            c = self.peak * s.get("strength", 1.0) * math.exp(-d / self.decay)
            crossings = sum(1 for a, b in self.walls if _segments_intersect(pos, sp, a, b))
            total += c * (self.wall_factor ** crossings)
        return total


def wall_segments_from_world(world_text: str) -> list:
    """Parse Wall nodes (translation + size) from the generated .wbt into 2D centre-line segments."""
    segs = []
    for block in world_text.split("\nWall {")[1:]:
        lines = [l.strip() for l in block.splitlines()[:4]]
        try:
            tline = next(l for l in lines if l.startswith("translation"))
            sline = next(l for l in lines if l.startswith("size"))
        except StopIteration:
            continue
        tx, ty, _ = (float(v) for v in tline.split()[1:4])
        sx, sy, _ = (float(v) for v in sline.split()[1:4])
        if sx >= sy:
            segs.append(((tx - sx / 2, ty), (tx + sx / 2, ty)))
        else:
            segs.append(((tx, ty - sy / 2), (tx, ty + sy / 2)))
    return segs
