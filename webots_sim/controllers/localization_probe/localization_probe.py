"""Observer-only calibration fixture; no control decisions consume scene truth."""
import json
import math
import os
from pathlib import Path
import sys

import numpy as np
from controller import Supervisor

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from truth.episode import make_episode
from swarm.perception import Detector,bgra_to_rgb
from swarm.fusion import localize,lidar_range_in_box

sv=Supervisor()
ts=int(sv.getBasicTimeStep())
out=Path(os.environ['FYP_LOCALIZATION_OUT'])
out.mkdir(exist_ok=True,parents=True)
cam,lidar=sv.getDevice('camera'),sv.getDevice('lidar')
cam.enable(ts);lidar.enable(ts)
ep=make_episode(5,'normal')
def place(node,pos,yaw=0,z=0):
    node.getField('translation').setSFVec3f([*pos,z])
    node.getField('rotation').setSFRotation([0,0,1,yaw])
    node.resetPhysics()
for h in ep['hazards']:
    place(sv.getFromDef(h['id']),h['pos'],h['yaw'])
    sv.getFromDef(h['id']+'_LIGHT').getField('on').setSFBool(True)
for d in ep['distractors']:
    place(sv.getFromDef(d['id']),d['pos'],d['yaw'],.44 if d['id']=='DISTRACT_3' else 0)
place(sv.getFromDef('AGENT_B'),[-10,5],0,.0975)
detector=Detector(score_thresh=.55)
angles=math.pi-(np.arange(360)+.5)*2*math.pi/360
results=[]
for i,pose in enumerate([[.9507,-2.5752,.0913],[3.4863,-3,.0446],[-2.5958,-1.95,-.5811],[4.8,-2.7,.03]]):
    place(sv.getSelf(),pose[:2],pose[2],.0975)
    for _ in range(8):sv.step(ts)
    rgb=bgra_to_rgb(cam.getImage(),320,240)
    cam.saveImage(str(out/f'view_{i}.jpg'),95)
    scan=np.asarray(lidar.getRangeImage())
    detections=detector.detect(rgb)
    rows=[]
    for d in detections:
        rows.append(dict(detection=d,localized=localize(d,pose,scan,angles,320,1.2,8,240),
          lidar=lidar_range_in_box(scan,angles,d['box'],320,1.2,8),
          size_range=(160/math.tan(.6))/max(4,d['box'][3]-d['box'][1])))
    results.append(dict(pose=pose,rows=rows,ranges=scan.tolist(),image=str(out/f'view_{i}.jpg')))
(out/'results.json').write_text(json.dumps(dict(episode=ep,views=results),indent=2))
sv.simulationQuit(0)
