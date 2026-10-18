"""Real UDP Peer-to-Peer (P2P) Socket Mesh & Multi-Task CBBA Bundle Queue Engine.

Designed for 2 Specialized UGVs operating across a 6-Building Industrial & Office Complex:
1. Each UGV owns a real non-blocking OS UDP socket (`127.0.0.1:<port>`).
2. Each UGV computes a multi-task CBBA Bundle Queue (`bundle_queue`) ordered by Hierarchy Tier
   (Tier 1 Critical -> Tier 2 High -> Tier 3 Routine) and marginal travel distance.
3. Primary specialists outbid backup generalists on their specialty buildings, so UGV-1 and UGV-2
   partition the 6 buildings into conflict-free sequential queues and execute them one by one.
4. If one UGV loses power or comms, its UDP heartbeats stop; after `HEARTBEAT_TIMEOUT_SEC`, the
   surviving UGV invalidates the silent peer's claims and absorbs all remaining buildings into its
   own queue using its backup sensor suite.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import socket
from typing import Any

from .navigation_controller import shortest_navigation_distance
from .physics_world import PhysicsArenaWorld, RobotBody, RoomZone
from .vision_ai import SwarmAIEngine, TacticalDecisionOutput


@dataclass
class P2PPacketRecord:
    seq_id: int
    sim_time: float
    sender_id: str
    receiver_id: str
    packet_type: str
    summary: str
    bytes_size: int


@dataclass
class AgentLocalBrain:
    """Strictly decentralized per-UGV state, multi-task CBBA bundle queue & UDP socket."""

    agent_id: str
    udp_socket: socket.socket
    udp_port: int
    peer_ports: dict[str, tuple[str, int]] = field(default_factory=dict)
    winning_bids: dict[str, float] = field(default_factory=dict)
    winning_agents: dict[str, str | None] = field(default_factory=dict)
    room_completed: dict[str, str | None] = field(default_factory=dict)
    room_vision_evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    peer_last_heartbeat: dict[str, float] = field(default_factory=dict)
    peer_telemetry: dict[str, dict[str, Any]] = field(default_factory=dict)
    latest_decisions: dict[str, TacticalDecisionOutput] = field(default_factory=dict)
    bundle_queue: list[str] = field(default_factory=list)
    claimed_room_id: str | None = None
    packets_sent: int = 0
    packets_received: int = 0
    evicted_peers: set[str] = field(default_factory=set)


class DecentralizedSwarmMesh:
    """Manages real UDP P2P sockets and multi-task CBBA bundle consensus across the UGV team."""

    HEARTBEAT_TIMEOUT_SEC: float = 1.40

    def __init__(self, world: PhysicsArenaWorld, ai_engine: SwarmAIEngine) -> None:
        self.world = world
        self.ai_engine = ai_engine
        self.commander_link_active: bool = True
        self.packet_seq: int = 0
        self.packet_log: list[P2PPacketRecord] = []
        self.event_feed: list[dict[str, Any]] = []
        self.brains: dict[str, AgentLocalBrain] = {}
        self._init_udp_nodes()

    def _init_udp_nodes(self) -> None:
        self.close()
        self.brains.clear()

        for agent_id in self.world.robots:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("127.0.0.1", 0))
            sock.setblocking(False)
            port = int(sock.getsockname()[1])
            brain = AgentLocalBrain(
                agent_id=agent_id,
                udp_socket=sock,
                udp_port=port,
                winning_bids={rid: 0.0 for rid in self.world.rooms},
                winning_agents={rid: None for rid in self.world.rooms},
                room_completed={rid: None for rid in self.world.rooms},
                peer_last_heartbeat={
                    peer_id: self.world.sim_time for peer_id in self.world.robots
                },
            )
            self.brains[agent_id] = brain

        for agent_id, brain in self.brains.items():
            brain.peer_ports = {
                other_id: ("127.0.0.1", other_brain.udp_port)
                for other_id, other_brain in self.brains.items()
                if other_id != agent_id
            }

        self._log_event(
            "SWARM_BOOT",
            f"Bound {len(self.brains)} UDP P2P sockets: "
            + ", ".join(f"{aid}@127.0.0.1:{b.udp_port}" for aid, b in self.brains.items()),
        )

    def _log_event(self, kind: str, message: str, agent_id: str = "SWARM") -> None:
        self.event_feed.append(
            {
                "time": round(self.world.sim_time, 2),
                "kind": kind,
                "agent_id": agent_id,
                "message": message,
            }
        )
        if len(self.event_feed) > 100:
            self.event_feed = self.event_feed[-100:]

    def close(self) -> None:
        for brain in self.brains.values():
            try:
                brain.udp_socket.close()
            except Exception:
                pass

    def step_swarm_autonomy(self) -> None:
        """Execute one perception -> UDP P2P -> multi-task CBBA bundle queue -> scan cycle."""
        # 1. Onboard Torchvision MobileNetV3 inference for each operational UGV
        for agent_id, robot in self.world.robots.items():
            brain = self.brains[agent_id]
            if not robot.operational or robot.battery <= 0.0:
                brain.bundle_queue.clear()
                brain.claimed_room_id = None
                robot.assigned_room_id = None
                robot.task_bundle_queue = []
                continue

            if robot.last_camera_rgb is not None:
                if self.world.step_count <= 2 or self.world.step_count % 3 == 0 or robot.last_vision_conf == 0.0:
                    pred = self.ai_engine.classify_camera_frame(
                        robot.last_camera_rgb, bbox_hint=robot.last_vision_bbox
                    )
                    robot.last_vision_class = pred.predicted_class
                    robot.last_vision_conf = pred.confidence
                    robot.last_vision_severity = pred.severity

                loc = self.world.location_for_point(robot.x, robot.y)
                if loc in self.world.rooms:
                    room = self.world.rooms[loc]
                    dist_inspect = math.hypot(
                        robot.x - room.inspect_point[0],
                        robot.y - room.inspect_point[1],
                    )
                    if dist_inspect < 0.95 and room.completed_by is None:
                        room.scan_progress = min(1.0, round(room.scan_progress + 0.095, 4))
                        room.detected_by_vision = True
                        room.detected_confidence = max(room.detected_confidence, robot.last_vision_conf)
                        brain.room_vision_evidence[loc] = {
                            "class": robot.last_vision_class,
                            "confidence": robot.last_vision_conf,
                            "severity": robot.last_vision_severity,
                            "observer": agent_id,
                            "time": round(self.world.sim_time, 2),
                        }
                        if room.scan_progress >= 1.0 and room.completed_by is None:
                            room.completed_by = agent_id
                            room.completed_at_time = round(self.world.sim_time, 2)
                            brain.room_completed[loc] = agent_id
                            if loc in brain.bundle_queue:
                                brain.bundle_queue.remove(loc)
                            brain.claimed_room_id = (
                                brain.bundle_queue[0] if brain.bundle_queue else None
                            )
                            robot.assigned_room_id = brain.claimed_room_id
                            robot.task_bundle_queue = list(brain.bundle_queue)
                            robot.current_path = []
                            robot.status_phase = f"VERIFIED_{loc.upper()}"
                            self._log_event(
                                "ZONE_VERIFIED",
                                f"{robot.name} verified {room.name} via Torchvision MobileNetV3 ({robot.last_vision_class}, conf={robot.last_vision_conf*100:.1f}%)",
                                agent_id=agent_id,
                            )

        # 2. Receive incoming P2P UDP datagrams on every UGV's socket
        for agent_id, brain in self.brains.items():
            robot = self.world.robots[agent_id]
            while True:
                try:
                    raw_bytes, _addr = brain.udp_socket.recvfrom(8192)
                except (BlockingIOError, InterruptedError, OSError):
                    break
                if not robot.operational or not robot.comms_online or robot.battery <= 0.0:
                    continue
                brain.packets_received += 1
                try:
                    payload = json.loads(raw_bytes.decode("utf-8"))
                except Exception:
                    continue
                self._process_incoming_p2p_packet(brain, payload)

        # 3. Check P2P heartbeat timeouts & update multi-task CBBA bundle queues
        for agent_id, brain in self.brains.items():
            robot = self.world.robots[agent_id]
            if not robot.operational or robot.battery <= 0.0:
                continue
            self._check_peer_heartbeat_timeouts(brain)
            self._run_local_cbba_bundle_builder(brain, robot)

        # 4. Broadcast P2P UDP bundle & heartbeat packets on initial handshake or every 2 physics steps
        if self.world.step_count == 0 or self.world.step_count % 2 == 0:
            for agent_id, brain in self.brains.items():
                robot = self.world.robots[agent_id]
                if robot.operational and robot.comms_online and robot.battery > 0.0:
                    self._broadcast_p2p_state(brain, robot)

    def _process_incoming_p2p_packet(
        self, brain: AgentLocalBrain, payload: dict[str, Any]
    ) -> None:
        sender_id = str(payload.get("sender_id", ""))
        if not sender_id or sender_id == brain.agent_id:
            return
        brain.peer_last_heartbeat[sender_id] = self.world.sim_time
        brain.evicted_peers.discard(sender_id)
        brain.peer_telemetry[sender_id] = payload.get("telemetry", {})

        peer_completed: dict[str, str | None] = payload.get("room_completed", {})
        for rid, comp_by in peer_completed.items():
            if comp_by is not None and brain.room_completed.get(rid) is None:
                brain.room_completed[rid] = comp_by
                if rid in brain.bundle_queue:
                    brain.bundle_queue.remove(rid)

        peer_vision: dict[str, dict[str, Any]] = payload.get("vision_evidence", {})
        for rid, ev in peer_vision.items():
            cur = brain.room_vision_evidence.get(rid)
            if cur is None or float(ev.get("confidence", 0.0)) >= float(cur.get("confidence", 0.0)):
                brain.room_vision_evidence[rid] = ev

        peer_bids: dict[str, float] = payload.get("winning_bids", {})
        peer_winners: dict[str, str | None] = payload.get("winning_agents", {})

        for rid in self.world.rooms:
            if brain.room_completed.get(rid) is not None:
                continue
            p_winner = peer_winners.get(rid)
            p_bid = float(peer_bids.get(rid, 0.0))
            my_winner = brain.winning_agents.get(rid)
            my_bid = float(brain.winning_bids.get(rid, 0.0))

            if my_winner == sender_id and p_winner != sender_id:
                brain.winning_agents[rid] = p_winner
                brain.winning_bids[rid] = p_bid
                continue

            if p_winner is not None and p_winner not in brain.evicted_peers:
                if (p_bid > my_bid + 1e-4) or (
                    abs(p_bid - my_bid) <= 1e-4 and my_winner is not None and p_winner < my_winner
                ):
                    brain.winning_bids[rid] = p_bid
                    brain.winning_agents[rid] = p_winner
                    if rid in brain.bundle_queue and p_winner != brain.agent_id:
                        was_head = brain.claimed_room_id == rid
                        brain.bundle_queue.remove(rid)
                        if was_head:
                            self.world.robots[brain.agent_id].current_path = []
                        self._log_event(
                            "CBBA_YIELD_TURN",
                            f"{brain.agent_id} yielded {rid} to specialist {p_winner} (peer bid {p_bid:.3f} > own {my_bid:.3f})",
                            agent_id=brain.agent_id,
                        )

    def _check_peer_heartbeat_timeouts(self, brain: AgentLocalBrain) -> None:
        for peer_id, last_t in list(brain.peer_last_heartbeat.items()):
            if peer_id == brain.agent_id:
                continue
            if (self.world.sim_time - last_t) > self.HEARTBEAT_TIMEOUT_SEC:
                if peer_id not in brain.evicted_peers:
                    brain.evicted_peers.add(peer_id)
                    reopened = []
                    for rid, owner in list(brain.winning_agents.items()):
                        if owner == peer_id and brain.room_completed.get(rid) is None:
                            brain.winning_agents[rid] = None
                            brain.winning_bids[rid] = 0.0
                            reopened.append(rid)
                    if reopened:
                        self._log_event(
                            "P2P_FAILOVER_TAKEOVER",
                            f"{brain.agent_id} detected UDP P2P silence from {peer_id} (> {self.HEARTBEAT_TIMEOUT_SEC:.1f}s) -> absorbing {reopened} into backup queue!",
                            agent_id=brain.agent_id,
                        )

    def _check_specialty(self, robot: RobotBody, room: RoomZone) -> tuple[bool, bool]:
        """Return (is_eligible, is_primary_specialty)."""
        req = room.required_sensor
        if req == "any":
            return ("exploration" in robot.operations), True
        if req in robot.primary_sensors:
            return True, True
        if req in robot.backup_sensors:
            return True, False
        return False, False

    def _run_local_cbba_bundle_builder(
        self, brain: AgentLocalBrain, robot: RobotBody
    ) -> None:
        """Build an ordered multi-building task bundle queue (Tier 1 -> Tier 2 -> Tier 3)."""
        brain.peer_last_heartbeat[brain.agent_id] = self.world.sim_time

        # Remove completed or outbid rooms from our local bundle queue
        brain.bundle_queue = [
            rid
            for rid in brain.bundle_queue
            if brain.room_completed.get(rid) is None
            and self.world.rooms[rid].completed_by is None
            and brain.winning_agents.get(rid) in (None, brain.agent_id)
        ]

        ordered_rooms = sorted(
            self.world.rooms.values(),
            key=lambda r: (r.base_hierarchy_tier, r.room_id),
        )

        # Evaluate every building room for the CBBA matrix and add winnable rooms to our bundle queue
        prev_head = brain.claimed_room_id
        candidate_claims: list[tuple[int, float, str]] = []

        for room in ordered_rooms:
            rid = room.room_id
            if room.completed_by is not None or brain.room_completed.get(rid) is not None:
                brain.room_completed[rid] = room.completed_by or brain.room_completed.get(rid)
                continue

            eligible, is_primary = self._check_specialty(robot, room)
            nav_dist = shortest_navigation_distance(
                self.world, (robot.x, robot.y), rid
            )
            ev = brain.room_vision_evidence.get(rid, {})
            vis_conf = float(ev.get("confidence", 0.70))
            sev = float(
                ev.get(
                    "severity",
                    0.92 if room.base_hierarchy_tier == 1 else (0.72 if room.base_hierarchy_tier == 2 else 0.30),
                )
            )

            cur_winner = brain.winning_agents.get(rid)
            peer_best_bid = (
                float(brain.winning_bids.get(rid, 0.0))
                if (cur_winner is not None and cur_winner != brain.agent_id)
                else 0.0
            )

            decision = self.ai_engine.evaluate_tactical_bid(
                sensor_match=eligible,
                is_primary_specialty=is_primary,
                ops_match=("exploration" in robot.operations),
                battery=robot.battery,
                distance_meters=nav_dist,
                vision_confidence=vis_conf,
                hazard_severity=sev,
                hierarchy_tier=room.base_hierarchy_tier,
                peer_best_bid=peer_best_bid,
                is_busy=False,
            )
            brain.latest_decisions[rid] = decision

            if not decision.should_pass and decision.bid_utility > peer_best_bid + 1e-4:
                candidate_claims.append(
                    (room.base_hierarchy_tier, -decision.bid_utility, rid)
                )

        # Sort winnable rooms by Hierarchy Tier first (1 -> 2 -> 3), then highest neural bid
        candidate_claims.sort()
        new_queue = [rid for _tier, _neg_bid, rid in candidate_claims]

        # Preserve current head room if it's still in the same highest available tier so UGV doesn't oscillate mid-drive
        if (
            prev_head in new_queue
            and new_queue
            and self.world.rooms[prev_head].base_hierarchy_tier
            == self.world.rooms[new_queue[0]].base_hierarchy_tier
        ):
            new_queue.remove(prev_head)
            new_queue.insert(0, prev_head)

        brain.bundle_queue = new_queue
        for rid in brain.bundle_queue:
            my_bid = brain.latest_decisions[rid].bid_utility
            brain.winning_bids[rid] = my_bid
            brain.winning_agents[rid] = brain.agent_id

        brain.claimed_room_id = brain.bundle_queue[0] if brain.bundle_queue else None
        robot.assigned_room_id = brain.claimed_room_id
        robot.task_bundle_queue = list(brain.bundle_queue)

        if brain.claimed_room_id != prev_head and brain.claimed_room_id is not None:
            robot.current_path = []
            d_info = brain.latest_decisions[brain.claimed_room_id]
            self._log_event(
                "CBBA_QUEUE_ADVANCE",
                f"{robot.name} active target -> {brain.claimed_room_id} (Tier {d_info.hierarchy_tier}, {d_info.specialty_mode}, Bid={d_info.bid_utility:.3f}) | Queue: {brain.bundle_queue}",
                agent_id=brain.agent_id,
            )

    def _broadcast_p2p_state(self, brain: AgentLocalBrain, robot: RobotBody) -> None:
        packet = {
            "sender_id": brain.agent_id,
            "sim_time": round(self.world.sim_time, 3),
            "claimed_room": brain.claimed_room_id,
            "bundle_queue": brain.bundle_queue,
            "winning_bids": brain.winning_bids,
            "winning_agents": brain.winning_agents,
            "room_completed": brain.room_completed,
            "vision_evidence": brain.room_vision_evidence,
            "telemetry": {
                "x": round(robot.x, 3),
                "y": round(robot.y, 3),
                "z": round(robot.z, 3),
                "battery": round(robot.battery, 4),
                "phase": robot.status_phase,
            },
        }
        encoded = json.dumps(packet, separators=(",", ":")).encode("utf-8")

        for peer_id, addr in brain.peer_ports.items():
            try:
                brain.udp_socket.sendto(encoded, addr)
                brain.packets_sent += 1
                self.packet_seq += 1
                self.packet_log.append(
                    P2PPacketRecord(
                        seq_id=self.packet_seq,
                        sim_time=round(self.world.sim_time, 2),
                        sender_id=brain.agent_id,
                        receiver_id=peer_id,
                        packet_type="UDP_CBBA_BUNDLE",
                        summary=f"active={brain.claimed_room_id or 'NONE'} queue={len(brain.bundle_queue)} bat={robot.battery*100:.0f}%",
                        bytes_size=len(encoded),
                    )
                )
            except Exception:
                pass

        if len(self.packet_log) > 60:
            self.packet_log = self.packet_log[-60:]
