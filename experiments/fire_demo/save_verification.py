"""Record measured native demo outcomes and the executed regression results."""
import datetime
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT=Path('D:/FYP')
SIM=ROOT/'webots_sim'
OUT=Path(__file__).resolve().parent
def read(p):
    return json.loads(p.read_text())

suite=next(ET.parse(ROOT/'experiments/fire_demo_full_suite_final.xml').getroot().iter('testsuite')).attrib
assert int(suite['tests'])==146 and int(suite['failures'])==int(suite['errors'])==int(suite['skipped'])==0,suite
episodes=[]
for folder in [SIM/'runs/fire_review_final/test_robot_discovers_fire_conf0',SIM/'runs/fire_demo_live',SIM/'runs/fire_demo_watch']:
    r=read(folder/'demo_result.json')
    assert r['confirmed'] and r['error_m']<1.6 and r['held_stationary'],r
    assert r['safety']['contact_steps']==0 and r['hold_displacement_m']<.02,r
    a=r['verification']['last_attempt']
    assert a['confirmed'] and a['sensor']>a['threshold'] and a['sample_count']>=8
    assert a['accepted_visual'] and a['camera_fresh'] and a['close_visual_frames']>=3
    episodes.append(dict(directory=str(folder),**r))

names=['controllers/fire_demo_agent/fire_demo_agent.py','controllers/fire_demo_supervisor/fire_demo_supervisor.py',
       'tools/run_fire_demo.py','swarm/live_sensors.py','dashboard/server.py','dashboard/static/fire.html',
       'tests/test_live_sensors.py','tests/test_dashboard.py','tests/test_fire_demo_webots.py']
hashes={n:hashlib.file_digest(open(SIM/n,'rb'),'sha256').hexdigest() for n in names}
old=read(ROOT/'experiments/hazard_confirmation/accepted/source_manifest.json')['sha256']
unchanged={}
for n in ['controllers/agent/agent.py','worlds/facility.wbt','models/detector_frcnn_mnv3.pt','swarm/motion.py']:
    digest=hashlib.file_digest(open(SIM/n,'rb'),'sha256').hexdigest()
    assert old[str(SIM/n)]==digest,n
    unchanged[n]=digest
report=dict(verified_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    live_url='http://127.0.0.1:8770/fire',screenshot=str(SIM/'runs/fire_demo_watch/dashboard.png'),
    causes=['Previous visible run was a repeated navigation fixture with no detector or hazard mission.',
            'Generic one-layer lidar image is only one pixel high; the combined viewer plots actual range rays.',
            'Nonfinite lidar diagnostics could produce invalid browser JSON; ingested nonfinite tokens become null.'],
    scope='One thermal robot searches public room R1, discovers fire from camera inference, verifies with measured thermal samples, then holds.',
    pytest_full_suite=suite,
    additional_targeted_checks=dict(passed=41,includes='Fresh-directory replay guard plus sensor/dashboard/hazard/traffic checks',
        final_ui_checks_passed=11),
    source_sha256=hashes,unchanged_production_source_sha256=unchanged,
    previous_mission_matrix='Full suite explicitly reused accepted native mission episodes for the unchanged production agent, model and world; physics cases and the new fire demo ran fresh.',
    native_episodes=episodes,
    review='Independent read-only reviewer found a stale-output replay risk; nonempty output is now rejected and the guard regression passes. No remaining blocker on recheck.',
    limits=['Thermal readings are Webots light-sensor proxies, not calibrated temperature.',
            'Controlled one-room fire demonstration; this is not a general real-world fire detector validation.',
            'The default multi-robot mission logic and detector weights were not changed.',
            'The read-only dashboard stays live while Webots is running; it cannot move the robot.'])
(OUT/'verification.json').write_text(json.dumps(report,indent=2,allow_nan=False))
print(json.dumps(dict(full_suite_passed=int(suite['tests']),native_demos=len(episodes),
    confirmations=[e['verification']['last_attempt']['t'] for e in episodes],
    contacts=[e['safety']['contact_steps'] for e in episodes],
    held_stationary=[e['held_stationary'] for e in episodes]),indent=2))
