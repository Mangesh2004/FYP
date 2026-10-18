"""Disposable physical two-robot bottleneck tests using production control methods."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from check_stability import box, make_world
import build_world as bw
from run_webots import run


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--case', choices=['opposed', 'same_direction', 'staging_obstacle'], default='opposed')
    ap.add_argument('--loss', type=float, default=0)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--port', type=int, default=1450)
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    blueprint = out/'blueprint.json'
    blueprint.write_text(json.dumps(dict(map_bounds=[-4,-3,4,3], rooms={},
        doors={'D': dict(pos=[0,0], width=1.2, wall='v', between=['L','R'])},
        corridor=[-4,-3,4,3], entrance=dict(pos=[-4,0]))))
    cfg = dict(out=str(out), case=args.case, seed=args.seed, loss=args.loss, duration=90,
               blueprint=str(blueprint), goals={'A': [[2.4,0]], 'B': [[-2.4,0]]})
    starts, yaws = {'A': [-1.8,0], 'B': [1.8,0]}, {'A': 0, 'B': math.pi}
    if args.case == 'same_direction':
        starts['B'], yaws['B'], cfg['goals']['B'] = [-3,0], 0, [[2.4,1.4]]
    config = out/'config.json'
    config.write_text(json.dumps(cfg))
    empty,_ = make_world('primitives')
    world = empty[:empty.index('DEF AGENT_A')]
    world += box('door_upper', 0, 2.1, .2, 3.0) + box('door_lower', 0, -2.1, .2, 3.0)
    if args.case=='staging_obstacle':
        world += box('obstructed_staging',1.35,1.2,.55,.55)
    for name in ['A','B']:
        d = out/name
        d.mkdir(exist_ok=True)
        custom = dict(name=name, run_dir=str(d), noise=1.0, seed=args.seed, method='cbba', loss=args.loss)
        robot = bw.agent(f'AGENT_{name}', name, starts[name], '.2 .6 .8', 'thermal')
        robot = robot.replace('controller "agent"', 'controller "coordination_probe"').replace('supervisor FALSE', 'supervisor TRUE')
        robot = robot.replace(f'  name "{name}"', f'  rotation 0 0 1 {yaws[name]}\n  customData {json.dumps(json.dumps(custom))}\n  name "{name}"')
        world += robot
    source = ROOT/'worlds'/f'_coordination_{args.port}.wbt'
    canonical = ROOT/'worlds/facility.wbt'
    original_hash = hashlib.sha256(canonical.read_bytes()).hexdigest()
    source.write_text(world)
    try:
        code = run(source, timeout=160, port=args.port, log=out/'console.txt', gui=False,
                   extra_env={'FYP_PAIR_CONFIG': str(config)})
    finally:
        source.unlink(missing_ok=True)
    assert hashlib.sha256(canonical.read_bytes()).hexdigest() == original_hash
    if code or not (out/'result.json').exists():
        print((out/'console.txt').read_text(errors='replace')[-5000:])
        raise SystemExit(f'probe failed: {code}')
    print((out/'result.json').read_text())


if __name__ == '__main__':
    main()
