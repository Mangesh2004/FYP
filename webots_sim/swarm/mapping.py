"""Per-agent log-odds occupancy grid built from the agent's own lidar and pose estimate."""
from __future__ import annotations

import base64
import zlib

import numpy as np
from scipy import ndimage

L_OCC = 0.9
L_FREE = -0.45
L_MIN, L_MAX = -4.0, 4.0
OCC_T = 0.7
FREE_T = -0.7


class OccupancyGrid:
    def __init__(self, bounds: list[float], res: float = 0.1) -> None:
        self.x0, self.y0, self.x1, self.y1 = bounds
        self.res = res
        self.w = int(round((self.x1 - self.x0) / res))
        self.h = int(round((self.y1 - self.y0) / res))
        self.own = np.zeros((self.h, self.w), dtype=np.float32)
        self.peer = np.zeros((self.h, self.w), dtype=np.float32)
        self._sent = np.zeros((self.h, self.w), dtype=np.int8)
        self._fused_cache: np.ndarray | None = None
        # obstacles the lidar plane cannot see (e.g. a powered-off robot below the scan height)
        self.static = np.zeros((self.h, self.w), dtype=bool)
        self._static_src: dict[str, tuple[float, float, float]] = {}

    # ------------------------------------------------------------ coordinates
    def to_cell(self, x, y):
        cx = np.floor((np.asarray(x) - self.x0) / self.res).astype(np.int32)
        cy = np.floor((np.asarray(y) - self.y0) / self.res).astype(np.int32)
        return cx, cy

    def to_world(self, cx, cy):
        return self.x0 + (np.asarray(cx) + 0.5) * self.res, self.y0 + (np.asarray(cy) + 0.5) * self.res

    def inside(self, cx, cy):
        return (cx >= 0) & (cx < self.w) & (cy >= 0) & (cy < self.h)

    # ------------------------------------------------------------ update
    def integrate_scan(self, x: float, y: float, yaw: float, ranges: np.ndarray, angles: np.ndarray,
                       max_range: float, free_cap: float = 4.5) -> None:
        r = np.asarray(ranges, dtype=np.float32)
        hit = np.isfinite(r) & (r < max_range * 0.98) & (r > 0.12)
        rr = np.where(hit, np.minimum(r, max_range), np.minimum(free_cap, max_range))
        a = yaw + angles
        step = self.res * 0.7
        n = int(np.ceil(rr.max() / step)) if rr.size else 0
        if n == 0:
            return
        s = np.arange(n, dtype=np.float32) * step
        mask = s[None, :] < (rr[:, None] - self.res)
        px = x + s[None, :] * np.cos(a)[:, None]
        py = y + s[None, :] * np.sin(a)[:, None]
        cx, cy = self.to_cell(px[mask], py[mask])
        ok = self.inside(cx, cy)
        free_idx = np.unique(cy[ok] * self.w + cx[ok])
        ex, ey = x + r[hit] * np.cos(a[hit]), y + r[hit] * np.sin(a[hit])
        ocx, ocy = self.to_cell(ex, ey)
        ok2 = self.inside(ocx, ocy)
        occ_idx = np.unique(ocy[ok2] * self.w + ocx[ok2])
        flat = self.own.reshape(-1)
        free_idx = np.setdiff1d(free_idx, occ_idx, assume_unique=True)
        flat[free_idx] += L_FREE
        flat[occ_idx] += L_OCC
        np.clip(flat, L_MIN, L_MAX, out=flat)
        self._fused_cache = None

    # ------------------------------------------------------------ fused views
    def fused(self) -> np.ndarray:
        if self._fused_cache is None:
            self._fused_cache = np.where(np.abs(self.own) >= np.abs(self.peer), self.own, self.peer)
        return self._fused_cache

    def occupied(self) -> np.ndarray:
        return (self.fused() > OCC_T) | self.static

    def known(self) -> np.ndarray:
        f = self.fused()
        return (f > OCC_T) | (f < FREE_T) | self.static

    # ------------------------------------------------------------ non-lidar obstacles
    def set_static(self, key: str, x: float, y: float, r: float) -> None:
        self._static_src[key] = (x, y, r)
        self._rebuild_static()

    def clear_static(self, key: str) -> None:
        if self._static_src.pop(key, None) is not None:
            self._rebuild_static()

    def _rebuild_static(self) -> None:
        self.static[:] = False
        yy, xx = np.mgrid[0:self.h, 0:self.w]
        wx, wy = self.to_world(xx, yy)
        for x, y, r in self._static_src.values():
            self.static |= (wx - x) ** 2 + (wy - y) ** 2 <= r * r

    def static_points(self, x: float, y: float, radius: float) -> np.ndarray:
        if not self._static_src:
            return np.zeros((0, 2))
        cy, cx = np.nonzero(self.static)
        wx, wy = self.to_world(cx, cy)
        m = (wx - x) ** 2 + (wy - y) ** 2 <= radius * radius
        return np.stack([wx[m], wy[m]], axis=1)

    def known_fraction(self, rect: list[float], margin: float = 0.3) -> float:
        x0, y0, x1, y1 = rect
        c0x, c0y = self.to_cell(x0 + margin, y0 + margin)
        c1x, c1y = self.to_cell(x1 - margin, y1 - margin)
        k = self.known()[int(c0y):int(c1y), int(c0x):int(c1x)]
        return float(k.mean()) if k.size else 0.0

    def inflated(self, radius: float) -> np.ndarray:
        r = int(np.ceil(radius / self.res))
        yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
        disk = (xx * xx + yy * yy) <= r * r
        return ndimage.binary_dilation(self.occupied(), structure=disk)

    def frontiers(self, rect: list[float] | None = None) -> np.ndarray:
        """Free cells adjacent to unknown cells; returns array of (x, y) world points."""
        f = self.fused()
        free = f < FREE_T
        unknown = np.abs(f) <= 0.3
        near_unknown = ndimage.binary_dilation(unknown, structure=np.ones((3, 3), bool))
        fr = free & near_unknown
        if rect is not None:
            x0, y0, x1, y1 = rect
            c0x, c0y = self.to_cell(x0 + 0.35, y0 + 0.35)
            c1x, c1y = self.to_cell(x1 - 0.35, y1 - 0.35)
            m = np.zeros_like(fr)
            m[int(c0y):int(c1y), int(c0x):int(c1x)] = True
            fr &= m
        cy, cx = np.nonzero(fr)
        if cx.size == 0:
            return np.zeros((0, 2))
        wx, wy = self.to_world(cx, cy)
        return np.stack([wx, wy], axis=1)

    # ------------------------------------------------------------ sharing
    def encode_delta(self, full: bool = False) -> dict | None:
        q = np.clip(np.round(self.own * 16), -127, 127).astype(np.int8)
        changed = np.abs(q.astype(np.int16) - self._sent.astype(np.int16)) >= 8 if not full else (q != 0)
        idx = np.flatnonzero(changed).astype(np.uint32)
        if idx.size == 0:
            return None
        vals = q.reshape(-1)[idx]
        self._sent.reshape(-1)[idx] = vals
        return {
            "idx": base64.b64encode(zlib.compress(np.diff(idx, prepend=np.uint32(0)).astype(np.uint32).tobytes(), 6)).decode(),
            "val": base64.b64encode(zlib.compress(vals.tobytes(), 6)).decode(),
            "n": int(idx.size),
        }

    def apply_peer_delta(self, msg: dict) -> int:
        d = np.frombuffer(zlib.decompress(base64.b64decode(msg["idx"])), dtype=np.uint32)
        idx = np.cumsum(d.astype(np.int64))
        vals = np.frombuffer(zlib.decompress(base64.b64decode(msg["val"])), dtype=np.int8).astype(np.float32) / 16.0
        ok = idx < self.peer.size
        self.peer.reshape(-1)[idx[ok]] = vals[ok]
        self._fused_cache = None
        return int(ok.sum())
