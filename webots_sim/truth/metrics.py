"""Score an episode: compare what the agents reported against ground truth."""
from __future__ import annotations

import json
import math
from pathlib import Path

MATCH_RADIUS = 1.6


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def score(run_dir: Path, truth: dict, sim: dict) -> dict:
    events = {n: _read_jsonl(run_dir / f"agent_{n}.jsonl") for n in ("A", "B")}
    status = {}
    for n in ("A", "B"):
        p = run_dir / f"status_{n}.json"
        if p.exists():
            try:
                status[n] = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                status[n] = {}

    # union of confirmed findings across agents (deduplicated by position and kind)
    confirmed = []
    for n, evs in events.items():
        for e in evs:
            if e.get("ev") == "finding" and e.get("confirmed"):
                confirmed.append({**e, "agent": n})
    hazards = truth["hazards"]
    victims = truth["victims"]

    def match(kind_true: str, pos) -> dict | None:
        best = None
        for c in confirmed:
            if c["kind"] != kind_true:
                continue
            d = math.dist(c["pos"], pos)
            if d <= MATCH_RADIUS and (best is None or c["t"] < best["t"]):
                best = c
        return best

    hazard_hits, first_times, loc_err = 0, [], []
    for h in hazards:
        m = match(h["type"], h["pos"])
        if m:
            hazard_hits += 1
            first_times.append(m["t"])
            loc_err.append(math.dist(m["pos"], h["pos"]))
    victim_hits = 0
    for v in victims:
        m = match("victim", v["pos"])
        if m:
            victim_hits += 1
            first_times.append(m["t"])
            loc_err.append(math.dist(m["pos"], v["pos"]))

    false_conf = 0
    seen = []
    for c in confirmed:
        if any(c["kind"] == s["kind"] and math.dist(c["pos"], s["pos"]) < MATCH_RADIUS for s in seen):
            continue
        seen.append(c)
        truths = [h["pos"] for h in hazards if h["type"] == c["kind"]] if c["kind"] in ("fire", "gas") \
            else [v["pos"] for v in victims]
        if not any(math.dist(c["pos"], p) <= MATCH_RADIUS for p in truths):
            false_conf += 1

    inspected: dict[str, list[str]] = {}
    for n, evs in events.items():
        for e in evs:
            if e.get("ev") == "room_inspected":
                inspected.setdefault(e["room"], []).append(n)
    rooms_total = 4
    rooms_done = len(inspected)
    duplicate = sum(1 for r, who in inspected.items() if len(set(who)) > 1)

    complete_t = None
    for n, evs in events.items():
        for e in evs:
            if e.get("ev") == "mission_complete":
                complete_t = e["t"] if complete_t is None else min(complete_t, e["t"])
    all_done_t = None
    if rooms_done == rooms_total:
        all_done_t = max(min(e["t"] for n2, evs2 in events.items() for e in evs2
                             if e.get("ev") == "room_inspected" and e["room"] == r) for r in inspected)

    recovery = None
    for fault in truth["faults"]:
        if fault["kind"] == "robot_killed":
            dead = fault["target"]
            alive = "B" if dead == "A" else "A"
            alive_after = [e for e in events[alive] if e.get("ev") == "room_inspected" and e["t"] > fault["t"]]
            if rooms_done == rooms_total and alive_after:
                recovery = max(e["t"] for e in alive_after) - fault["t"]

    def first(ev: str, **match) -> float | None:
        ts = [e["t"] for evs in events.values() for e in evs
              if e.get("ev") == ev and all(e.get(k) == v for k, v in match.items())]
        return min(ts) if ts else None

    def count(ev: str) -> int:
        return sum(1 for evs in events.values() for e in evs if e.get("ev") == ev)

    door_fault = next((f for f in truth["faults"] if f["kind"] == "door_blocked"), None)
    tasks_after_fault = None
    if truth["faults"]:
        tf = truth["faults"][0]["t"]
        tasks_after_fault = sum(1 for evs in events.values() for e in evs if e.get("ev") == "task_done" and e["t"] > tf)

    msgs_tx = sum(status.get(n, {}).get("msgs_tx", 0) for n in ("A", "B"))
    msgs_rx = sum(status.get(n, {}).get("msgs_rx", 0) for n in ("A", "B"))
    dist = sum(status.get(n, {}).get("odom_dist", 0.0) for n in ("A", "B"))

    return {
        "seed": truth["seed"],
        "condition": truth["condition"],
        "method": truth["method"],
        "sim_time": sim["t"],
        "timed_out": sim["timed_out"],
        "hazards_total": len(hazards),
        "hazards_confirmed": hazard_hits,
        "victims_total": len(victims),
        "victims_found": victim_hits,
        "false_confirmations": false_conf,
        "localization_error_m": round(sum(loc_err) / len(loc_err), 3) if loc_err else None,
        "first_detection_time": min(first_times) if first_times else None,
        "rooms_inspected": rooms_done,
        "rooms_total": rooms_total,
        "duplicate_inspections": duplicate,
        "all_rooms_time": all_done_t,
        "mission_complete_time": complete_t,
        "recovery_time_after_kill": recovery,
        "messages_sent": msgs_tx,
        "messages_received": msgs_rx,
        "distance_m": round(dist, 2),
        "true_distance_m": sim.get("true_distance", {}),
        "peer_only_time": first("mode", mode="peer_only"),
        "peer_lost_time": first("peer_lost"),
        "door_blocked_detected_time": first("door_blocked_detected", door=door_fault["door"]) if door_fault else None,
        "replans": count("replan"),
        "stuck_recoveries": count("stuck_recovery"),
        "tasks_done_after_fault": tasks_after_fault,
        "suspected_hazards": count("suspected_hazard"),
    }
