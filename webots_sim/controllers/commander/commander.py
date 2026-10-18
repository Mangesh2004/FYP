"""Commander / base station. Listens to agent heartbeats, keeps a mission picture, broadcasts
incident priorities, and (only for the 'central' baseline) assigns tasks.

Agents never depend on it in the decentralized methods; when the supervisor cuts its radio
(power off), the agents notice the missing heartbeats and continue peer-to-peer.
"""
from __future__ import annotations

import json
import math
import random
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller import Robot  # noqa: E402

from swarm.blueprint import Blueprint  # noqa: E402
from swarm.io_util import atomic_write_text  # noqa: E402
from swarm.radio import Radio  # noqa: E402
from swarm.tasks import TaskBook  # noqa: E402


def main() -> None:
    robot = Robot()
    ts = int(robot.getBasicTimeStep())
    while not robot.getCustomData():
        if robot.step(ts) == -1:
            return
    cfg = json.loads(robot.getCustomData())
    run_dir = Path(cfg["run_dir"])
    tx, rx = robot.getDevice("radio_tx"), robot.getDevice("radio_rx")
    rx.enable(ts)
    radio = Radio(tx, rx, "CMD", float(cfg.get("loss", 0.0)), random.Random(cfg.get("seed", 0)).randrange(1 << 30))
    bp = Blueprint()
    book = TaskBook(bp)
    report = cfg.get("incident_report", {})
    priorities = {rid: (1.0 if rid == report.get("room") else 0.4) for rid in bp.rooms}
    agents: dict[str, dict] = {}
    central = cfg.get("method") == "central"
    assign: dict[str, str] = {}
    log_path = run_dir / "commander.jsonl"

    def log(ev: str, **kw) -> None:
        rec = {"t": round(robot.getTime(), 3), "ev": ev, **kw}
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    log("commander_online", incident=report, method=cfg.get("method"))
    step = 0
    while robot.step(ts) != -1:
        step += 1
        now = robot.getTime()
        for msg in radio.poll():
            if msg.get("type") != "hb":
                continue
            agents[msg["src"]] = {"pose": msg["pose"], "cap": msg["cap"], "task": msg.get("task"),
                                  "state": msg.get("state"), "heard": now}
            book.merge(msg)
        if step % 31 != 0:
            continue
        msg = {"type": "cmd", "t": round(now, 2), "priorities": priorities}
        if central:
            live = {n: a for n, a in agents.items() if now - a["heard"] < 6.0}
            open_tasks = book.open_tasks()
            for n in list(assign):
                if assign[n] in book.done or n not in live:
                    assign.pop(n)
            taken = set(assign.values())
            for n, a in sorted(live.items()):
                if n in assign:
                    continue
                cands = [t for t in open_tasks if t["id"] not in taken and book.eligible(t, a["cap"])]
                if cands:
                    best = min(cands, key=lambda t: (t["kind"] != "verify", math.dist(a["pose"][:2], t["pos"])))
                    assign[n] = best["id"]
                    taken.add(best["id"])
                    log("assign", agent=n, task=best["id"])
            msg["assign"] = assign
            caps = {a["cap"] for a in live.values()}
            msg["complete"] = bool(live) and all(not book.feasible(t, caps) for t in open_tasks)
        radio.send(msg)
        if step % 155 == 0:
            atomic_write_text(run_dir / "commander_picture.json", json.dumps({
                "t": round(now, 2), "agents": agents, "done": book.done,
                "findings": list(book.findings.values()), "blocked": sorted(book.blocked)}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
