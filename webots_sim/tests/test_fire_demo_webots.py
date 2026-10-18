"""Actual detector, measured sensors, autonomous discovery and stopped result."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.fire_demo
def test_robot_discovers_fire_confirms_with_measured_evidence_and_holds(tmp_path):
    subprocess.run([sys.executable,str(ROOT/'tools/run_fire_demo.py'),'--out',str(tmp_path),
                    '--port','1711'],cwd=ROOT,check=True,timeout=320)
    assert not list(tmp_path.glob('crash*'))
    r=json.loads((tmp_path/'demo_result.json').read_text())
    assert r['confirmed'] and r['error_m']<1.6,r
    evidence=r['verification']['last_attempt']
    assert evidence['confirmed'] and evidence['sensor']>evidence['threshold']
    assert evidence['sample_count']>=8 and evidence['close_visual_frames']>=3
    assert evidence['accepted_visual'] and evidence['camera_fresh']
    assert r['safety']['contact_steps']==0 and max(r['safety']['max_tilt_deg'].values())<10
    assert r['held_stationary'] and r['hold_displacement_m']<.02,r
    a=json.loads((tmp_path/'fire_telemetry_A.json').read_text())
    assert a['state']=='DONE' and a['motion_command']==[0,0]
    assert a['sensors']['lidar']['valid_count']>100
    assert a['camera_t']>evidence['t'] and any(d['cls']=='fire' for d in a['dets'])
    assert (tmp_path/'confirmed_camera.jpg').stat().st_size>1000
