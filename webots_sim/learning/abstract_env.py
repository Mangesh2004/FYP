"""Abstract two-robot mission environment for learning task allocation.

Same semantics as the Webots mission, at the level of task decisions:
  * rooms R1-R4 of the same blueprint, route lengths from the same door graph (blocked doors honoured)
  * travel and task durations, detection, false-suspicion and verification rates measured from Webots
    episodes (learning/calibration.json, produced by learning/calibrate.py)
  * each agent keeps its own TaskBook; the peer's completions, claims, pose and new verification tasks
    arrive over a lossy heartbeat channel (1 Hz, first successful heartbeat delivers)
  * faults: robot power kill at t=50 s, packet loss, blocked doors discovered only when a route uses them
Decisions are event driven: an agent picks a task when it becomes idle. The observation is
learning.features.build_obs, exactly what the Webots 'learned' allocator computes.

Classes:
  MissionSim        core simulator
  TaskAllocAEC      PettingZoo AEC environment (agents "A", "B")
  SharedPolicyEnv   Gymnasium view for parameter-shared independent PPO (one policy, both agents' turns)
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from learning.features import N_SLOTS, OBS_DIM, build_obs
from swarm.blueprint import Blueprint
from swarm.tasks import TaskBook

ROOT = Path(__file__).resolve().parents[1]
CAL_PATH = ROOT / "learning" / "calibration.json"
CAPS = {"A": "thermal", "B": "gas"}
NEED = {"fire": "thermal", "gas": "gas"}
DOOR_PAIR = {"D1": "D2", "D2": "D1", "D3": "D4", "D4": "D3"}
MAX_TIME = 300.0
PEER_TIMEOUT = 6.0
CONDITIONS = ("normal", "loss20", "robot_killed", "door_blocked", "random")

DEFAULT_CAL = {"inspect": {"overhead_s": 41.0, "s_per_m": 2.77, "resid_sd_s": 16.0},
               "verify": {"overhead_s": 2.0, "s_per_m": 2.7, "resid_sd_s": 8.0},
               "p_suspect_given_present": 0.95, "false_suspicions_per_room": 0.18,
               "p_confirm_true": 0.71, "p_reject_false": 0.93}


def load_calibration() -> dict:
    if CAL_PATH.exists():
        return json.loads(CAL_PATH.read_text(encoding="utf-8"))
    return DEFAULT_CAL


class MissionSim:
    REWARD = {"time_per_s": -1 / 60, "room": 1.0, "hazard": 1.5, "false_conf": -1.0, "duplicate": -0.5,
              "success": 3.0, "unfinished": -1.0}

    def __init__(self, cal: dict | None = None) -> None:
        self.cal = cal or load_calibration()
        self.bp = Blueprint()

    # ------------------------------------------------------------------ episode
    def reset(self, seed: int, condition: str = "random") -> str | None:
        self.rng = random.Random(seed)
        r = self.rng
        if condition == "random":
            condition = r.choice(("normal", "loss20", "robot_killed", "door_blocked"))
        self.condition = condition
        rooms = list(self.bp.rooms)
        self.hazards = []
        for room in r.sample(rooms, r.choice([1, 2, 2, 3])):
            self.hazards.append({"type": r.choice(["fire", "gas"]), "room": room, "pos": self._point_in(room)})
        self.blocked = set()
        for d in r.sample(list(DOOR_PAIR), r.choice([0, 1, 1, 2])):
            if DOOR_PAIR[d] not in self.blocked:
                self.blocked.add(d)
        self.loss = {"loss20": 0.2}.get(condition, r.uniform(0.0, 0.1))
        self.kill = None
        if condition == "robot_killed":
            self.kill = (50.0, r.choice(["A", "B"]))
        if condition == "door_blocked":
            free = [d for d in DOOR_PAIR if d not in self.blocked and DOOR_PAIR[d] not in self.blocked]
            if free:
                self.blocked.add(r.choice(free))
        report = self.hazards[0]["room"] if r.random() < 0.7 else r.choice(rooms)
        self.t = 0.0
        self.ag = {}
        for n in ("A", "B"):
            book = TaskBook(self.bp)
            book.urgency[report] = 1.0
            self.ag[n] = {"pos": [r.uniform(-6.0, -4.5), r.uniform(-0.6, 0.6)], "book": book, "alive": True,
                          "task": None, "busy_until": 0.0, "idle": True, "peer_pos": None, "peer_task": None,
                          "peer_heard": 0.0, "peer_dead": False}
        self.msgs: list[tuple[float, str, str, object]] = []  # (deliver_t, receiver, kind, payload)
        self.inspected: dict[str, list[str]] = {}
        self.done_global: set[str] = set()
        self.confirmed: list[dict] = []
        self.false_conf = 0
        self.duplicates = 0
        self.reward_acc = 0.0
        self.finished = False
        self.success = False
        for n in ("A", "B"):
            self._broadcast(n, "pose", list(self.ag[n]["pos"]))
        return self._next_decision()

    def _point_in(self, room: str) -> list[float]:
        x0, y0, x1, y1 = self.bp.rooms[room]["rect"]
        return [round(self.rng.uniform(x0 + 1.0, x1 - 1.0), 2), round(self.rng.uniform(y0 + 1.0, y1 - 1.0), 2)]

    def _delay(self) -> float:
        k = 1
        while self.rng.random() < self.loss and k < 60:
            k += 1
        return float(k)

    def _broadcast(self, src: str, kind: str, payload) -> None:
        if not self.ag[src]["alive"]:
            return
        dst = "B" if src == "A" else "A"
        self.msgs.append((self.t + self._delay(), dst, kind, payload))

    # ------------------------------------------------------------------ decisions
    def deciding(self) -> str | None:
        return self._decider

    def observation(self, n: str):
        a = self.ag[n]
        peer = "B" if n == "A" else "A"
        peer_pos = None if a["peer_dead"] else a["peer_pos"]
        peer_tasks = {a["peer_task"]} if a["peer_task"] and not a["peer_dead"] else set()
        return build_obs(a["book"], CAPS[n], a["pos"], peer_pos, peer_tasks, a["task"])

    def decide(self, action: int) -> tuple[float, bool]:
        """Apply the deciding agent's choice, advance to the next decision. Returns (reward, done)."""
        n = self._decider
        obs, mask, owner = self.observation(n)
        if action not in owner:
            action = int(np.flatnonzero(mask)[0])
        tid = owner[action]
        self._start(n, tid)
        self.reward_acc = 0.0
        self._decider = self._next_decision()
        return self.reward_acc, self.finished

    def _start(self, n: str, tid: str) -> None:
        a = self.ag[n]
        task = a["book"].tasks[tid]
        true_route = self.bp.route_length(a["pos"], task["room"], self.blocked) if task.get("room") in self.bp.rooms \
            else math.dist(a["pos"], task["pos"])
        believed = a["book"].travel(a["pos"], task)
        extra = 0.0
        if true_route > believed + 0.5:
            # drove to a blocked door, saw the debris, turned around
            extra = 8.0
            for d in self.blocked:
                a["book"].blocked.add(d)
            self._broadcast(n, "blocked", sorted(self.blocked))
        c = self.cal["inspect"] if task["kind"] == "inspect" else self.cal["verify"]
        a["unreachable"] = true_route >= 1e5
        if a["unreachable"]:
            dur = 20.0  # planner fails a few times, then the agent gives the room up (as in Webots)
        else:
            dur = c["overhead_s"] + c["s_per_m"] * true_route + extra + self.rng.gauss(0, 0.5 * c["resid_sd_s"])
        a["task"] = tid
        a["idle"] = False
        a["busy_until"] = self.t + max(5.0, dur)
        a["target_pos"] = list(task["pos"]) if task["kind"] == "verify" else self.bp.room_center(task["room"])
        self._broadcast(n, "claim", tid)

    def _complete(self, n: str) -> None:
        a = self.ag[n]
        tid = a["task"]
        a["idle"] = True
        if tid is None:  # end of a wait
            return
        book = a["book"]
        task = book.tasks[tid]
        a["task"] = None
        self._broadcast(n, "claim", None)
        if a.get("unreachable"):
            book.mark_done(tid, n, self.t, "unreachable")
            self._broadcast(n, "done", tid)
            self.done_global.add(tid)
            return
        a["pos"] = list(a["target_pos"])
        self._broadcast(n, "pose", list(a["pos"]))
        if tid in self.done_global and task["kind"] == "inspect":
            self.duplicates += 1
            self.reward_acc += self.REWARD["duplicate"]
        book.mark_done(tid, n, self.t)
        self._broadcast(n, "done", tid)
        if task["kind"] == "inspect":
            room = task["room"]
            if room not in self.inspected:
                self.reward_acc += self.REWARD["room"]
            self.inspected.setdefault(room, []).append(n)
            new = []
            for h in self.hazards:
                if h["room"] == room and self.rng.random() < self.cal["p_suspect_given_present"]:
                    new.append((h["type"], h["pos"], True))
            if self.rng.random() < self.cal["false_suspicions_per_room"]:
                new.append((self.rng.choice(["fire", "gas"]), self._point_in(room), False))
            for kind, pos, real in new:
                vt = book.add_verify(kind, pos, room, n, self.t)
                if vt:
                    book.tasks[vt]["_real"] = real
                    book.add_finding({"kind": kind, "pos": pos, "room": room, "confirmed": False})
                    self._broadcast(n, "verify", dict(book.tasks[vt]))
        else:
            real = task.get("_real", False)
            if real:
                ok = self.rng.random() < self.cal["p_confirm_true"]
                if ok and not any(c["id"] == tid for c in self.confirmed):
                    self.confirmed.append({"id": tid, "t": self.t})
                    self.reward_acc += self.REWARD["hazard"]
            elif self.rng.random() > self.cal["p_reject_false"]:
                self.false_conf += 1
                self.reward_acc += self.REWARD["false_conf"]
        self.done_global.add(tid)

    def _deliver(self, dst: str, kind: str, payload) -> None:
        a = self.ag[dst]
        if not a["alive"]:
            return
        a["peer_heard"] = self.t
        a["peer_dead"] = False
        book = a["book"]
        if kind == "pose":
            a["peer_pos"] = payload
        elif kind == "claim":
            a["peer_task"] = payload
        elif kind == "done":
            book.mark_done(payload, "peer", self.t)
            if a["task"] == payload:
                # peer finished it first: abort like the Webots agent does (done_by_peer)
                a["task"], a["idle"] = None, True
        elif kind == "verify":
            if payload["id"] not in book.tasks:
                book.tasks[payload["id"]] = dict(payload)
                book.add_finding({"kind": payload["hazard"], "pos": payload["pos"], "room": payload["room"],
                                  "confirmed": False})
        elif kind == "blocked":
            book.blocked |= set(payload)

    def _alive_caps(self) -> set[str]:
        return {CAPS[n] for n in self.ag if self.ag[n]["alive"]}

    def _all_done(self) -> bool:
        caps = self._alive_caps()
        if any(f"inspect:{r}" not in self.done_global for r in self.bp.rooms):
            return False
        for n in self.ag:
            for t in self.ag[n]["book"].tasks.values():
                if t["kind"] == "verify" and t["id"] not in self.done_global and t["need"] in caps:
                    return False
        return True

    def _next_decision(self) -> str | None:
        while True:
            if self._all_done():
                self.finished, self.success = True, True
                self.reward_acc += self.REWARD["success"]
                return None
            for n in ("A", "B"):
                a = self.ag[n]
                if a["alive"] and not a["peer_dead"] and self.t - a["peer_heard"] > PEER_TIMEOUT:
                    a["peer_dead"] = True
                if a["alive"] and a["idle"]:
                    _, mask, _ = self.observation(n)
                    if mask.any():
                        return n
            # advance time to the next event
            events = []
            for n, a in self.ag.items():
                if a["alive"] and not a["idle"]:
                    events.append(a["busy_until"])
            if self.msgs:
                events.append(min(m[0] for m in self.msgs))
            if self.kill and self.ag[self.kill[1]]["alive"]:
                events.append(self.kill[0])
            heartbeat = math.floor(self.t) + 1.0
            events.append(heartbeat)
            t_next = min(events)
            if t_next >= MAX_TIME:
                self.reward_acc += self.REWARD["time_per_s"] * (MAX_TIME - self.t)
                self.t = MAX_TIME
                self.finished = True
                caps = self._alive_caps()
                unfinished = sum(1 for r in self.bp.rooms if f"inspect:{r}" not in self.done_global)
                unfinished += len({t["id"] for a in self.ag.values() for t in a["book"].tasks.values()
                                   if t["kind"] == "verify" and t["id"] not in self.done_global and t["need"] in caps})
                self.reward_acc += self.REWARD["unfinished"] * unfinished
                return None
            self.reward_acc += self.REWARD["time_per_s"] * (t_next - self.t)
            self.t = t_next
            if self.kill and self.ag[self.kill[1]]["alive"] and self.t >= self.kill[0]:
                k = self.ag[self.kill[1]]
                k["alive"], k["idle"], k["task"] = False, False, None
            for n, a in self.ag.items():
                if a["alive"] and not a["idle"] and a["busy_until"] <= self.t + 1e-9:
                    self._complete(n)
            due = [m for m in self.msgs if m[0] <= self.t + 1e-9]
            self.msgs = [m for m in self.msgs if m[0] > self.t + 1e-9]
            for _, dst, kind, payload in sorted(due, key=lambda m: m[0]):
                self._deliver(dst, kind, payload)
            if abs(self.t - heartbeat) < 1e-9:
                for n, a in self.ag.items():
                    if a["alive"]:
                        # periodic heartbeat refreshes liveness at the peer (subject to loss)
                        self._broadcast(n, "pose", list(a["pos"]) if a["idle"] else list(a.get("target_pos", a["pos"])))
                        if a["task"]:
                            self._broadcast(n, "claim", a["task"])

    def wait(self, n: str, dt: float = 5.0) -> None:
        """Deciding agent declines every task for dt seconds (used by the static baseline)."""
        a = self.ag[n]
        a["idle"], a["task"], a["busy_until"] = False, None, self.t + dt
        self.reward_acc = 0.0
        self._decider = self._next_decision()

    def metrics(self) -> dict:
        return {"condition": self.condition, "success": self.success, "time": self.t,
                "rooms": len(self.inspected), "hazards": len(self.hazards),
                "hazards_confirmed": len(self.confirmed), "false_conf": self.false_conf,
                "duplicates": self.duplicates}

    def start(self) -> None:
        self._decider = None

    _decider: str | None = None


