"""Task allocation methods. All of them run inside one agent and only use its task book,
its own pose and what peers told it over the radio.

    static   fixed room split, no reallocation
    greedy   nearest unclaimed eligible task, claims gossiped, conflicts resolved by route length
    cbba     consensus-based bundle algorithm (Choi, Brunet, How 2009) for two agents
    central  commander assigns tasks; agents wait without a commander (fragile baseline)
    learned  shared PPO policy scores candidate tasks (see learning/)
"""
from __future__ import annotations

import math
from pathlib import Path

from .tasks import SPEED, TaskBook

PEER_TIMEOUT = 6.0
STATIC_SPLIT = {"A": {"R1", "R3"}, "B": {"R2", "R4"}}


class Allocator:
    name = "base"

    def __init__(self, me: str, capability: str, book: TaskBook) -> None:
        self.me = me
        self.cap = capability
        self.book = book
        self.current: str | None = None

    def on_peer(self, msg: dict, now: float) -> None:
        pass

    def on_command(self, msg: dict, now: float) -> None:
        pass

    def outgoing(self) -> dict:
        return {}

    def choose(self, now: float, pos, peers: dict) -> str | None:
        raise NotImplementedError

    def my_scope_done(self, alive_caps: set[str]) -> bool:
        return all(not self.book.feasible(t, alive_caps) for t in self.book.open_tasks())

    def value(self, task: dict, pos) -> float:
        travel = self.book.travel(pos, task)
        return self.book.priority(task) * self.book.suitability(task, self.cap, travel)


class StaticAllocator(Allocator):
    name = "static"

    def mine(self, task: dict) -> bool:
        if task["kind"] == "inspect":
            return task["room"] in STATIC_SPLIT[self.me]
        return task["need"] == self.cap

    def choose(self, now, pos, peers):
        if self.current and self.current not in self.book.done:
            return self.current
        cands = [t for t in self.book.open_tasks() if self.mine(t)]
        if not cands:
            self.current = None
            return None
        cands.sort(key=lambda t: (t["kind"] != "verify", t["id"]))
        self.current = cands[0]["id"]
        return self.current

    def my_scope_done(self, alive_caps):
        return not any(self.mine(t) for t in self.book.open_tasks())


class GreedyAllocator(Allocator):
    name = "greedy"

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.claim_cost = 0.0
        self.peer_claims: dict[str, tuple[str, float, float]] = {}

    def on_peer(self, msg, now):
        c = msg.get("claim")
        if c:
            self.peer_claims[msg["src"]] = (c["task"], c["cost"], now)
        else:
            self.peer_claims.pop(msg["src"], None)

    def outgoing(self):
        return {"claim": {"task": self.current, "cost": round(self.claim_cost, 2)}} if self.current else {}

    def choose(self, now, pos, peers):
        live = {p: c for p, c in self.peer_claims.items() if now - c[2] < PEER_TIMEOUT}
        if self.current and self.current not in self.book.done:
            for p, (task, cost, _) in live.items():
                if task == self.current and (cost < self.claim_cost - 0.3 or (abs(cost - self.claim_cost) <= 0.3 and p < self.me)):
                    self.current = None
                    break
            if self.current:
                return self.current
        taken = {task for task, _, _ in live.values()}
        cands = [t for t in self.book.open_tasks() if self.book.eligible(t, self.cap) and t["id"] not in taken]
        if not cands:
            self.current = None
            return None
        best = min(cands, key=lambda t: (t["kind"] != "verify", self.book.travel(pos, t)))
        self.current = best["id"]
        self.claim_cost = self.book.travel(pos, best)
        return self.current


