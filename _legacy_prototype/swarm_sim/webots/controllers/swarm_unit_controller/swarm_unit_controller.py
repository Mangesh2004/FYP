"""Webots Controller Bridge for the 4-Room Decentralized Swarm.

Configured via `runtime.ini` to run inside `C:/Users/mange/anaconda3/envs/pytorch/python.exe`.
Can run either inside Webots (when `controller` module is present) or standalone.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from swarm_sim.server import SwarmSimulationSession


def run_controller() -> None:
    session = SwarmSimulationSession()
    try:
        from controller import Supervisor  # type: ignore

        sup = Supervisor()
        timestep = int(sup.getBasicTimeStep())
        while sup.step(timestep) != -1:
            session.step_once(dt=timestep / 1000.0)
    except ImportError:
        print("Webots `controller` library not detected in environment; running 100 standalone steps...")
        for _ in range(100):
            session.step_once(dt=0.05)
        snap = session.snapshot()
        print(f"Completed standalone verification: {snap['completed_rooms']}/{snap['total_rooms']} rooms verified.")


if __name__ == "__main__":
    run_controller()
