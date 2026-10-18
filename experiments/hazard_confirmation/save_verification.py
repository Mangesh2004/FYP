"""Assemble measured results only after the full suite succeeds."""
import datetime
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT=Path('D:/FYP')
SIM=ROOT/'webots_sim'
OUT=ROOT/'experiments/hazard_confirmation'
RUNS=OUT/'accepted/runs'

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

suite=next(ET.parse(OUT/'full_suite_accepted.xml').getroot().iter('testsuite')).attrib
assert int(suite['tests'])==139,suite
assert int(suite['failures'])==int(suite['errors'])==int(suite['skipped'])==0,suite
manifest=read(OUT/'accepted/source_manifest.json')
for name,expected in manifest['sha256'].items():
    with open(name,'rb') as f:
        assert hashlib.file_digest(f,'sha256').hexdigest()==expected,name
baseline=read(ROOT/'experiments/two_robot/verification.json')['source_sha256']
unchanged={name:digest for name,digest in manifest['sha256'].items()
           if name.endswith(('motion.py','facility.wbt','detector_frcnn_mnv3.pt','task_policy.pt'))}
assert len(unchanged)==4
for name,digest in unchanged.items():
    assert baseline[name]==digest,name

episodes=[]
for p in sorted(RUNS.glob('*/metrics.json')):
    m=read(p)
    truth=read(p.parent/'truth.json')
    info=read(p.parent/'batch_info.json')
    assert info['exit_code']==0 and not list(p.parent.glob('crash_*.txt')),p.parent
    events=[]
    for f in p.parent.glob('agent_*.jsonl'):
        events.extend(json.loads(line) for line in f.read_text(encoding='utf-8').splitlines())
    errors=[]
    evidence=[]
    for e in events:
        if e.get('ev')=='finding' and e.get('confirmed') and e['kind'] in ('fire','gas'):
            attempts=[a for a in events if a.get('ev')=='verification_attempt'
                      and a.get('agent')==e['agent'] and abs(a['t']-e['t'])<.01 and a['confirmed']]
            assert len(attempts)==1,(p,e)
            a=attempts[0]
            assert a['accepted_visual'] and a['camera_fresh'] and a['close_visual_frames']>=3,a
            assert a['sample_count']>=8 and a['sensor']>a['threshold'],a
            evidence.append(a)
            targets=[h['pos'] for h in truth['hazards'] if h['type']==e['kind']]
            errors.append(min((math.dist(e['pos'],q) for q in targets),default=float('inf')))
    episodes.append(dict(directory=str(p.parent),metrics=m,batch=info,
        hazard_report_errors_m=errors,confirmed_hazard_evidence=evidence,
        inconclusive_attempts=sum(e['ev']=='verification_inconclusive' for e in events),
        attempts=sum(e['ev']=='verification_attempt' for e in events),
        rooms_with_hazards=sorted({h['room'] for h in truth['hazards']})))

cbba=[e for e in episodes if e['metrics']['method']=='cbba']
assert len(episodes)==15 and len(cbba)==12,(len(episodes),len(cbba))
for e in cbba:
    m=e['metrics']
    assert not m['timed_out'] and m['rooms_inspected']==m['rooms_total']==4,m
    assert m['mission_complete_time'] is not None,m
    assert m['hazards_confirmed']==m['hazards_total'] and m['false_confirmations']==0,m
    assert m['safety']['contact_steps']==m['safety']['doorway_overlap_steps']==0,m
    assert max(m['safety']['max_tilt_deg'].values())<10,m
    assert all(error<=1.6 for error in e['hazard_report_errors_m']),e

physical=[]
for p in (SIM/'runs/hazard_accepted_pytest').rglob('result.json'):
    r=read(p)
    if not {'completed','contact_steps','max_tilt_deg'}<=r.keys():
        continue
    assert r['completed'] and r['contact_steps']==0 and r['max_tilt_deg']<10,r
    assert r.get('doorway_overlap_steps',0)==0,r
    physical.append(dict(result_file=str(p),**r))
assert len(physical)==19,len(physical)

matrix=[e for e in cbba if e['metrics']['condition'] in ('normal','loss20','door_blocked')]
assert len(matrix)==10
errors=[x for e in matrix for x in e['hazard_report_errors_m']]
summary=dict(pytest_passed=139,local_tests=100,physical_tests=19,mission_fault_checks=10,hazard_matrix_checks=10,
    full_mission_episodes=len(episodes),cbba_missions=len(cbba),randomized_layouts=8,
    hazard_matrix_runs=len(matrix),matrix_hazards=sum(e['metrics']['hazards_total'] for e in matrix),
    matrix_hazards_confirmed=sum(e['metrics']['hazards_confirmed'] for e in matrix),
    matrix_false_confirmations=sum(e['metrics']['false_confirmations'] for e in matrix),
    cbba_hazards=sum(e['metrics']['hazards_total'] for e in cbba),
    cbba_hazards_confirmed=sum(e['metrics']['hazards_confirmed'] for e in cbba),
    cbba_false_confirmations=sum(e['metrics']['false_confirmations'] for e in cbba),
    cbba_contact_steps=sum(e['metrics']['safety']['contact_steps'] for e in cbba),
    cbba_doorway_overlap_steps=sum(e['metrics']['safety']['doorway_overlap_steps'] for e in cbba),
    hazard_report_error_mean_m=sum(errors)/len(errors),hazard_report_error_max_m=max(errors),
    cbba_max_tilt_deg=max(max(e['metrics']['safety']['max_tilt_deg'].values()) for e in cbba))
report=dict(verified_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    summary=summary,pytest=suite,
    command='FYP_HAZARD_DIR=FYP_FAULT_DIR=experiments/hazard_confirmation/accepted; python -m pytest -q --basetemp=runs/hazard_accepted_pytest --junitxml=experiments/hazard_confirmation/full_suite_accepted.xml',
    investigation=read(OUT/'investigation.json'),source_manifest=manifest,unchanged_from_previous_milestone=unchanged,
    episodes=episodes,physical_cases=physical,
    review='Independent read-only review checked frame quality, evidence freshness, bounded retries, person reporting and safe abandoned-reservation release.',
    limits=[
        'Finite simulation verification on the same building floor plan with randomized hazards, spawns and distractors; not universal performance or real-hardware calibration.',
        'Evaluator match radius remains 1.6m. Individual report errors are included above; camera box-size localization remains approximate.',
        'Inconclusive suspected tasks are explicitly logged and closed for this mission; later observations do not automatically reopen them.',
        'Gas transport is a plume approximation and thermal sensing uses Webots LightSensor, so these tests do not establish physical sensor accuracy.',
        'Full episodes end after logical mission completion, before both rally returns are necessarily finished.',
        'Seeded geometry and radio loss do not make every native/noisy sensor trajectory deterministic.',
        'The hazard layout matrix permits 600 simulation seconds for multi-view verification and room inspection; the original 300s candidate failures are preserved.',
        'Central commander-loss and static robot-loss are deliberate incomplete negative controls.'])
(OUT/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(summary,indent=2))
print('Verified source hashes:',len(manifest['sha256']))
print(OUT/'verification.json')
