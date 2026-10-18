"""Fresh fault episodes, then the complete suite against the bound mission matrix.

Run after the eight normal episodes have completed in accepted/.
"""
import os
from pathlib import Path
import subprocess
import sys

SIM=Path('D:/FYP/webots_sim')
OUT=Path(__file__).resolve().parent/'accepted'
sys.path.insert(0,str(SIM))
from tests.test_faults_webots import door_used_after

for seed in [0,1,3,5,8,11,1000,1001]:
    assert (OUT/f'runs/s{seed:03d}_normal_cbba/metrics.json').exists(),seed

def batch(conditions,method='cbba',extra=()):
    subprocess.run([sys.executable,str(SIM/'tools/run_batch.py'),'--out',str(OUT),
        '--seeds','3','--conditions',*conditions,'--methods',method,'--workers','2',
        '--max-time','300','--port-base','1640','--env','FYP_PERCEPTION_TRACE=1',*extra],
        cwd=SIM,check=True)

batch(['commander_cut','robot_killed','loss20'])
door=door_used_after(OUT/'runs/s003_normal_cbba',20)
batch(['door_blocked'],extra=[f'FYP_FAULT_DOOR={door}'])
batch(['normal','commander_cut'],'central')
batch(['robot_killed'],'static')
env={**os.environ,'FYP_HAZARD_DIR':str(OUT),'FYP_FAULT_DIR':str(OUT)}
subprocess.run([sys.executable,'-m','pytest','-q','--basetemp=runs/hazard_accepted_pytest',
    '--junitxml='+str(OUT.parent/'full_suite_accepted.xml')],cwd=SIM,env=env,check=True)
