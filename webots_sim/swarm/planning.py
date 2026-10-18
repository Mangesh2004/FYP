"""Global A* on the agent's own inflated occupancy grid and a dynamic-window local controller."""
from __future__ import annotations

import heapq
import math

import numpy as np
from scipy import ndimage

from .mapping import OccupancyGrid
from .motion import BODY_RADIUS, SAFETY_MARGIN

SQRT2 = math.sqrt(2.0)
NEIGHBORS = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
             (1, 1, SQRT2), (1, -1, SQRT2), (-1, 1, SQRT2), (-1, -1, SQRT2)]


def astar(blocked: np.ndarray, cost: np.ndarray, start: tuple[int, int], goal: tuple[int, int],
          max_expand: int = 60000) -> list[tuple[int, int]] | None:
    """A* on a 2D grid. blocked[y, x] True = impassable; cost[y, x] >= 0 is an extra per-cell cost."""
    h, w = blocked.shape
    sx, sy = start
    gx, gy = goal
    if not (0 <= gx < w and 0 <= gy < h) or blocked[gy, gx]:
        return None
    g = {start: 0.0}
    parent: dict[tuple[int, int], tuple[int, int]] = {}
    pq = [(math.hypot(gx - sx, gy - sy), 0.0, start)]
    closed = set()
    expanded = 0
    while pq:
        _, gc, node = heapq.heappop(pq)
        if node in closed:
            continue
        if node == goal:
            path = [node]
            while node in parent:
                node = parent[node]
                path.append(node)
            return path[::-1]
        closed.add(node)
        expanded += 1
        if expanded > max_expand:
            return None
        x, y = node
        for dx, dy, step in NEIGHBORS:
            nx, ny = x + dx, y + dy
            if nx < 0 or ny < 0 or nx >= w or ny >= h or blocked[ny, nx]:
                continue
            if dx and dy and (blocked[y, nx] or blocked[ny, x]):
                continue
            ng = gc + step * (1.0 + cost[ny, nx])
            nb = (nx, ny)
            if ng < g.get(nb, 1e18):
                g[nb] = ng
                parent[nb] = node
                heapq.heappush(pq, (ng + math.hypot(gx - nx, gy - ny), ng, nb))
    return None


class GlobalPlanner:
    """Plans on a coarser copy of the map (factor x resolution) for speed."""

    def __init__(self, grid: OccupancyGrid, robot_radius: float = BODY_RADIUS, factor: int = 2) -> None:
        self.grid = grid
        self.radius = robot_radius
        self.k = factor

    def _coarse(self):
        infl = self.grid.inflated(self.radius)
        f = self.grid.fused()
        unknown = np.abs(f) <= 0.3
        k = self.k
        h, w = infl.shape
        hh, ww = h // k, w // k
        blk = infl[:hh * k, :ww * k].reshape(hh, k, ww, k)
        # a coarse cell is blocked only if most of it is blocked, keeping 1.1 m doors passable
        blocked = blk.mean(axis=(1, 3)) > 0.5
        unk = unknown[:hh * k, :ww * k].reshape(hh, k, ww, k).mean(axis=(1, 3))
        dist = ndimage.distance_transform_edt(~blocked) * self.grid.res * k
        cost = 0.6 * unk + 2.0 * np.clip(0.6 - dist, 0, None)
        return blocked, cost

    def plan(self, start_xy, goal_xy) -> list[list[float]] | None:
        blocked, cost = self._coarse()
        res = self.grid.res * self.k
        to = lambda x, y: (int((x - self.grid.x0) / res), int((y - self.grid.y0) / res))  # noqa: E731
        s = to(*start_xy)
        gcell = to(*goal_xy)
        h, w = blocked.shape
        s = (min(max(s[0], 0), w - 1), min(max(s[1], 0), h - 1))
        blocked = blocked.copy()
        if blocked[s[1], s[0]]:
            # inside an inflated zone (next to a wall or a peer): start from the nearest free cell
            free = self._nearest_free(blocked, s, max_r=4)
            if free is not None:
                s = free
        blocked[max(0, s[1] - 1):s[1] + 2, max(0, s[0] - 1):s[0] + 2] = False
        if blocked[gcell[1], gcell[0]] if (0 <= gcell[0] < w and 0 <= gcell[1] < h) else True:
            gcell = self._nearest_free(blocked, gcell)
            if gcell is None:
                return None
        cells = astar(blocked, cost, s, gcell)
        if cells is None:
            return None
        pts = [[self.grid.x0 + (cx + 0.5) * res, self.grid.y0 + (cy + 0.5) * res] for cx, cy in cells]
        return self._prune(pts, blocked, res)

    def _nearest_free(self, blocked: np.ndarray, cell, max_r: int = 6):
        h, w = blocked.shape
        cx, cy = cell
        for r in range(1, max_r + 1):
            best = None
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < w and 0 <= y < h and not blocked[y, x]:
                        d = dx * dx + dy * dy
                        if best is None or d < best[0]:
                            best = (d, (x, y))
            if best:
                return best[1]
        return None

    def _prune(self, pts, blocked, res, spacing: float = 0.7):
        if len(pts) <= 2:
            return pts
        out = [pts[0]]
        acc = 0.0
        for a, b in zip(pts, pts[1:]):
            acc += math.dist(a, b)
            if acc >= spacing:
                out.append(b)
                acc = 0.0
        if out[-1] != pts[-1]:
            out.append(pts[-1])
        return out


