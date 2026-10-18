"""FastAPI + WebSocket Orchestrator for the 6-Building Industrial 2-UGV Swarm Simulator.

Run using the `pytorch` Conda environment:
    C:\\Users\\mange\\anaconda3\\envs\\pytorch\\python.exe -m swarm_sim.server
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

from .navigation_controller import compute_lidar_dwa_control
from .p2p_mesh import DecentralizedSwarmMesh
from .physics_world import PhysicsArenaWorld
from .vision_ai import SwarmAIEngine, render_annotated_hud_jpeg


STATIC_DIR = Path(__file__).resolve().parent / "static"


class SwarmSimulationSession:
    """Coordinates the 36m x 28m 6-Building PhysicsArenaWorld, Torchvision AI, and UDP P2P Mesh."""

    def __init__(self) -> None:
        self.ai_engine = SwarmAIEngine()
        self.vehicle_mode: Literal["rover", "drone"] = "rover"
        self.swarm_mode: Literal["2_ugv_industrial", "4_agents", "2_cars_classroom"] = "2_ugv_industrial"
        self.running: bool = True
        self.speed_multiplier: int = 2
        self.world = PhysicsArenaWorld(
            vehicle_mode=self.vehicle_mode,
            swarm_mode=self.swarm_mode,
        )
        self.mesh = DecentralizedSwarmMesh(self.world, self.ai_engine)
        # Perform 3 initial UDP P2P handshake cycles so CBBA bundle queues converge at t=0
        for _ in range(3):
            self.mesh.step_swarm_autonomy()

    def reset(
        self,
        *,
        vehicle_mode: Literal["rover", "drone"] | None = None,
        swarm_mode: Literal["2_ugv_industrial", "4_agents", "2_cars_classroom"] | None = None,
    ) -> None:
        if vehicle_mode is not None:
            self.vehicle_mode = vehicle_mode
        if swarm_mode is not None:
            self.swarm_mode = swarm_mode
        self.mesh.close()
        self.world = PhysicsArenaWorld(
            vehicle_mode=self.vehicle_mode,
            swarm_mode=self.swarm_mode,
        )
        self.mesh = DecentralizedSwarmMesh(self.world, self.ai_engine)
        for _ in range(3):
            self.mesh.step_swarm_autonomy()

    def step_once(self, dt: float = 0.05) -> None:
        self.mesh.step_swarm_autonomy()
        commands: dict[str, tuple[float, float]] = {}
        for agent_id, robot in self.world.robots.items():
            commands[agent_id] = compute_lidar_dwa_control(self.world, robot)
        self.world.step_physics(commands, dt=dt)

    def kill_agent(self, agent_id: str) -> None:
        if agent_id in self.world.robots:
            robot = self.world.robots[agent_id]
            robot.battery = 0.0
            robot.operational = False
            robot.status_phase = "POWER_FAILURE_OFFLINE"
            self.mesh._log_event(
                "HARDWARE_FAILURE",
                f"Injected power failure on {robot.name} (UDP P2P heartbeat stopped)!",
                agent_id=agent_id,
            )

    def revive_agent(self, agent_id: str) -> None:
        if agent_id in self.world.robots:
            robot = self.world.robots[agent_id]
            robot.battery = 0.90
            robot.operational = True
            robot.comms_online = True
            robot.status_phase = "REBOOTED_ONLINE"
            self.mesh.brains[agent_id].peer_last_heartbeat[agent_id] = self.world.sim_time
            self.mesh._log_event(
                "AGENT_RECOVERED",
                f"Restored battery & UDP P2P radio on {robot.name}",
                agent_id=agent_id,
            )

    def toggle_commander_link(self) -> bool:
        self.mesh.commander_link_active = not self.mesh.commander_link_active
        state_str = (
            "RESTORED (Command Station Telemetry Active)"
            if self.mesh.commander_link_active
            else "SEVERED (COMMS-DENIED MODE: 2 UGVs operating on pure UDP P2P autonomy!)"
        )
        self.mesh._log_event("COMMANDER_LINK", f"Commander Link {state_str}", agent_id="PILOT")
        return self.mesh.commander_link_active

    def trigger_emergency_fire(self, room_id: str = "zone-6-warehouse") -> None:
        target_id = room_id if room_id in self.world.rooms else "zone-6-warehouse"
        room = self.world.rooms[target_id]
        room.true_hazard_class = "fire_hazard"
        room.required_sensor = "thermal"
        room.base_hierarchy_tier = 1
        room.scan_progress = 0.0
        room.completed_by = None
        room.completed_at_time = None
        for brain in self.mesh.brains.values():
            brain.room_completed[target_id] = None
            brain.winning_agents[target_id] = None
            brain.winning_bids[target_id] = 0.0
        self.mesh._log_event(
            "EMERGENCY_ESCALATION",
            f"Tier-1 Fire Emergency erupted in {room.name}! Re-queued for Thermal Specialist verification.",
            agent_id="ENV",
        )

    def snapshot(self) -> dict[str, Any]:
        robots_data = []
        for aid, r in self.world.robots.items():
            brain = self.mesh.brains[aid]
            decisions_dict = {
                rid: {
                    "bid": d.bid_utility,
                    "pass_prob": d.pass_probability,
                    "should_pass": d.should_pass,
                    "tier": d.hierarchy_tier,
                    "mode": d.specialty_mode,
                    "reasoning": d.reasoning,
                }
                for rid, d in brain.latest_decisions.items()
            }
            robots_data.append(
                {
                    "id": aid,
                    "name": r.name,
                    "specialty": r.specialty_title,
                    "color": r.color_hex,
                    "vehicle_mode": r.vehicle_mode,
                    "primary_sensors": sorted(r.primary_sensors),
                    "backup_sensors": sorted(r.backup_sensors),
                    "operations": sorted(r.operations),
                    "x": round(r.x, 3),
                    "y": round(r.y, 3),
                    "z": round(r.z, 3),
                    "yaw": round(r.yaw, 3),
                    "pitch": round(r.pitch, 3),
                    "roll": round(r.roll, 3),
                    "speed": round(float(np.hypot(r.vx, r.vy)), 3),
                    "battery": round(r.battery, 4),
                    "operational": r.operational,
                    "comms_online": r.comms_online,
                    "assigned_room": r.assigned_room_id,
                    "bundle_queue": brain.bundle_queue,
                    "status_phase": r.status_phase,
                    "odometry_m": round(r.odometry_distance, 2),
                    "lidar_hits": r.lidar_hit_points[::2],
                    "path": r.current_path,
                    "camera_jpeg": render_annotated_hud_jpeg(
                        r.last_camera_rgb,
                        r.last_vision_class,
                        r.last_vision_conf,
                        r.last_vision_bbox,
                    ),
                    "vision_class": r.last_vision_class,
                    "vision_conf": round(r.last_vision_conf, 3),
                    "vision_severity": round(r.last_vision_severity, 3),
                    "udp_port": brain.udp_port,
                    "packets_sent": brain.packets_sent,
                    "packets_received": brain.packets_received,
                    "evicted_peers": sorted(brain.evicted_peers),
                    "cbba_winners": brain.winning_agents,
                    "cbba_bids": {k: round(v, 3) for k, v in brain.winning_bids.items()},
                    "decisions": decisions_dict,
                }
            )

        rooms_data = [
            {
                "id": rm.room_id,
                "name": rm.name,
                "style": rm.building_style,
                "bounds": rm.bounds,
                "roof_height": rm.roof_height,
                "facade_color": rm.facade_color,
                "doorway": rm.doorway_point,
                "inspect_point": rm.inspect_point,
                "hazard_pos": rm.hazard_pos,
                "hazard_class": rm.true_hazard_class,
                "required_sensor": rm.required_sensor,
                "tier": rm.base_hierarchy_tier,
                "scan_progress": round(rm.scan_progress, 3),
                "completed_by": rm.completed_by,
                "completed_at": rm.completed_at_time,
                "detected_conf": round(rm.detected_confidence, 3),
            }
            for rm in self.world.rooms.values()
        ]

        walls_data = [
            {
                "x1": w.x1,
                "y1": w.y1,
                "x2": w.x2,
                "y2": w.y2,
                "height": w.height,
                "thickness": w.thickness,
                "tag": w.tag,
                "building_id": w.building_id,
            }
            for w in self.world.walls
        ]
        obstacles_data = [
            {
                "id": o.obs_id,
                "kind": o.kind,
                "x": o.x,
                "y": o.y,
                "radius": o.radius,
                "height": o.height,
            }
            for o in self.world.obstacles
        ]
        packets_data = [
            {
                "seq": p.seq_id,
                "time": p.sim_time,
                "from": p.sender_id,
                "to": p.receiver_id,
                "type": p.packet_type,
                "summary": p.summary,
                "bytes": p.bytes_size,
            }
            for p in self.mesh.packet_log[-18:]
        ]

        completed_count = sum(1 for rm in self.world.rooms.values() if rm.completed_by is not None)
        return {
            "arena_width": self.world.WIDTH,
            "arena_height": self.world.HEIGHT,
            "sim_time": round(self.world.sim_time, 2),
            "step_count": self.world.step_count,
            "running": self.running,
            "speed_multiplier": self.speed_multiplier,
            "vehicle_mode": self.vehicle_mode,
            "swarm_mode": self.swarm_mode,
            "commander_link_active": self.mesh.commander_link_active,
            "ai_metadata": self.ai_engine.training_metadata,
            "completed_rooms": completed_count,
            "total_rooms": len(self.world.rooms),
            "robots": robots_data,
            "rooms": rooms_data,
            "walls": walls_data,
            "obstacles": obstacles_data,
            "packets": packets_data,
            "events": self.mesh.event_feed[-24:],
        }


app = FastAPI(title="MUM-T 6-Building Industrial Complex — 2-UGV Swarm")
session = SwarmSimulationSession()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/state")
async def get_state() -> JSONResponse:
    return JSONResponse(session.snapshot())


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            try:
                msg_text = await asyncio.wait_for(ws.receive_text(), timeout=0.045)
                cmd = __import__("json").loads(msg_text)
                action = cmd.get("action")
                if action == "toggle_play":
                    session.running = not session.running
                elif action == "step":
                    session.running = False
                    session.step_once()
                elif action == "reset":
                    session.reset(
                        vehicle_mode=cmd.get("vehicle_mode"),
                        swarm_mode=cmd.get("swarm_mode"),
                    )
                elif action == "set_mode":
                    session.reset(
                        vehicle_mode=cmd.get("vehicle_mode", session.vehicle_mode),
                        swarm_mode=cmd.get("swarm_mode", session.swarm_mode),
                    )
                elif action == "set_speed":
                    session.speed_multiplier = int(np.clip(cmd.get("speed", 2), 1, 5))
                elif action == "kill_agent":
                    session.kill_agent(str(cmd.get("agent_id", "UGV-2")))
                elif action == "revive_agent":
                    session.revive_agent(str(cmd.get("agent_id", "UGV-2")))
                elif action == "toggle_commander_link":
                    session.toggle_commander_link()
                elif action == "emergency_fire":
                    session.trigger_emergency_fire(str(cmd.get("room_id", "zone-6-warehouse")))
            except asyncio.TimeoutError:
                pass

            if session.running:
                for _ in range(session.speed_multiplier):
                    session.step_once()

            await ws.send_json(session.snapshot())
    except WebSocketDisconnect:
        return


def main(host: str = "127.0.0.1", port: int = 8765) -> None:
    print(f"Launching White-Theme 6-Building Industrial 2-UGV Studio on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
