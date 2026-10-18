"""One robot searches one assigned room, verifies a visible fire, then holds.

python tools/run_fire_demo.py --gui --out runs/fire_live
python -m dashboard.server --run runs/fire_live --port 8770
Open http://127.0.0.1:8770/fire
"""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import build_world as bw
from run_webots import run


def make_demo_world(out,batch=False,max_time=180):
    bw.FURNITURE_RECTS.clear()
    world=''.join(bw.header(bw.furniture()))
    world+=bw.fire_node(0).replace(bw.vec(30,20,bw.HIDE_Z),bw.vec(-2.25,3.35,0)).replace('on FALSE','on TRUE')
    cfg=dict(name='A',run_dir=str(Path(out).resolve()),seed=1,noise=1.0,loss=0.0,
             method='cbba',peer_ids=['A'],search_rooms=['R1'],telemetry=True,perception_trace=True)
    robot=bw.agent('AGENT_A','fire_search_robot',[-6.3,.45],'0.95 0.45 0.1','thermal')
    robot=robot.replace('controller "agent"','controller "fire_demo_agent"\n  customData '+json.dumps(json.dumps(cfg)))
    world+=robot
    observer=dict(out=cfg['run_dir'],batch=batch,max_time=max_time)
    world+='Robot { supervisor TRUE controller "fire_demo_supervisor" customData '+json.dumps(json.dumps(observer))+' }\n'
    return world,cfg


def prepare_output(path):
    out=Path(path).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('Choose a fresh --out directory; prior telemetry must not be reused for a live demonstration.')
    out.mkdir(parents=True,exist_ok=True)
    return out


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--gui',action='store_true')
    ap.add_argument('--port',type=int,default=1710)
    ap.add_argument('--max-time',type=float,default=180)
    args=ap.parse_args()
    try:
        args.out=prepare_output(args.out)
    except ValueError as exc:
        ap.error(str(exc))
    world,cfg=make_demo_world(args.out,batch=not args.gui,max_time=args.max_time)
    (args.out/'agent_config.json').write_text(json.dumps(cfg,indent=2))
    source=ROOT/'worlds'/f'_fire_demo_{args.port}.wbt'
    source.write_text(world)
    try:
        code=run(source,timeout=1800 if args.gui else 300,port=args.port,
                 log=args.out/'console.txt',gui=args.gui,fast=not args.gui,extra_env={})
    finally:
        source.unlink(missing_ok=True)
    if code:
        raise SystemExit(code)


if __name__=='__main__':
    main()
