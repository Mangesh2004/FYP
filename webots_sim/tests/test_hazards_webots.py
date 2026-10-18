"""Full camera + specialty sensor missions, with observer-only truth scoring."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from tests.test_faults_webots import door_used_after

ROOT=Path(__file__).resolve().parents[1]
SEEDS=[0,1,3,5,8,11,1000,1001]
CASES=[(seed,'normal') for seed in SEEDS]+[(3,'loss20'),(3,'door_blocked')]
pytestmark=pytest.mark.hazard_webots


@pytest.fixture(scope='module')
def hazard_runs():
    out=Path(os.environ.get('FYP_HAZARD_DIR') or ROOT/'runs/hazard_tests'/time.strftime('%Y%m%d_%H%M%S'))
    def batch(seeds,conditions,extra=()):
        subprocess.run([sys.executable,str(ROOT/'tools/run_batch.py'),'--out',str(out),
            '--seeds',*map(str,seeds),'--conditions',*conditions,'--methods','cbba','--workers','1',
            '--max-time','600','--env','FYP_PERCEPTION_TRACE=1',*extra],cwd=ROOT,check=True)
    batch(SEEDS,['normal'])
    batch([3],['loss20'])
    door=door_used_after(out/'runs/s003_normal_cbba',20)
    batch([3],['door_blocked'],[f'FYP_FAULT_DOOR={door}'])
    result={}
    for seed,condition in CASES:
        d=out/'runs'/f's{seed:03d}_{condition}_cbba'
        assert not list(d.glob('crash_*.txt')),d
        assert json.loads((d/'batch_info.json').read_text())['exit_code']==0,d
        result[(seed,condition)]=json.loads((d/'metrics.json').read_text())
    return result


@pytest.mark.parametrize('seed,condition',CASES)
def test_all_hazards_confirmed_without_false_confirmations_or_contact(hazard_runs,seed,condition):
    m=hazard_runs[(seed,condition)]
    assert not m['timed_out'],m
    assert m['rooms_inspected']==m['rooms_total']==4,m
    assert m['mission_complete_time'] is not None,m
    assert m['hazards_confirmed']==m['hazards_total'],m
    assert m['false_confirmations']==0,m
    assert m['safety']['contact_steps']==m['safety']['doorway_overlap_steps']==0,m
    assert max(m['safety']['max_tilt_deg'].values())<10,m