class DWA:
    """Dynamic window approach on lidar points expressed in the robot frame."""

    def __init__(self, v_max: float = 0.45, w_max: float = 1.6, radius: float = BODY_RADIUS) -> None:
        self.v_max = v_max
        self.w_max = w_max
        self.radius = radius
        self.v = 0.0
        self.w = 0.0
        self.escape = 0.0
        self.escape_left = 0.0
        self.stall = 0.0

    STALL_S = 0.6
    ESCAPE_S = 3.0

    def command(self, pose, target, obstacles: np.ndarray, dt: float) -> tuple[float, float, float]:
        x, y, yaw = pose
        dx, dy = target[0] - x, target[1] - y
        heading_err = math.atan2(math.sin(math.atan2(dy, dx) - yaw), math.cos(math.atan2(dy, dx) - yaw))
        dist = math.hypot(dx, dy)
        if abs(heading_err) > (2.6 if self.escape else 1.0):
            w = max(-self.w_max, min(self.w_max, 2.0 * heading_err))
            self.v, self.w = 0.0, w
            return 0.0, w, self._clearance(obstacles)
        a_v, a_w = 0.9, 3.0
        vs = np.linspace(max(0.0, self.v - a_v * 0.5), min(self.v_max, self.v + a_v * 0.5), 6)
        ws = np.linspace(max(-self.w_max, self.w - a_w * 0.5), min(self.w_max, self.w + a_w * 0.5), 13)
        best, best_s = (0.0, 0.0), -1e9
        horizon, n = 1.4, 7
        ts = np.linspace(horizon / n, horizon, n)
        obs = obstacles
        for v in vs:
            for w in ws:
                if abs(w) < 1e-4:
                    px, py, pth = v * ts, np.zeros_like(ts), np.zeros_like(ts)
                else:
                    pth = w * ts
                    px = v / w * np.sin(pth)
                    py = v / w * (1 - np.cos(pth))
                if obs.size:
                    d = np.sqrt((obs[:, None, 0] - px[None, :]) ** 2 + (obs[:, None, 1] - py[None, :]) ** 2)
                    clear = float(d.min())
                else:
                    clear = 3.0
                if clear < self.radius + SAFETY_MARGIN + v * v / (2 * a_v):
                    continue
                gx = math.cos(yaw) * px[-1] - math.sin(yaw) * py[-1] + x
                gy = math.sin(yaw) * px[-1] + math.cos(yaw) * py[-1] + y
                final_yaw = yaw + pth[-1]
                to_goal = math.atan2(target[1] - gy, target[0] - gx)
                if self.escape:
                    # skirt the obstacle: aim to the escape side of the goal and reward moving at all
                    to_goal += self.escape * 1.3
                herr = abs(math.atan2(math.sin(to_goal - final_yaw), math.cos(to_goal - final_yaw)))
                progress = dist - math.hypot(target[0] - gx, target[1] - gy)
                if self.escape:
                    s = 1.2 * v - 0.8 * herr + 0.6 * min(clear, 1.0)
                else:
                    s = 2.0 * progress - 0.8 * herr + 0.6 * min(clear, 1.0) + 0.2 * v
                if s > best_s:
                    best_s, best = s, (float(v), float(w))
        if self.escape:
            self.escape_left -= dt
            if self.escape_left <= 0:
                self.escape = 0.0
        stalled = best_s == -1e9 or (best[0] < 0.02 and dist > 0.35)
        if stalled and not self.escape:
            self.stall += dt
            if self.stall > self.STALL_S:
                # no forward progress for a while: skirt the obstacle on the side with more room
                near = obs[np.hypot(obs[:, 0], obs[:, 1]) < 1.5] if obs.size else obs
                self.escape = (-1.0 if near[:, 1].mean() > 0 else 1.0) if near.size else 1.0
                self.escape_left = self.ESCAPE_S
                self.stall = 0.0
        elif not stalled:
            self.stall = 0.0
        if stalled:
            self.v, self.w = 0.0, self.escape * 0.9
            return self.v, self.w, self._clearance(obstacles)
        self.v, self.w = best
        if dist < 0.35:
            self.v *= dist / 0.35
        return self.v, self.w, self._clearance(obstacles)

    @staticmethod
    def _clearance(obs: np.ndarray) -> float:
        return float(np.sqrt((obs ** 2).sum(axis=1)).min()) if obs.size else 9.0
