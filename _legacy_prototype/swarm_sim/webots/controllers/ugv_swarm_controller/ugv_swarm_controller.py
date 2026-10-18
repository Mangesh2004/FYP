"""Webots Supervisor & 2-UGV Decentralized Swarm Controller (`ugv_swarm_controller.py`).

Configured via `runtime.ini` to run strictly inside `C:/Users/mange/anaconda3/envs/pytorch/python.exe`.
Synchronizes the 3D Webots scene (`DEF UGV_1` and `DEF UGV_2`) with the PyTorch Torchvision
`mobilenet_v3_small` detector, Shapely/NetworkX A* navigation, and real UDP P2P CBBA bundle queue.
"""
from __future__ import annotations

import math
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from swarm_sim.server import SwarmSimulationSession


def run_webots_or_standalone() -> None:
    session = SwarmSimulationSession()
    try:
        from controller import Supervisor  # type: ignore

        sup = Supervisor()
        timestep = int(sup.getBasicTimeStep())
        ugv1_node = sup.getFromDef("UGV_1")
        ugv2_node = sup.getFromDef("UGV_2")

        print("Webots 3D Supervisor connected: stepping 2-UGV Industrial Swarm in real time...")
        while sup.step(timestep) != -1:
            session.step_once(dt=timestep / 1000.0)
            for def_node, rid in ((ugv1_node, "UGV-1"), (ugv2_node, "UGV-2")):
                if def_node is not None and rid in session.world.robots:
                    r = session.world.robots[rid]
                    trans_field = def_node.getField("translation")
                    rot_field = def_node.getField("rotation")
                    if trans_field is not None:
                        trans_field.setSFVec3f([float(r.x), float(r.y), float(r.z)])
                    if rot_field is not None:
                        rot_field.setSFRotation([0.0, 0.0, 1.0, float(r.yaw)])
    except ImportError:
        print("Running standalone headless verification of `ugv_swarm_controller` (200 steps)...")
        for _ in range(200):
            session.step_once(dt=0.05)
        snap = session.snapshot()
        print(
            f"Standalone run verified {snap['completed_rooms']}/{snap['total_rooms']} buildings | "
            f"UGV queues: {[ (r['id'], r['bundle_queue']) for r in snap['robots'] ]}"
        )
        session.mesh.close()


if __name__ == "__main__":
    run_webots_or_standalone()
