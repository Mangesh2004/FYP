"""Same-seed Webots episodes proving that each injected fault changes behaviour.

Runs 8 headless episodes sequentially to bound detector memory. Select with: pytest -m webots
Set FYP_FAULT_DIR to reuse an existing output directory instead of a fresh one.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SEED = 3
EPISODES = [("normal", "cbba"), ("commander_cut", "cbba"), ("normal", "central"), ("commander_cut", "central"),
            ("robot_killed", "cbba"), ("robot_killed", "static"), ("door_blocked", "cbba"), ("loss20", "cbba")]
DOORS = json.loads((ROOT / "config" / "blueprint.json").read_text(encoding="utf-8"))["doors"]

pytestmark = pytest.mark.webots


def batch(out: Path, conds: list[str], method: str, env: list[str] = ()) -> None:
    cmd = [sys.executable, str(ROOT / "tools" / "run_batch.py"), "--out", str(out), "--seeds", str(SEED),
           "--conditions", *conds, "--methods", method, "--workers", "1"]
    if env:
        cmd += ["--env", *env]
    subprocess.run(cmd, check=True, cwd=ROOT)


def door_used_after(run_dir: Path, t_min: float) -> str:
    """First corridor door a robot drove through after t_min in the unfaulted run."""
    traj = json.loads((run_dir / "trajectory.json").read_text(encoding="utf-8"))
    best = None
    for pts in traj.values():
        for t, x, y in pts:
            if t < t_min:
                continue
            for did in ("D1", "D2", "D3", "D4"):
                if abs(x - DOORS[did]["pos"][0]) < 0.4 and abs(y - DOORS[did]["pos"][1]) < 0.4:
                    if best is None or t < best[0]:
                        best = (t, did)
    assert best, "robots never used a corridor door after the fault time"
    return best[1]


@pytest.fixture(scope="module")
def runs() -> dict:
    out = Path(os.environ.get("FYP_FAULT_DIR") or ROOT / "runs" / "fault_tests" / time.strftime("%Y%m%d_%H%M%S"))
    jobs = {}
    for cond, method in EPISODES:
        if cond != "door_blocked":
            jobs.setdefault(method, []).append(cond)
    for method, conds in jobs.items():
        batch(out, conds, method)
    door = door_used_after(out / "runs" / f"s{SEED:03d}_normal_cbba", 20.0)
    batch(out, ["door_blocked"], "cbba", [f"FYP_FAULT_DOOR={door}"])
    res = {}
    for cond, method in EPISODES:
        d = out / "runs" / f"s{SEED:03d}_{cond}_{method}"
        assert not list(d.glob("crash_*.txt")), f"controller crashed in {d}"
        res[(cond, method)] = {"m": json.loads((d / "metrics.json").read_text(encoding="utf-8")),
                               "truth": json.loads((d / "truth.json").read_text(encoding="utf-8")), "dir": d}
    return res


def test_commander_cut_makes_agents_switch_to_peer_only_and_finish(runs):
    normal, cut = runs[("normal", "cbba")]["m"], runs[("commander_cut", "cbba")]["m"]
    assert normal["peer_only_time"] is None
    assert cut["peer_only_time"] is not None and 30.0 < cut["peer_only_time"] < 40.0
    assert cut["rooms_inspected"] == 4 and cut["mission_complete_time"] is not None


def test_central_baseline_stalls_when_commander_is_cut(runs):
    normal, cut = runs[("normal", "central")]["m"], runs[("commander_cut", "central")]["m"]
    assert normal["rooms_inspected"] == 4
    assert cut["rooms_inspected"] < 4
    assert cut["timed_out"]


def test_robot_kill_is_detected_and_survivor_takes_over(runs):
    killed = runs[("robot_killed", "cbba")]
    m, fault = killed["m"], killed["truth"]["faults"][0]
    assert m["peer_lost_time"] is not None and fault["t"] < m["peer_lost_time"] < fault["t"] + 12.0
    assert m["rooms_inspected"] == 4
    assert m["tasks_done_after_fault"] >= 1
    static = runs[("robot_killed", "static")]["m"]
    assert static["rooms_inspected"] < 4, "static split cannot reallocate the dead robot's rooms"


def test_door_debris_is_detected_and_route_changes(runs):
    door, normal = runs[("door_blocked", "cbba")], runs[("normal", "cbba")]["m"]
    m = door["m"]
    assert m["door_blocked_detected_time"] is not None and m["door_blocked_detected_time"] > 15.0
    assert m["rooms_inspected"] == 4
    d_true = sum(m["true_distance_m"].values())
    d_norm = sum(normal["true_distance_m"].values())
    assert abs(d_true - d_norm) > 1.0, (d_true, d_norm)


def test_packet_loss_lowers_delivery_ratio(runs):
    normal, lossy = runs[("normal", "cbba")]["m"], runs[("loss20", "cbba")]["m"]
    r_norm = normal["messages_received"] / normal["messages_sent"]
    r_loss = lossy["messages_received"] / lossy["messages_sent"]
    assert r_loss < 0.9 * r_norm, (r_loss, r_norm)
    assert lossy["rooms_inspected"] == 4


@pytest.mark.parametrize('condition',['normal','loss20','commander_cut','robot_killed','door_blocked'])
def test_cbba_mission_completes_without_contact_or_shared_door_occupancy(runs,condition):
    metrics=runs[(condition,'cbba')]['m']
    assert metrics['rooms_inspected']==metrics['rooms_total']==4
    assert metrics['mission_complete_time'] is not None
    assert not metrics['timed_out']
    assert metrics['safety']['contact_steps']==0
    assert metrics['safety']['doorway_overlap_steps']==0
    assert max(metrics['safety']['max_tilt_deg'].values())<10