class CBBAAllocator(Allocator):
    name = "cbba"
    LAMBDA = 0.985
    BUNDLE_MAX = 4

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.y: dict[str, float] = {}
        self.z: dict[str, str | None] = {}
        self.bundle: list[str] = []
        self.path: list[str] = []
        self.peer_heard: dict[str, float] = {}
        self.executing: str | None = None
        self.consensus_updates = 0

    # path score with time-discounted rewards
    def _path_score(self, path: list[str], pos) -> float:
        t, here, total = 0.0, list(pos), 0.0
        for tid in path:
            task = self.book.tasks[tid]
            t += self.book.travel(here, task) / SPEED
            v = self.book.priority(task) * self.book.suitability(task, self.cap, self.book.travel(pos, task))
            if tid == self.executing:
                v *= 1.6
            total += v * (self.LAMBDA ** t)
            t += self.book.duration(task)
            here = task["pos"]
        return total

    def _build(self, pos) -> None:
        open_ids = {t["id"] for t in self.book.open_tasks()}
        self.bundle = [b for b in self.bundle if b in open_ids and self.z.get(b) == self.me]
        self.path = [p for p in self.path if p in self.bundle]
        base = self._path_score(self.path, pos)
        while len(self.bundle) < self.BUNDLE_MAX:
            best = None
            for t in self.book.open_tasks():
                tid = t["id"]
                if tid in self.bundle or not self.book.eligible(t, self.cap):
                    continue
                for k in range(len(self.path) + 1):
                    cand = self.path[:k] + [tid] + self.path[k:]
                    c = self._path_score(cand, pos) - base
                    yj, zj = self.y.get(tid, 0.0), self.z.get(tid)
                    if zj is None or zj == self.me:
                        wins = True
                    else:
                        wins = c > yj + 1e-6 or (abs(c - yj) <= 1e-6 and self.me < zj)
                    if wins and (best is None or c > best[0]):
                        best = (c, tid, k)
            if best is None or best[0] <= 0:
                break
            c, tid, k = best
            self.bundle.append(tid)
            self.path.insert(k, tid)
            self.y[tid], self.z[tid] = c, self.me
            base = self._path_score(self.path, pos)

    def on_peer(self, msg, now):
        k = msg["src"]
        self.peer_heard[k] = now
        theirs = msg.get("cbba")
        if not theirs:
            return
        lost = set()
        for tid, (zk, yk) in theirs.items():
            zi, yi = self.z.get(tid), self.y.get(tid, 0.0)
            if zk == k:
                if zi == self.me:
                    if yk > yi + 1e-6 or (abs(yk - yi) <= 1e-6 and k < self.me):
                        self.z[tid], self.y[tid] = k, yk
                        lost.add(tid)
                else:
                    self.z[tid], self.y[tid] = k, yk
            elif zk == self.me:
                if zi == k:
                    self.z[tid], self.y[tid] = None, 0.0
            elif zk is None:
                if zi == k:
                    self.z[tid], self.y[tid] = None, 0.0
        if lost:
            self.consensus_updates += 1
            for i, tid in enumerate(self.bundle):
                if tid in lost:
                    for later in self.bundle[i + 1:]:
                        if self.z.get(later) == self.me:
                            self.z[later], self.y[later] = None, 0.0
                    self.bundle = self.bundle[:i]
                    break
            self.path = [p for p in self.path if p in self.bundle]

    def outgoing(self):
        return {"cbba": {tid: [self.z.get(tid), round(self.y.get(tid, 0.0), 5)]
                         for tid in self.z if tid not in self.book.done}}

    def choose(self, now, pos, peers):
        for p, heard in self.peer_heard.items():
            if now - heard > PEER_TIMEOUT:
                for tid, w in list(self.z.items()):
                    if w == p:
                        self.z[tid], self.y[tid] = None, 0.0
        for tid in list(self.z):
            if tid in self.book.done:
                self.z.pop(tid, None)
                self.y.pop(tid, None)
        # re-bid every cycle so scores track the robot's current position
        for tid in self.bundle:
            if self.z.get(tid) == self.me:
                self.z[tid], self.y[tid] = None, 0.0
        self.bundle, self.path = [], []
        self._build(pos)
        self.current = self.path[0] if self.path else None
        return self.current

    def set_executing(self, tid: str | None) -> None:
        self.executing = tid


class CentralAllocator(Allocator):
    name = "central"

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.assigned: str | None = None
        self.cmd_complete = False

    def on_command(self, msg, now):
        a = msg.get("assign", {})
        if self.me in a:
            self.assigned = a[self.me]
        self.cmd_complete = bool(msg.get("complete"))

    def choose(self, now, pos, peers):
        if self.assigned and self.assigned not in self.book.done:
            self.current = self.assigned
        else:
            self.current = None
        return self.current

    def my_scope_done(self, alive_caps):
        return self.cmd_complete


class LearnedAllocator(Allocator):
    """Scores each candidate task with the shared PPO policy trained in the abstract environment."""
    name = "learned"

    def __init__(self, *a, policy_path: Path | None = None, **kw) -> None:
        super().__init__(*a, **kw)
        from learning.policy_runtime import TaskPolicy  # torch-only module, no simulator access
        self.policy = TaskPolicy(policy_path)
        self.peer_task: dict[str, tuple[str | None, float]] = {}

    def on_peer(self, msg, now):
        self.peer_task[msg["src"]] = (msg.get("task"), now)

    def choose(self, now, pos, peers):
        from learning.features import build_obs

        if self.current and self.current not in self.book.done:
            return self.current  # the policy decides when the agent is idle, as in training
        live_peer_tasks = {t for t, h in self.peer_task.values() if now - h < PEER_TIMEOUT and t}
        peer_pos = None
        for p in peers.values():
            if p.get("alive"):
                peer_pos = p.get("pos")
        obs, mask, owner = build_obs(self.book, self.cap, list(pos), peer_pos, live_peer_tasks, self.current)
        if not mask.any():
            self.current = None
            return None
        self.current = owner[self.policy.pick_slot(obs, mask)]
        return self.current


def make_allocator(method: str, me: str, capability: str, book: TaskBook) -> Allocator:
    table = {"static": StaticAllocator, "greedy": GreedyAllocator, "cbba": CBBAAllocator,
             "central": CentralAllocator, "learned": LearnedAllocator}
    if method not in table:
        raise ValueError(f"unknown allocation method {method}")
    return table[method](me, capability, book)
