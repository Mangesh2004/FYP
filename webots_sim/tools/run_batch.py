"""Run many headless Webots episodes in parallel and collect metrics.json from each.

Usage:
    python tools/run_batch.py --out D:/FYP/experiments/pilot --seeds 0-9 \
        --conditions normal loss20 commander_cut robot_killed door_blocked \
        --methods static greedy cbba central --workers 3

Each episode writes to OUT/runs/s{seed}_{condition}_{method}/. Finished episodes (metrics.json present)
are skipped, so an interrupted batch can be resumed with the same command.
"""
from __future__ import annotations

import argparse
import csv
import json
import queue
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from run_webots import run  # noqa: E402

WORLD = ROOT / "worlds" / "facility.wbt"
FIELDS = ["seed", "condition", "method", "sim_time", "timed_out", "hazards_total", "hazards_confirmed",
          "victims_total", "victims_found", "false_confirmations", "localization_error_m", "first_detection_time",
          "rooms_inspected", "rooms_total",
          "duplicate_inspections", "all_rooms_time", "mission_complete_time", "recovery_time_after_kill",
          "messages_sent", "messages_received", "distance_m", "peer_only_time", "peer_lost_time",
          "door_blocked_detected_time", "replans", "stuck_recoveries", "tasks_done_after_fault",
          "suspected_hazards", "wall_s", "exit_code", "crashed"]


def parse_seeds(spec: list[str]) -> list[int]:
    out = []
    for s in spec:
        if "-" in s:
            a, b = s.split("-")
            out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(s))
    return out


def episode_dir(out: Path, seed: int, cond: str, method: str) -> Path:
    return out / "runs" / f"s{seed:03d}_{cond}_{method}"


def worker(idx: int, jobs: queue.Queue, out: Path, max_time: float, lock: threading.Lock, done: list,
           extra: dict[str, str], port_base: int = 1330) -> None:
    port = port_base + idx * 7
    while True:
        try:
            seed, cond, method = jobs.get_nowait()
        except queue.Empty:
            return
        d = episode_dir(out, seed, cond, method)
        d.mkdir(parents=True, exist_ok=True)
        env = {"FYP_SEED": str(seed), "FYP_CONDITION": cond, "FYP_METHOD": method, "FYP_RUN_DIR": str(d),
               "FYP_MAX_TIME": str(max_time), "FYP_BATCH": "1", **extra}
        t0 = time.time()
        code = run(WORLD, timeout=max_time * 1.5 + 120, port=port, log=d / "webots_console.txt", gui=False,
                   extra_env=env)
        wall = time.time() - t0
        (d / "batch_info.json").write_text(json.dumps({"exit_code": code, "wall_s": round(wall, 1), "port": port}),
                                           encoding="utf-8")
        with lock:
            done.append((seed, cond, method))
            ok = (d / "metrics.json").exists()
            print(f"[{len(done)}] s{seed} {cond} {method}: exit {code}, {wall:.0f}s wall, "
                  f"{'metrics ok' if ok else 'NO METRICS'}", flush=True)


def collect(out: Path) -> list[dict]:
    rows = []
    for d in sorted((out / "runs").glob("s*_*_*")):
        m = d / "metrics.json"
        if not m.exists():
            continue
        r = json.loads(m.read_text(encoding="utf-8"))
        info = d / "batch_info.json"
        if info.exists():
            r.update(json.loads(info.read_text(encoding="utf-8")))
        r["crashed"] = ",".join(sorted(p.stem for p in d.glob("crash_*.txt")))
        rows.append({k: r.get(k) for k in FIELDS})
    with open(out / "results.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--seeds", nargs="+", default=["0-9"])
    ap.add_argument("--conditions", nargs="+", default=["normal", "loss20", "commander_cut", "robot_killed", "door_blocked"])
    ap.add_argument("--methods", nargs="+", default=["static", "greedy", "cbba", "central"])
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--max-time", type=float, default=300.0)
    ap.add_argument("--env", nargs="*", default=[], help="extra KEY=VALUE for the supervisor, e.g. FYP_FAULT_DOOR=D2")
    ap.add_argument("--port-base", type=int, default=1330,
                    help="first Webots port; concurrent batches need disjoint ranges (workers x 7)")
    a = ap.parse_args()
    extra = dict(kv.split("=", 1) for kv in a.env)
    out = Path(a.out)
    (out / "runs").mkdir(parents=True, exist_ok=True)
    jobs: queue.Queue = queue.Queue()
    todo = 0
    for seed in parse_seeds(a.seeds):
        for cond in a.conditions:
            for method in a.methods:
                if not (episode_dir(out, seed, cond, method) / "metrics.json").exists():
                    jobs.put((seed, cond, method))
                    todo += 1
    print(f"{todo} episodes to run with {a.workers} workers", flush=True)
    lock, done = threading.Lock(), []
    t0 = time.time()
    threads = [threading.Thread(target=worker, args=(i, jobs, out, a.max_time, lock, done, extra, a.port_base)) for i in range(a.workers)]
    for t in threads:
        t.start()
        time.sleep(3)
    for t in threads:
        t.join()
    rows = collect(out)
    print(f"batch finished in {time.time() - t0:.0f}s; {len(rows)} episodes with metrics in {out / 'results.csv'}")


if __name__ == "__main__":
    main()
