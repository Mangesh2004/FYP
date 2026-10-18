"""Generate disposable ONE-robot worlds and collect physical stability evidence.

python tools/check_stability.py --case primitives --out runs/stability/before
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_world as bw
from run_webots import run


def box(name, x, y, sx, sy, height=0.6):
    return (f'Solid {{ translation {x} {y} {height/2} name "{name}" children [ Shape {{ '
            f'appearance PBRAppearance {{ baseColor 0.7 0.3 0.15 roughness 1 }} '
            f'geometry Box {{ size {sx} {sy} {height} }} }} ] '
            f'boundingObject Box {{ size {sx} {sy} {height} }} }}\n')


def make_world(case, yaw=0.0):
    preamble = ('#VRML_SIM R2025a utf8\n'
                f'EXTERNPROTO "{bw.URL}/{bw.PROTOS["Pioneer3dx"]}"\n'
                'WorldInfo { basicTimeStep 32 contactProperties [ ContactProperties { coulombFriction [0.9] } ] }\n'
                'Viewpoint { orientation -0.357 0.862 0.357 1.718 position 3 -6 6 }\n'
                'DirectionalLight { direction 0.3 0.4 -1 intensity 1.5 }\n'
                'Solid { translation 0 0 -0.05 name "ground" children [ Shape { '
                'appearance PBRAppearance { baseColor 0.85 0.85 0.85 roughness 1 } '
                'geometry Box { size 24 16 0.1 } } ] boundingObject Box { size 24 16 0.1 } }\n')
    start, goals, mode, duration = [0, 0], [], "primitives", 18
    obstacles = ""
    if case == "wall":
        obstacles = box("wall", 1.2, 0, 0.2, 3)
        goals, mode, duration = [[2.5, 0]], "navigate", 65
    elif case == "low_obstacle":
        obstacles = box("low_crate", 1.2, 0, 0.6, 0.6, 0.22)
        goals, mode, duration = [[2.5, 0]], "navigate", 50
    elif case == "recover":
        obstacles = box("rear_wall", -0.4, 0, 0.1, 2)
        goals, mode, duration = [[2.5, 0]], "recover", 20
    elif case == "doorway":
        obstacles = box("door_left", 1.5, 1.5, 0.2, 1.8) + box("door_right", 1.5, -1.5, 0.2, 1.8)
        goals, mode, duration = [[3, 0], [0, 0]], "navigate", 65
    elif case == "close_turn":
        obstacles = box("side_wall", 0, -0.38, 4, 0.1)
        mode, duration = "spin", 15
    elif case == "facility":
        bw.FURNITURE_RECTS.clear()
        preamble = "".join(bw.header(bw.furniture()))
        start, goals, mode, duration = [-6.3, 0.45], [[-3.5, 2.6], [-3.5, -2.6], [-6.0, 0.5]], "navigate", 150
    robot = bw.agent("AGENT_A", "stability_robot", start, "0.95 0.45 0.1", "thermal")
    robot = robot.replace('controller "agent"', 'controller "stability_probe"').replace('supervisor FALSE', 'supervisor TRUE')
    robot = robot.replace('  name "stability_robot"', f'  rotation 0 0 1 {yaw}\n  name "stability_robot"')
    return preamble + obstacles + robot, {"mode": mode, "goals": goals, "duration": duration}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", choices=["primitives", "wall", "low_obstacle", "recover", "doorway", "facility", "close_turn"], required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--port", type=int, default=1420)
    ap.add_argument("--gui", action="store_true")
    ap.add_argument("--yaw", type=float, default=0.0, help="initial heading in radians")
    ap.add_argument("--noise", type=float, default=1.0, help="production extra sensor-noise multiplier")
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    world, cfg = make_world(args.case, args.yaw)
    cfg.update(case=args.case, out=str(out), gui=args.gui, yaw=args.yaw, noise=args.noise)
    config = out / "config.json"
    config.write_text(json.dumps(cfg))
    source = ROOT / "worlds" / f"_stability_{args.port}.wbt"
    canonical = ROOT / "worlds" / "facility.wbt"
    before = hashlib.sha256(canonical.read_bytes()).hexdigest()
    cfg["canonical_world_sha256"] = before
    config.write_text(json.dumps(cfg, indent=2))
    source.write_text(world)
    try:
        code = run(source, timeout=180, port=args.port, log=out / "console.txt", gui=args.gui,
                   extra_env={"FYP_STABILITY_CONFIG": str(config)})
    finally:
        source.unlink(missing_ok=True)
    assert hashlib.sha256(canonical.read_bytes()).hexdigest() == before, "canonical world changed"
    result = out / "result.json"
    if code or not result.exists():
        print((out / "console.txt").read_text(errors="replace")[-6000:])
        raise SystemExit(f"Probe failed: exit={code}, result exists={result.exists()}")
    print(result.read_text())


if __name__ == "__main__":
    main()
