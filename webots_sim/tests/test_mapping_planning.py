import math

import numpy as np

from swarm.mapping import OccupancyGrid
from swarm.planning import DWA, GlobalPlanner, astar


def raycast_box(x, y, yaw, angles, walls, max_r=8.0):
    """Exact ranges to axis-aligned wall segments for a synthetic lidar."""
    out = np.full(len(angles), np.inf, dtype=np.float32)
    for i, a in enumerate(angles):
        dx, dy = math.cos(yaw + a), math.sin(yaw + a)
        best = max_r
        for (x0, y0), (x1, y1) in walls:
            if x0 == x1:
                if abs(dx) < 1e-9:
                    continue
                t = (x0 - x) / dx
                yy = y + t * dy
                if t > 0 and min(y0, y1) <= yy <= max(y0, y1):
                    best = min(best, t)
            else:
                if abs(dy) < 1e-9:
                    continue
                t = (y0 - y) / dy
                xx = x + t * dx
                if t > 0 and min(x0, x1) <= xx <= max(x0, x1):
                    best = min(best, t)
        out[i] = best if best < max_r else np.inf
    return out


ROOM = [((0, 0), (4, 0)), ((4, 0), (4, 3)), ((4, 3), (0, 3)), ((0, 3), (0, 0))]
ANGLES = 2 * math.pi / 2 - (np.arange(360) + 0.5) * 2 * math.pi / 360


def test_occupancy_grid_reconstructs_room_walls():
    g = OccupancyGrid([-1, -1, 5, 4], 0.1)
    for x, y, yaw in [(1, 1, 0.2), (3, 2, 1.7), (2, 1.5, -2.0), (1.2, 2.3, 3.0)]:
        for _ in range(3):
            g.integrate_scan(x, y, yaw, raycast_box(x, y, yaw, ANGLES, ROOM), ANGLES, 8.0)
    occ = g.occupied()
    truth = np.zeros_like(occ)
    for (x0, y0), (x1, y1) in ROOM:
        xs = np.linspace(x0, x1, 200)
        ys = np.linspace(y0, y1, 200)
        cx, cy = g.to_cell(xs, ys)
        truth[np.clip(cy, 0, g.h - 1), np.clip(cx, 0, g.w - 1)] = True
    dil = np.zeros_like(truth)
    from scipy import ndimage
    dil = ndimage.binary_dilation(truth, iterations=1)
    precision = (occ & dil).sum() / max(occ.sum(), 1)
    recall = (ndimage.binary_dilation(occ, iterations=1) & truth).sum() / truth.sum()
    assert precision > 0.9, precision
    assert recall > 0.85, recall
    assert g.known_fraction([0, 0, 4, 3]) > 0.9
    cx, cy = g.to_cell(2.0, 1.5)
    assert g.fused()[cy, cx] < -0.7


def test_map_delta_round_trip_reproduces_peer_map():
    a = OccupancyGrid([-1, -1, 5, 4], 0.1)
    b = OccupancyGrid([-1, -1, 5, 4], 0.1)
    a.integrate_scan(2, 1.5, 0.0, raycast_box(2, 1.5, 0.0, ANGLES, ROOM), ANGLES, 8.0)
    msg = a.encode_delta()
    assert msg is not None and msg["n"] > 100
    b.apply_peer_delta(msg)
    assert np.array_equal(a.occupied(), b.occupied())
    assert a.encode_delta() is None  # nothing new to send


def test_astar_routes_through_the_only_gap_and_fails_when_blocked():
    blocked = np.zeros((20, 30), bool)
    blocked[:, 15] = True
    blocked[14:17, 15] = False
    cost = np.zeros_like(blocked, float)
    path = astar(blocked, cost, (2, 2), (27, 2))
    assert path is not None
    assert any(x == 15 and 14 <= y <= 16 for x, y in path)
    length = sum(math.dist(a, b) for a, b in zip(path, path[1:]))
    straight = math.dist((2, 2), (15, 15)) + math.dist((15, 15), (27, 2))
    assert length <= straight * 1.15
    blocked[14:17, 15] = True
    assert astar(blocked, cost, (2, 2), (27, 2)) is None


