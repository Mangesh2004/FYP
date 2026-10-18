"""Seeded episode randomization: hazards, victims, blockages, spawns, noise, packet loss, faults."""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SLOTS = json.loads((ROOT / "truth" / "slots.json").read_text(encoding="utf-8"))
BLUEPRINT = json.loads((ROOT / "config" / "blueprint.json").read_text(encoding="utf-8"))

CONDITIONS = ("random", "normal", "loss20", "commander_cut", "robot_killed", "door_blocked", "demo")
CORRIDOR_DOORS = ["D1", "D2", "D3", "D4"]
DOOR_PAIR = {"D1": "D2", "D2": "D1", "D3": "D4", "D4": "D3"}


def _pick_slots(rng: random.Random, room: str, taken: list[list[float]], k: int = 1) -> list[list[float]]:
    cands = [p for p in SLOTS["rooms"][room] if all(math.dist(p, q) > 1.1 for q in taken)]
    rng.shuffle(cands)
    return cands[:k]


def make_episode(seed: int, condition: str = "random", method: str = "cbba", max_time: float = 300.0,
                 fault_door: str | None = None) -> dict:
    """fault_door overrides which corridor door the door_blocked condition blocks."""
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition {condition}")
    if fault_door is not None and fault_door not in CORRIDOR_DOORS:
        raise ValueError(f"fault door must be one of {CORRIDOR_DOORS}")
    rng = random.Random(seed)
    rooms = list(BLUEPRINT["rooms"].keys())
    taken: list[list[float]] = []

    n_haz = rng.choice([1, 2, 2, 3])
    haz_rooms = rng.sample(rooms, n_haz)
    hazards = []
    counts = {"fire": 0, "gas": 0}
    for room in haz_rooms:
        kind = rng.choice(["fire", "gas"])
        if counts[kind] >= 3:
            kind = "gas" if kind == "fire" else "fire"
        pos = _pick_slots(rng, room, taken)
        if not pos:
            continue
        taken.append(pos[0])
        hazards.append({"id": f"{kind.upper()}_{counts[kind]}", "type": kind, "room": room,
                        "pos": pos[0], "yaw": rng.uniform(-math.pi, math.pi),
                        "strength": rng.uniform(0.8, 1.2)})
        counts[kind] += 1

    victims = []
    for i in range(rng.choice([0, 1, 1, 2])):
        room = rng.choice(rooms)
        pos = _pick_slots(rng, room, taken)
        if not pos:
            continue
        taken.append(pos[0])
        victims.append({"id": f"VICTIM_{i}", "room": room, "pos": pos[0], "yaw": rng.uniform(-math.pi, math.pi)})

    distractors = []
    for i in rng.sample(range(4), rng.choice([2, 3, 4])):
        room = rng.choice(rooms)
        pos = _pick_slots(rng, room, taken)
        if not pos:
            continue
        taken.append(pos[0])
        distractors.append({"id": f"DISTRACT_{i}", "room": room, "pos": pos[0], "yaw": rng.uniform(-math.pi, math.pi)})

    spawn_idx = rng.sample(range(4), 2)
    spawns = {"A": SLOTS["spawns"][spawn_idx[0]], "B": SLOTS["spawns"][spawn_idx[1]]}
    noise_scale = round(rng.uniform(0.6, 1.4), 3)

    # draws below are always made so the shared part of the episode stays identical across conditions
    rand_loss = round(rng.uniform(0.0, 0.3), 3)
    rand_blocked = []
    for d in rng.sample(CORRIDOR_DOORS, rng.choice([0, 1, 1, 2])):
        if DOOR_PAIR[d] not in rand_blocked:
            rand_blocked.append(d)
    late_door = rng.choice(CORRIDOR_DOORS)
    kill_target = rng.choice(["A", "B"])
    rand_fault = rng.choice(["none", "commander_cut", "robot_killed", "door_blocked"])
    intel_room = hazards[0]["room"] if hazards and rng.random() < 0.7 else rng.choice(rooms)

    loss, blocked, faults = 0.0, [], []
    if condition == "random":
        loss, blocked = rand_loss, rand_blocked
        if rand_fault == "commander_cut":
            faults.append({"t": 30.0, "kind": "commander_cut"})
        elif rand_fault == "robot_killed":
            faults.append({"t": 50.0, "kind": "robot_killed", "target": kill_target})
        elif rand_fault == "door_blocked":
            door = late_door if DOOR_PAIR[late_door] not in blocked and late_door not in blocked else None
            if door:
                faults.append({"t": 15.0, "kind": "door_blocked", "door": door})
    elif condition == "loss20":
        loss = 0.2
    elif condition == "commander_cut":
        faults.append({"t": 30.0, "kind": "commander_cut"})
    elif condition == "robot_killed":
        faults.append({"t": 50.0, "kind": "robot_killed", "target": kill_target})
    elif condition == "door_blocked":
        faults.append({"t": 15.0, "kind": "door_blocked", "door": fault_door or late_door})
    elif condition == "demo":
        loss = 0.1
        faults += [{"t": 15.0, "kind": "door_blocked", "door": fault_door or late_door},
                   {"t": 30.0, "kind": "commander_cut"},
                   {"t": 110.0, "kind": "robot_killed", "target": kill_target}]

    return {
        "seed": seed,
        "condition": condition,
        "method": method,
        "max_time": max_time,
        "hazards": hazards,
        "victims": victims,
        "distractors": distractors,
        "blocked_doors": blocked,
        "spawns": spawns,
        "spawn_yaw": {"A": rng.uniform(-0.4, 0.4), "B": rng.uniform(-0.4, 0.4)},
        "noise_scale": noise_scale,
        "packet_loss": loss,
        "faults": faults,
        "incident_report": {"room": intel_room, "text": f"Smoke alarm reported in {intel_room}"},
    }


def debris_pose(door_id: str) -> tuple[list[float], float]:
    d = BLUEPRINT["doors"][door_id]
    x, y = d["pos"]
    yaw = 0.0 if d["wall"] == "v" else math.pi / 2
    if d["wall"] == "h":
        y += 0.42 if y > 0 else -0.42
    return [x, y], yaw