def _reset(sim: MissionSim, seed: int, condition: str) -> None:
    sim._decider = sim.reset(seed, condition)


# ---------------------------------------------------------------------- PettingZoo AEC wrapper
try:
    from pettingzoo import AECEnv
except ImportError:  # pragma: no cover
    AECEnv = object


class TaskAllocAEC(AECEnv):
    metadata = {"name": "task_alloc_v0", "is_parallelizable": False}

    def __init__(self, condition: str = "random") -> None:
        super().__init__()
        self.sim = MissionSim()
        self.condition = condition
        self.possible_agents = ["A", "B"]
        self._obs_space = spaces.Dict({"observation": spaces.Box(-1.0, 10.0, (OBS_DIM,), np.float32),
                                       "action_mask": spaces.Box(0, 1, (N_SLOTS,), np.int8)})
        self._act_space = spaces.Discrete(N_SLOTS)

    def observation_space(self, agent):
        return self._obs_space

    def action_space(self, agent):
        return self._act_space

    def reset(self, seed=None, options=None):
        _reset(self.sim, seed if seed is not None else random.randrange(1 << 30), self.condition)
        self.agents = list(self.possible_agents)
        self.rewards = {a: 0.0 for a in self.agents}
        self._cumulative_rewards = {a: 0.0 for a in self.agents}
        self.terminations = {a: False for a in self.agents}
        self.truncations = {a: False for a in self.agents}
        self.infos = {a: {} for a in self.agents}
        self.agent_selection = self.sim._decider or "A"
        if self.sim._decider is None:
            self._finish()

    def observe(self, agent):
        obs, mask, _ = self.sim.observation(agent)
        return {"observation": obs.astype(np.float32), "action_mask": mask.astype(np.int8)}

    def _finish(self):
        for a in self.agents:
            self.terminations[a] = self.sim.t < MAX_TIME
            self.truncations[a] = self.sim.t >= MAX_TIME
            self.infos[a] = self.sim.metrics()

    def step(self, action):
        if self.terminations[self.agent_selection] or self.truncations[self.agent_selection]:
            self._was_dead_step(action)
            return
        self._cumulative_rewards[self.agent_selection] = 0.0
        r, done = self.sim.decide(int(action))
        for a in self.agents:
            self.rewards[a] = r
        if done:
            self._finish()
        else:
            self.agent_selection = self.sim._decider
        self._accumulate_rewards()


