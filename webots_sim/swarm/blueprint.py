"""Public floor plan (rooms, doors, bounds) that responders receive before entering the building."""
from __future__ import annotations

import heapq
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Blueprint:
    def __init__(self, path: Path | None = None) -> None:
        data = json.loads((path or ROOT / "config" / "blueprint.json").read_text(encoding="utf-8"))
        self.data = data
        self.bounds = data["map_bounds"]
        self.rooms = data["rooms"]
        self.doors = data["doors"]
        self.corridor = data["corridor"]
        self.entrance = data["entrance"]["pos"]

    def room_of(self, x: float, y: float) -> str | None:
        for rid, r in self.rooms.items():
            x0, y0, x1, y1 = r["rect"]
            if x0 < x < x1 and y0 < y < y1:
                return rid
        x0, y0, x1, y1 = self.corridor
        if x0 - 3.5 < x < x1 and y0 < y < y1:
            return "CORRIDOR"
        return None

    def room_center(self, rid: str) -> list[float]:
        return list(self.rooms[rid]["center"])

    def viewpoints(self, rid: str) -> list[list[float]]:
        x0, y0, x1, y1 = self.rooms[rid]["rect"]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        # stay on the corridor side of the room centre line, away from wall-mounted furniture
        cy += 0.4 if cy < 0 else -0.4
        q = (x1 - x0) / 4
        return [[cx, cy], [cx - q, cy], [cx + q, cy]]

    def route_length(self, start: list[float], goal_room: str, blocked: set[str] | None = None) -> float:
        """Shortest route length through door centres, used for allocation cost estimates."""
        blocked = blocked or set()
        here = self.room_of(*start) or "CORRIDOR"
        goal = self.room_center(goal_room)
        if here == goal_room:
            return math.dist(start, goal)
        nodes: dict[str, list[float]] = {"__start": list(start), "__goal": goal}
        edges: dict[str, list[tuple[str, float]]] = {}

        def add(a: str, b: str) -> None:
            d = math.dist(nodes[a], nodes[b])
            edges.setdefault(a, []).append((b, d))
            edges.setdefault(b, []).append((a, d))

        for did, d in self.doors.items():
            if did in blocked:
                continue
            nodes[did] = d["pos"]
        for did, d in self.doors.items():
            if did in blocked:
                continue
            for did2, d2 in self.doors.items():
                if did2 <= did or did2 in blocked:
                    continue
                if set(d["between"]) & set(d2["between"]):
                    add(did, did2)
            if here in d["between"]:
                add("__start", did)
            if goal_room in d["between"]:
                add(did, "__goal")
        dist = {"__start": 0.0}
        pq = [(0.0, "__start")]
        while pq:
            c, n = heapq.heappop(pq)
            if n == "__goal":
                return c
            if c > dist.get(n, 1e9):
                continue
            for m, w in edges.get(n, []):
                nc = c + w
                if nc < dist.get(m, 1e9):
                    dist[m] = nc
                    heapq.heappush(pq, (nc, m))
        return 1e6
