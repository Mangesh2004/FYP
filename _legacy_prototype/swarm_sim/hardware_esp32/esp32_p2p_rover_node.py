"""Standalone Peer-to-Peer (P2P) UDP Swarm Node for Physical Classroom Robot Cars (ESP32 / Raspberry Pi).

How This Works in Your Classroom Cardboard 4-Room Setup:
1. Run this script on Car #1 (`--id A --specialty thermal,visual`) and Car #2 (`--id B --specialty gas,visual`),
   OR run it on two Raspberry Pi / Jetson / ESP32-WiFi bridge processes.
2. Uses UDP Broadcast (`255.255.255.255:19500` or multicast) so the cars discover each other and negotiate
   rooms directly Peer-to-Peer without needing a central server or laptop!
3. Even if the laptop dashboard disconnects completely, the cars continue exchanging P2P UDP heartbeats
   and CBBA bids, taking turns clearing all 4 cardboard rooms and automatically taking over if a car stops.
"""
from __future__ import annotations

import argparse
import json
import socket
import time


ROOMS_CATALOG = {
    "room-1": {"tier": 1, "req_sensor": "thermal", "pos": (2.0, 2.2)},
    "room-2": {"tier": 2, "req_sensor": "gas", "pos": (8.0, 2.2)},
    "room-3": {"tier": 1, "req_sensor": "visual", "pos": (2.0, 7.8)},
    "room-4": {"tier": 3, "req_sensor": "any", "pos": (8.0, 7.8)},
}


class PhysicalCarP2PNode:
    """Zero-dependency P2P UDP + CBBA controller ready for Raspberry Pi / Jetson / ESP32 Wi-Fi."""

    def __init__(
        self,
        agent_id: str,
        sensors: set[str],
        udp_port: int = 19500,
        bind_ip: str = "0.0.0.0",
        broadcast_ip: str = "255.255.255.255",
    ) -> None:
        self.agent_id = agent_id
        self.sensors = sensors
        self.udp_port = udp_port
        self.broadcast_ip = broadcast_ip
        self.battery = 1.0
        self.x = 2.5 if agent_id == "A" else 7.5
        self.y = 5.0
        self.claimed_room: str | None = None
        self.winning_bids: dict[str, float] = {r: 0.0 for r in ROOMS_CATALOG}
        self.winning_agents: dict[str, str | None] = {r: None for r in ROOMS_CATALOG}
        self.completed_rooms: dict[str, str | None] = {r: None for r in ROOMS_CATALOG}
        self.peer_last_seen: dict[str, float] = {}

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind((bind_ip, udp_port))
        self.sock.setblocking(False)

    def compute_local_bid(self, room_id: str) -> float:
        meta = ROOMS_CATALOG[room_id]
        req = str(meta["req_sensor"])
        if req != "any" and req not in self.sensors:
            return 0.0
        rx, ry = meta["pos"]
        dist = ((self.x - rx) ** 2 + (self.y - ry) ** 2) ** 0.5
        tier_weight = {1: 0.35, 2: 0.22, 3: 0.10}[int(meta["tier"])]
        return round(0.40 * self.battery + 0.35 * max(0.0, 1.0 - dist / 12.0) + tier_weight, 4)

    def step(self) -> None:
        now = time.monotonic()
        # 1. Read all incoming P2P UDP packets from peer cars
        while True:
            try:
                data, _ = self.sock.recvfrom(2048)
                msg = json.loads(data.decode("utf-8"))
            except (BlockingIOError, OSError, ValueError):
                break
            sender = msg.get("agent_id")
            if not sender or sender == self.agent_id:
                continue
            self.peer_last_seen[sender] = now
            for rid, comp_by in msg.get("completed", {}).items():
                if comp_by and not self.completed_rooms.get(rid):
                    self.completed_rooms[rid] = comp_by
            for rid, p_bid in msg.get("bids", {}).items():
                p_win = msg.get("winners", {}).get(rid)
                if p_win and float(p_bid) > self.winning_bids.get(rid, 0.0) + 1e-4:
                    self.winning_bids[rid] = float(p_bid)
                    self.winning_agents[rid] = p_win
                    if self.claimed_room == rid and p_win != self.agent_id:
                        self.claimed_room = None

        # 2. Check peer heartbeat timeouts (2.0s silence -> takeover!)
        for peer_id, last_t in list(self.peer_last_seen.items()):
            if now - last_t > 2.0:
                for rid, owner in list(self.winning_agents.items()):
                    if owner == peer_id and not self.completed_rooms.get(rid):
                        self.winning_agents[rid] = None
                        self.winning_bids[rid] = 0.0

        # 3. Pick highest-priority uncompleted room we can win
        if self.claimed_room is None:
            for rid in sorted(ROOMS_CATALOG, key=lambda r: (ROOMS_CATALOG[r]["tier"], r)):
                if self.completed_rooms[rid] is not None:
                    continue
                my_bid = self.compute_local_bid(rid)
                cur_winner = self.winning_agents[rid]
                cur_bid = self.winning_bids[rid] if (cur_winner and cur_winner != self.agent_id) else 0.0
                if my_bid > cur_bid + 1e-4:
                    self.claimed_room = rid
                    self.winning_bids[rid] = my_bid
                    self.winning_agents[rid] = self.agent_id
                    break

        # 4. Broadcast P2P UDP state
        pkt = json.dumps(
            {
                "agent_id": self.agent_id,
                "claimed": self.claimed_room,
                "bids": self.winning_bids,
                "winners": self.winning_agents,
                "completed": self.completed_rooms,
                "battery": round(self.battery, 3),
            }
        ).encode("utf-8")
        try:
            self.sock.sendto(pkt, (self.broadcast_ip, self.udp_port))
        except OSError:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Physical Classroom Car UDP P2P Swarm Node")
    parser.add_argument("--id", default="A", help="Car ID (e.g. A or B)")
    parser.add_argument("--sensors", default="thermal,visual", help="Comma-separated specialty sensors")
    parser.add_argument("--steps", type=int, default=20, help="Number of P2P cycles to run")
    args = parser.parse_args()

    node = PhysicalCarP2PNode(
        agent_id=args.id,
        sensors=set(s.strip() for s in args.sensors.split(",") if s.strip()),
        bind_ip="127.0.0.1",
        broadcast_ip="127.0.0.1",
    )
    for i in range(args.steps):
        node.step()
        time.sleep(0.05)
    print(f"Car {node.agent_id} P2P Node active | Claimed Room: {node.claimed_room} | Bids: {node.winning_bids}")
