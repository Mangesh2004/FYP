"""One-room public search assignment using the unchanged production agent.

The demo adds device telemetry and holds after a genuine fire confirmation.
It never receives a hazard position or uses simulator labels.
"""
import json
import math
from pathlib import Path
import sys

import numpy as np

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from controllers.agent.agent import Agent,THERMAL_CONFIRM
from swarm.io_util import atomic_write_text
from swarm.live_sensors import lidar_snapshot


class FireDemoAgent(Agent):
    def __init__(self):
        self.camera_t=None
        self.last_verification=None
        super().__init__()
        rooms=set(self.cfg['search_rooms'])
        self.book.tasks={tid:t for tid,t in self.book.tasks.items() if t.get('room') in rooms}
        self.log('search_assignment',rooms=sorted(rooms),objective='Find and verify fire, then hold')

    def fire_confirmed(self):
        return any(f['kind']=='fire' and f.get('confirmed') for f in self.book.findings.values())

    def decide(self):
        if self.fire_confirmed():
            self.complete=True
            self.state='DONE'
            self.drive(0,0)
            return
        super().decide()

    def finish_task(self,result):
        super().finish_task(result)
        if self.fire_confirmed():
            self.complete=True
            self.state='DONE'
            self.drive(0,0)
            self.log('demo_hold',reason='Fire confirmed; stopped for inspection')

    def log(self,ev,**kw):
        if ev=='verification_attempt':
            self.last_verification=dict(t=self.now(),**kw)
        super().log(ev,**kw)

    def perceive(self,ranges):
        previous=self.last_rgb
        super().perceive(ranges)
        if self.last_rgb is not previous:
            self.camera_t=self.now()
        self.write_telemetry(True)

    def write_status(self):
        super().write_status()
        # Keep real camera inference live when the demonstration has stopped.
        if self.state=='DONE':
            self.perceive(self.motion_ranges)
        else:
            self.write_telemetry(True)

    def write_telemetry(self,with_images):
        super().write_telemetry(with_images)
        p=self.run_dir/f'telemetry_{self.name}.json'
        data=json.loads(p.read_text())
        now=self.now()
        data.update(camera_t=self.camera_t,detector='Faster R-CNN MobileNetV3 · score ≥ 0.55')
        data['sensors']=dict(
            lidar=lidar_snapshot(self.lidar.getRangeImage(),self.angles,self.lidar_max,self.motion_scan_t,now),
            thermal_raw={s.getName():s.getValue() for s,_ in self.thermal},
            proximity_m=[s.getValue() for s in self.proximity],
            gps_xyz=list(self.gps.getValues()),imu_rpy=list(self.imu.getRollPitchYaw()),
            wheel_rad=[self.le.getValue(),self.re.getValue()])
        samples=[float(s) for s in self.job.get('samples',[]) if math.isfinite(s)]
        data['verification']=dict(phase=self.job.get('phase'),sample_count=len(samples),
            median=float(np.median(samples)) if samples else None,threshold=THERMAL_CONFIRM,
            close_visual_frames=len(self.job.get('quality_positions',[])),
            camera_fresh=now-self.job.get('quality_seen_t',-1e9)<1,
            view=self.job.get('view_index',0)+1,last_attempt=self.last_verification,
            required_samples=8,required_frames=3,required_camera_age_s=1)
        # Publish the expanded snapshot once, so observers cannot read the
        # intermediate base telemetry without its matching sensor evidence.
        atomic_write_text(self.run_dir/f'fire_telemetry_{self.name}.json',json.dumps(data,allow_nan=False))


if __name__=='__main__':
    agent=None
    try:
        agent=FireDemoAgent()
        agent.run()
    except Exception:
        import traceback
        where=agent.run_dir if agent else ROOT/'runs'
        (where/'crash_fire_demo.txt').write_text(traceback.format_exc())
        raise