def test_global_planner_detours_around_debris_seen_by_lidar():
    walls = [((0, 0), (8, 0)), ((8, 0), (8, 4)), ((8, 4), (0, 4)), ((0, 4), (0, 0)),
             ((4, 0), (4, 1.4)), ((4, 2.6), (4, 4))]
    g = OccupancyGrid([-1, -1, 9, 5], 0.1)
    for x, y in [(2, 2), (6, 2), (3.5, 2), (4.5, 2)]:
        for _ in range(3):
            g.integrate_scan(x, y, 0, raycast_box(x, y, 0, ANGLES, walls), ANGLES, 8.0)
    p = GlobalPlanner(g)
    path = p.plan([2, 2], [6, 2])
    assert path is not None
    assert all(abs(pt[0] - 4) > 0.3 or 1.4 < pt[1] < 2.6 for pt in path)
    debris = walls + [((4, 1.4), (4, 2.6))]
    for _ in range(12):
        g.integrate_scan(3.4, 2, 0, raycast_box(3.4, 2, 0, ANGLES, debris), ANGLES, 8.0)
    assert p.plan([2, 2], [6, 2]) is None


def test_static_obstacle_closes_door_for_planner_and_can_be_cleared():
    walls = [((0, 0), (8, 0)), ((8, 0), (8, 4)), ((8, 4), (0, 4)), ((0, 4), (0, 0)),
             ((4, 0), (4, 1.45)), ((4, 2.55), (4, 4))]
    g = OccupancyGrid([-1, -1, 9, 5], 0.1)
    for x, y in [(2, 2), (6, 2), (3.5, 2), (4.5, 2)]:
        for _ in range(3):
            g.integrate_scan(x, y, 0, raycast_box(x, y, 0, ANGLES, walls), ANGLES, 8.0)
    p = GlobalPlanner(g)
    assert p.plan([2, 2], [6, 2]) is not None
    g.set_static("peer:A", 4.0, 2.0, 0.3)
    assert p.plan([2, 2], [6, 2]) is None
    assert len(g.static_points(3.5, 2.0, 1.0)) > 10
    g.clear_static("peer:A")
    assert p.plan([2, 2], [6, 2]) is not None


def drive(goal, world_obs, steps=900, dt=0.064):
    d = DWA()
    x, y, th = 0.0, 0.0, 0.0
    min_clear = 9.0
    for _ in range(steps):
        rel = world_obs - [x, y]
        c, s = math.cos(-th), math.sin(-th)
        local = np.stack([c * rel[:, 0] - s * rel[:, 1], s * rel[:, 0] + c * rel[:, 1]], 1) if len(rel) else rel
        v, w, _ = d.command((x, y, th), goal, local, dt)
        th += w * dt
        x += v * math.cos(th) * dt
        y += v * math.sin(th) * dt
        if len(world_obs):
            min_clear = min(min_clear, float(np.hypot(*(world_obs - [x, y]).T).min()))
        if math.dist((x, y), goal) < 0.2:
            return True, min_clear
    return False, min_clear


def test_dwa_reaches_goal_around_obstacle_without_contact():
    for y0, y1 in [(-0.35, 0.15), (-0.3, 0.3), (-0.9, 0.9)]:
        block = np.array([[1.2, yy] for yy in np.linspace(y0, y1, 25)])
        reached, clear = drive((3.0, 0.0), block)
        assert reached, (y0, y1)
        assert clear > 0.27, (y0, y1, clear)


def test_dwa_drives_straight_in_free_space():
    reached, _ = drive((3.0, 0.0), np.zeros((0, 2)), steps=200)
    assert reached


def test_dwa_keeps_full_chassis_and_safety_margin_clear_of_a_crate():
    block = np.array([[1.2, yy] for yy in np.linspace(-0.3, 0.3, 25)])
    reached, clear = drive((3.0, 0.0), block)
    assert reached
    assert clear >= 0.34, clear  # 30 cm chassis envelope + 4 cm clearance