# ---------------------------------------------------------------------- SB3 view
class SharedPolicyEnv(gym.Env):
    """Each step is one decision of whichever agent is idle; both agents share the policy (IPPO with
    parameter sharing). Reward is the shared team reward accumulated until the next decision."""

    def __init__(self, condition: str = "random", seed: int = 0) -> None:
        self.sim = MissionSim()
        self.condition = condition
        self.observation_space = spaces.Box(-1.0, 10.0, (OBS_DIM,), np.float32)
        self.action_space = spaces.Discrete(N_SLOTS)
        self._seed = seed
        self._episode = 0

    def reset(self, seed=None, options=None):
        if seed is not None:
            self._seed = seed
        self._episode += 1
        _reset(self.sim, self._seed * 100003 + self._episode, self.condition)
        while self.sim._decider is None:  # degenerate episode with nothing to decide
            self._episode += 1
            _reset(self.sim, self._seed * 100003 + self._episode, self.condition)
        obs, _, _ = self.sim.observation(self.sim._decider)
        return obs.astype(np.float32), {}

    def action_masks(self) -> np.ndarray:
        _, mask, _ = self.sim.observation(self.sim._decider)
        return mask.astype(bool)

    def step(self, action):
        r, done = self.sim.decide(int(action))
        info = self.sim.metrics() if done else {}
        if done:
            obs = np.zeros(OBS_DIM, np.float32)
            return obs, r, self.sim.t < MAX_TIME, self.sim.t >= MAX_TIME, info
        obs, _, _ = self.sim.observation(self.sim._decider)
        return obs.astype(np.float32), r, False, False, info


