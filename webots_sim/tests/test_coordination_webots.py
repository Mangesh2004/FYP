import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.pair_robot
@pytest.mark.parametrize('case,loss', [('opposed',0),('opposed',.2),('same_direction',0),('same_direction',.2),('staging_obstacle',0),('staging_obstacle',.2)])
def test_two_robots_finish_without_contact_and_serialize_doorway(case, loss, tmp_path):
    subprocess.run([sys.executable, str(ROOT/'tools/check_coordination.py'), '--case', case,
                    '--loss', str(loss), '--out', str(tmp_path)], cwd=ROOT, check=True, timeout=180)
    result = json.loads((tmp_path/'result.json').read_text())
    assert result['contact_steps'] == 0, result
    assert result['max_tilt_deg'] < 10, result
    assert result['completed'], result
    assert result['doorway_overlap_steps'] == 0, result
