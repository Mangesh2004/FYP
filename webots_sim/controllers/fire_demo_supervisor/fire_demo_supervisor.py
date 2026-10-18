"""Observer-only scoring. No hazard data is sent to the agent."""
import base64
import json
import math
from pathlib import Path
import sys

from controller import Supervisor

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from truth.motion_audit import MotionAudit


def main():
    sv=Supervisor()
    cfg=json.loads(sv.getCustomData())
    out=Path(cfg['out'])
    robot=sv.getFromDef('AGENT_A')
    hazard=sv.getFromDef('FIRE_0')
    audit=MotionAudit(json.loads((ROOT/'config/blueprint.json').read_text())['doors'])
    saved=False
    hold_start=None
    hold_position=None
    while sv.step(int(sv.getBasicTimeStep()))!=-1:
        audit.sample({'A':robot})
        if saved:
            continue
        p=out/'fire_telemetry_A.json'
        try:
            a=json.loads(p.read_text())
        except (OSError,ValueError):
            continue
        findings=[f for f in a['findings'] if f['kind']=='fire' and f.get('confirmed')]
        if findings:
            if hold_start is None:
                hold_start=sv.getTime()
                hold_position=robot.getPosition()[:2]
            if sv.getTime()-hold_start<2:
                continue
        if findings or sv.getTime()>cfg['max_time']:
            error=min((math.dist(f['pos'],hazard.getPosition()[:2]) for f in findings),default=None)
            displacement=math.dist(robot.getPosition()[:2],hold_position) if hold_position else None
            safety=audit.summary()
            if not math.isfinite(safety['minimum_separation']):
                safety['minimum_separation']=None  # no second robot in this demonstration
            result=dict(confirmed=bool(findings),t=sv.getTime(),error_m=error,
                        safety=safety,verification=a.get('verification'),findings=findings,
                        hold_displacement_m=displacement,
                        held_stationary=bool(findings and displacement<.02 and a['state']=='DONE' and a['motion_command']==[0,0]))
            (out/'demo_result.json').write_text(json.dumps(result,indent=2))
            if a.get('camera_jpg'):
                (out/'confirmed_camera.jpg').write_bytes(base64.b64decode(a['camera_jpg']))
            saved=True
            if cfg['batch']:
                sv.simulationQuit(0)
                break


if __name__=='__main__':
    main()