# ---------------------------------------------------------------------- baseline policies on the same obs
def greedy_policy(obs: np.ndarray, mask: np.ndarray) -> int:
    f = obs[:N_SLOTS * 8].reshape(N_SLOTS, 8)
    best, key = None, None
    for s in np.flatnonzero(mask):
        k = (f[s, 3] == 0, f[s, 4] == 1, f[s, 1])  # verify first, avoid peer's task, nearest
        if key is None or k < key:
            best, key = int(s), k
    return best


def static_policy_for(agent: str):
    mine = {"A": {0, 2}, "B": {1, 3}}[agent]

    def pick(obs, mask):
        f = obs[:N_SLOTS * 8].reshape(N_SLOTS, 8)
        valid = list(np.flatnonzero(mask))
        ver = [s for s in valid if f[s, 3] == 1]
        if ver:
            return int(ver[0])
        own = [s for s in valid if s in mine]
        if own:
            return int(own[0])
        return None  # the static split never takes the peer's rooms
    return pick


def run_episode(policy, seed: int, condition: str, sim: MissionSim | None = None) -> dict:
    """policy(agent, obs, mask) -> slot, or None to wait 5 s."""
    sim = sim or MissionSim()
    _reset(sim, seed, condition)
    guard = 0
    while sim._decider is not None and guard < 3000:
        guard += 1
        n = sim._decider
        obs, mask, _ = sim.observation(n)
        a = policy(n, obs, mask)
        if a is None:
            sim.wait(n)
        else:
            sim.decide(int(a))
    return sim.metrics()
