"""Measure the abstract env's parameters from finished Webots episodes (offline analysis, not agent code).

Usage:
    python -m learning.calibrate D:/FYP/experiments/pilot/runs [more run dirs ...]

Fits task duration = overhead + route_length / speed for inspect and verify tasks (route length from the
blueprint, start pose from the supervisor trajectory), and measures how often a present hazard was
suspected after its room was inspected, how often a room produced a false suspicion, and how often
verification confirmed a true hazard / rejected a false one. Writes learning/calibration.json.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from swarm.blueprint import Blueprint  # noqa: E402

OUT = ROOT / "learning" / "calibration.json"


def read_jsonl(p: Path) -> list[dict]:
    out = []
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def pose_at(traj: list, t: float):
    best = min(traj, key=lambda p: abs(p[0] - t))
    return best[1:3]


def fit(xs, ys):
    xs, ys = np.array(xs), np.array(ys)
    A = np.stack([np.ones_like(xs), xs], 1)
    (a, b), *_ = np.linalg.lstsq(A, ys, rcond=None)
    resid = ys - (a + b * xs)
    return float(a), float(b), float(resid.std())


def main() -> None:
    dirs = [Path(d) for d in sys.argv[1:]]
    bp = Blueprint()
    insp_x, insp_y, ver_x, ver_y = [], [], [], []
    present, suspected, rooms_insp, false_susp = 0, 0, 0, 0
    conf_true, n_true_ver, rej_false, n_false_ver = 0, 0, 0, 0
    episodes = 0
    for root in dirs:
        for d in sorted(root.glob("s*_*")):
            if not (d / "metrics.json").exists() or list(d.glob("crash_*.txt")):
                continue
            truth = json.loads((d / "truth.json").read_text(encoding="utf-8"))
            traj = json.loads((d / "trajectory.json").read_text(encoding="utf-8"))
            episodes += 1
            hz = truth["hazards"]
            inspected_rooms = set()
            susp = []
            for n in ("A", "B"):
                evs = read_jsonl(d / f"agent_{n}.jsonl")
                starts = {}
                for e in evs:
                    if e["ev"] == "task_start":
                        starts[e["task"]] = e["t"]
                    elif e["ev"] == "task_done" and e.get("result") in ("ok", "confirmed", "rejected") and e["task"] in starts:
                        t0 = starts[e["task"]]
                        p = pose_at(traj[n], t0)
                        if e["task"].startswith("inspect:"):
                            room = e["task"].split(":")[1]
                            insp_x.append(bp.route_length(list(p), room, set(truth["blocked_doors"])))
                            insp_y.append(e["t"] - t0)
                    elif e["ev"] == "finding" and "sensor" in e:
                        true = any(h["type"] == e["kind"] and math.dist(h["pos"], e["pos"]) < 1.6 for h in hz)
                        if true:
                            n_true_ver += 1
                            conf_true += bool(e["confirmed"])
                        else:
                            n_false_ver += 1
                            rej_false += not e["confirmed"]
                    elif e["ev"] == "suspected_hazard":
                        susp.append(e)
                    elif e["ev"] == "room_inspected":
                        inspected_rooms.add(e["room"])
                    if e["ev"] == "task_done" and e["task"].startswith("verify:") and e["task"] in starts:
                        t0 = starts[e["task"]]
                        p = pose_at(traj[n], t0)
                        xy = [float(v) for v in e["task"].split("@")[1].split(",")]
                        room = bp.room_of(*xy)
                        if room in bp.rooms:
                            ver_x.append(bp.route_length(list(p), room, set(truth["blocked_doors"])) + 0.5)
                            ver_y.append(e["t"] - t0)
            for h in hz:
                if h["room"] in inspected_rooms:
                    present += 1
                    suspected += any(s["kind"] == h["type"] and math.dist(s["pos"], h["pos"]) < 1.6 for s in susp)
            for r in inspected_rooms:
                rooms_insp += 1
                false_susp += sum(1 for s in susp if s.get("room") == r and
                                  not any(h["type"] == s["kind"] and math.dist(h["pos"], s["pos"]) < 1.6 for h in hz))
    ia, ib, isd = fit(insp_x, insp_y)
    va, vb, vsd = fit(ver_x, ver_y)
    cal = {
        "episodes": episodes, "sources": [str(d) for d in dirs],
        "inspect": {"n": len(insp_x), "overhead_s": ia, "s_per_m": ib, "speed_mps": 1 / ib if ib > 0 else None, "resid_sd_s": isd},
        "verify": {"n": len(ver_x), "overhead_s": va, "s_per_m": vb, "resid_sd_s": vsd},
        "p_suspect_given_present": suspected / present if present else None, "n_present": present,
        "false_suspicions_per_room": false_susp / rooms_insp if rooms_insp else None, "n_rooms": rooms_insp,
        "p_confirm_true": conf_true / n_true_ver if n_true_ver else None, "n_true_verify": n_true_ver,
        "p_reject_false": rej_false / n_false_ver if n_false_ver else None, "n_false_verify": n_false_ver,
    }
    OUT.write_text(json.dumps(cal, indent=2), encoding="utf-8")
    print(json.dumps(cal, indent=2))


if __name__ == "__main__":
    main()
