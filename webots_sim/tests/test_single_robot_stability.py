"""Physical regression tests: one Pioneer, production navigation, measured contacts/tilt."""
import json
import itertools
import math
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.single_robot
@pytest.mark.parametrize("case,yaw", [
    ("primitives", 0), ("wall", 0), ("low_obstacle", 0), ("recover", 0), ("doorway", 0), ("facility", 0),
    ("low_obstacle", -0.35), ("low_obstacle", 0.35), ("recover", -0.15), ("recover", 0.15),
    ("doorway", -0.25), ("doorway", 0.25),
    ("close_turn", 0),
])
def test_single_robot_motion_has_no_obstacle_contacts_or_tip(case, yaw, tmp_path):
    out = tmp_path / case
    subprocess.run([sys.executable, str(ROOT / "tools" / "check_stability.py"),
                    "--case", case, "--yaw", str(yaw), "--out", str(out)], cwd=ROOT, check=True, timeout=200)
    result = json.loads((out / "result.json").read_text())
    assert result["contact_steps"] == 0, result
    assert result["max_tilt_deg"] < 10, result
    assert 0.08 < result["z_range"][0] <= result["z_range"][1] < 0.12, result
    assert result["completed"], result  # safe stopping alone must not conceal a frozen robot
    if case == "primitives":
        assert result["final"][0] - result["initial"][0] > 1.0, "robot never drove"
        samples = [json.loads(line) for line in (out / "samples.jsonl").read_text().splitlines()]
        groups = [(label, list(rows)) for label, rows in itertools.groupby(samples, key=lambda s: s["phase"])]
        for label, rows in groups:
            xy0, xy1 = rows[0]["position"][:2], rows[-1]["position"][:2]
            if label == "straight":
                assert 1.0 < math.dist(xy0, xy1) < 1.35
            elif label == "turn":
                yaws = [r["pose_estimate"][2] for r in rows]
                total = sum(math.atan2(math.sin(b-a), math.cos(b-a)) for a, b in zip(yaws, yaws[1:]))
                assert 2.8 < total < 3.5
                assert math.dist(xy0, xy1) < 0.08
            elif label == "reverse":
                heading = rows[0]["pose_estimate"][2]
                along = (xy1[0]-xy0[0])*math.cos(heading) + (xy1[1]-xy0[1])*math.sin(heading)
                assert along < -0.35
            elif label == "stop":
                settled = rows[len(rows)//3:]
                assert math.dist(settled[0]["position"][:2], settled[-1]["position"][:2]) < 0.03
