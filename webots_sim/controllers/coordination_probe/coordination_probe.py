"""Two physical robots, production navigation/radio, explicit fixture goals.

Supervisor access is confined to measurements and ending the test. No peer
truth feeds agent navigation or doorway decisions.
"""
import json
import math
import os
from pathlib import Path
import sys
import traceback

import numpy as np
from controller import Supervisor

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from controllers.agent import agent as controller
from swarm.blueprint import Blueprint
from swarm.mapping import OccupancyGrid
from swarm.planning import GlobalPlanner


def main():
    # Substitute only the Webots construction boundary to expose physical
    # measurements in this fixture. All control methods remain the real Agent.
    controller.Robot = Supervisor
    a = controller.Agent()
    cfg = json.loads(Path(os.environ['FYP_PAIR_CONFIG']).read_text())
    sv, ts = a.robot, a.ts
    np.random.seed(cfg['seed'] + ord(a.name))
    a.bp = Blueprint(Path(cfg['blueprint']))
    a.grid = OccupancyGrid(a.bp.bounds)
    a.planner = GlobalPlanner(a.grid)
    if hasattr(a, 'traffic'):
        from swarm.traffic import DoorwayTraffic
        a.traffic = DoorwayTraffic(a.name, a.bp.doors, ['A', 'B'])
    goals = list(cfg['goals'][a.name])
    a.state, a.task = 'INSPECT', 'fixture_route'
    sv.step(ts)
    a.init_pose()
    ranges, step = a.scan(), 0
    done_file = Path(cfg['out']) / f'done_{a.name}.json'
    is_observer = a.name == 'A'
    nodes = {n: sv.getFromDef(f'AGENT_{n}') for n in ['A', 'B']} if is_observer else {}
    trace = (Path(cfg['out']) / 'physical.jsonl').open('w') if is_observer else None
    max_tilt, contacts, min_sep, overlap_steps = 0.0, 0, 100.0, 0
    while sv.step(ts) != -1:
        step += 1
        a.update_pose()
        a.handle_radio()
        if step % 4 == 0:
            ranges = a.scan()
            a.service_traffic()
            if step % 8 == 0:
                a.grid.integrate_scan(*a.pose, ranges, a.angles, a.lidar_max)
        if step % 31 == 0:
            a.update_links()
            a.send_heartbeat()
        if sv.getTime() > 2 and goals:
            if a.goal is None:
                a.set_goal(goals[0])
            result = a.nav_step(ranges)
            if result == 'arrived':
                goals.pop(0)
                a.goal = None
        elif not goals:
            a.state = 'DONE'
            a.drive(0, 0)
            done_file.write_text(json.dumps({'t': sv.getTime(), 'pose': a.pose, 'remaining': 0}))
        else:
            a.drive(0, 0)
        if is_observer:
            positions = {n: node.getPosition() for n, node in nodes.items()}
            sep = math.dist(positions['A'][:2], positions['B'][:2])
            min_sep = min(min_sep, sep)
            in_door = {n: abs(p[0]) < 0.55 and abs(p[1]) < 0.75 for n, p in positions.items()}
            overlap_steps += all(in_door.values())
            hits = {n: [list(c.point) for c in node.getContactPoints(True) if c.point[2] > .035]
                    for n, node in nodes.items()}
            contacts += any(hits.values())
            tilt = {n: math.degrees(math.acos(max(-1, min(1, node.getOrientation()[8]))))
                    for n, node in nodes.items()}
            max_tilt = max(max_tilt, *tilt.values())
            trace.write(json.dumps({'t': sv.getTime(), 'positions': positions, 'contacts': hits,
                                    'tilt': tilt, 'door_occupancy': in_door}) + '\n')
            done = all((Path(cfg['out']) / f'done_{n}.json').exists() for n in ['A', 'B'])
            if done or sv.getTime() >= cfg['duration']:
                trace.close()
                result = dict(completed=done, contact_steps=contacts, max_tilt_deg=max_tilt,
                              minimum_separation=min_sep, doorway_overlap_steps=overlap_steps,
                              sim_time=sv.getTime(), case=cfg['case'], loss=cfg['loss'])
                (Path(cfg['out']) / 'result.json').write_text(json.dumps(result, indent=2))
                sv.simulationQuit(0)
                break


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
