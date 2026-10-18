"""Task book shared by gossip: inspection and verification tasks, completions and findings.

Priority and suitability re-implement the weighted scoring from the teammate's coopai package
(priority: urgency 0.25, deadline 0.20, environment 0.35, importance 0.15, uncertainty 0.05;
suitability: battery 0.45, travel 0.35, information 0.20).
"""
from __future__ import annotations

import math

ROOM_IMPORTANCE = {"R1": 0.8, "R2": 0.5, "R3": 0.7, "R4": 0.8}
SPEED = 0.33
INSPECT_TIME = 30.0
VERIFY_TIME = 10.0


def finding_key(kind: str, pos) -> str:
    return f"{kind}@{round(pos[0] * 2) / 2:.1f},{round(pos[1] * 2) / 2:.1f}"


class TaskBook:
    def __init__(self, blueprint) -> None:
        self.bp = blueprint
        self.tasks: dict[str, dict] = {}
        for rid in blueprint.rooms:
            self.tasks[f"inspect:{rid}"] = {"id": f"inspect:{rid}", "kind": "inspect", "room": rid,
                                            "pos": blueprint.room_center(rid), "need": "any"}
        self.done: dict[str, dict] = {}
        self.findings: dict[str, dict] = {}
        self.blocked: set[str] = set()
        self.urgency: dict[str, float] = {}
        self.coverage: dict[str, float] = {}

    # ---------------------------------------------------------------- updates
    def add_verify(self, hazard: str, pos, room: str | None, by: str, t: float) -> str | None:
        for tid, tk in self.tasks.items():
            if tk["kind"] == "verify" and tk["hazard"] == hazard and math.dist(tk["pos"], pos) < 1.6:
                return None
        tid = f"verify:{finding_key(hazard, pos)}"
        self.tasks[tid] = {"id": tid, "kind": "verify", "hazard": hazard, "room": room,
                           "pos": [round(pos[0], 2), round(pos[1], 2)],
                           "need": "thermal" if hazard == "fire" else "gas", "by": by, "t": t}
        return tid

    def mark_done(self, tid: str, by: str, t: float, result: str = "ok") -> None:
        if result == 'unreachable':
            return  # a failed attempt must stay open for retry/reallocation
        if tid not in self.done:
            self.done[tid] = {"by": by, "t": t, "result": result}

    def add_finding(self, f: dict) -> bool:
        for k, g in self.findings.items():
            if g["kind"] == f["kind"] and math.dist(g["pos"], f["pos"]) < 1.2:
                if f.get("confirmed") and not g.get("confirmed"):
                    self.findings[k] = f
                    return True
                return False
        self.findings[finding_key(f["kind"], f["pos"])] = f
        return True

    def merge(self, hb: dict) -> list[str]:
        """Merge a peer heartbeat. Returns ids of newly learned verification tasks."""
        new = []
        for tk in hb.get("tasks", []):
            if tk["id"] not in self.tasks:
                self.tasks[tk["id"]] = tk
                new.append(tk["id"])
        for tid, d in hb.get("done", {}).items():
            if tid not in self.done:
                self.done[tid] = d
        for f in hb.get("findings", []):
            self.add_finding(f)
        self.blocked |= set(hb.get("blocked", []))
        return new

    def gossip(self) -> dict:
        return {
            "tasks": [t for t in self.tasks.values() if t["kind"] == "verify"],
            "done": self.done,
            "findings": list(self.findings.values()),
            "blocked": sorted(self.blocked),
        }

    # ---------------------------------------------------------------- queries
    def open_tasks(self) -> list[dict]:
        return [t for tid, t in self.tasks.items() if tid not in self.done]

    def eligible(self, task: dict, capability: str) -> bool:
        return task["need"] in ("any", capability)

    def priority(self, task: dict) -> float:
        if task["kind"] == "verify":
            urgency, deadline, env, importance, unc = 1.0, 0.8, 1.0, 0.9, 0.5
        else:
            rid = task["room"]
            urgency = self.urgency.get(rid, 0.4)
            deadline = 0.5
            suspected = any(f.get("room") == rid for f in self.findings.values())
            env = 1.0 if suspected else 0.3
            importance = ROOM_IMPORTANCE.get(rid, 0.6)
            unc = 1.0 - self.coverage.get(rid, 0.0)
        return 0.25 * urgency + 0.20 * deadline + 0.35 * env + 0.15 * importance + 0.05 * unc

    def suitability(self, task: dict, capability: str, travel_m: float, battery: float = 1.0) -> float:
        travel = 1.0 / (1.0 + travel_m / 10.0)
        if task["kind"] == "verify":
            info = 1.0 if task["need"] == capability else 0.0
        else:
            info = 0.5
            for f in self.findings.values():
                if f.get("room") == task["room"] and not f.get("confirmed"):
                    need = "thermal" if f["kind"] == "fire" else "gas" if f["kind"] == "gas" else None
                    if need == capability:
                        info = 1.0
        return 0.45 * battery + 0.35 * travel + 0.20 * info

    def duration(self, task: dict) -> float:
        return INSPECT_TIME if task["kind"] == "inspect" else VERIFY_TIME

    def travel(self, pos, task: dict) -> float:
        if task["kind"] == "inspect":
            return self.bp.route_length(list(pos), task["room"], self.blocked)
        room = task.get("room")
        if room and room in self.bp.rooms:
            return self.bp.route_length(list(pos), room, self.blocked) + 0.5
        return math.dist(pos, task["pos"])

    def feasible(self, task: dict, alive_caps: set[str]) -> bool:
        return task["need"] == "any" or task["need"] in alive_caps
