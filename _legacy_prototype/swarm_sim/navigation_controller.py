"""Shapely + NetworkX Roadway/Doorway Visibility Graph A* Planner & LiDAR DWA Controller."""
from __future__ import annotations

import math
import networkx as nx

from .physics_world import PhysicsArenaWorld, RobotBody, RoomZone


def normalize_angle(angle: float) -> float:
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def build_navigation_graph(world: PhysicsArenaWorld) -> nx.Graph:
    """Build and cache a NetworkX navigation graph across the 36m x 28m industrial complex."""
    cached = getattr(world, "_cached_nav_graph", None)
    if cached is not None:
        return cached

    g = nx.Graph()
    avenue_xs = [3.5, 6.0, 9.5, 12.5, 15.5, 18.0, 20.5, 23.5, 26.5, 30.0, 32.5]
    for x in avenue_xs:
        g.add_node(f"ave_n_{x}", pos=(x, 12.4))
        g.add_node(f"ave_c_{x}", pos=(x, 14.0))
        g.add_node(f"ave_s_{x}", pos=(x, 15.6))

    nodes_with_pos = {n: d["pos"] for n, d in g.nodes(data=True)}
    node_names = list(nodes_with_pos.keys())
    for i in range(len(node_names)):
        for j in range(i + 1, len(node_names)):
            n1, n2 = node_names[i], node_names[j]
            x1, y1 = nodes_with_pos[n1]
            x2, y2 = nodes_with_pos[n2]
            dist = math.hypot(x2 - x1, y2 - y1)
            if dist <= 4.5 and world.has_line_of_sight(x1, y1, x2, y2):
                g.add_edge(n1, n2, weight=dist)

    for rid, room in world.rooms.items():
        cx, cy = room.corridor_entry_point
        dx, dy = room.doorway_point
        ix, iy = room.inspect_point

        n_entry = f"{rid}_entry"
        n_door = f"{rid}_door"
        n_insp = f"{rid}_inspect"

        g.add_node(n_entry, pos=(cx, cy))
        g.add_node(n_door, pos=(dx, dy))
        g.add_node(n_insp, pos=(ix, iy))

        g.add_edge(n_entry, n_door, weight=math.hypot(dx - cx, dy - cy))
        g.add_edge(n_door, n_insp, weight=math.hypot(ix - dx, iy - dy))

        for ave_n, (ax, ay) in nodes_with_pos.items():
            d = math.hypot(ax - cx, ay - cy)
            if d <= 3.8 and world.has_line_of_sight(cx, cy, ax, ay):
                g.add_edge(n_entry, ave_n, weight=d)

    setattr(world, "_cached_nav_graph", g)
    return g


def shortest_navigation_distance(
    world: PhysicsArenaWorld,
    start_xy: tuple[float, float],
    target_room_id: str,
) -> float:
    """Fast doorway-routed travel distance in meters across the industrial complex."""
    room = world.rooms[target_room_id]
    sx, sy = start_xy
    cur_loc = world.location_for_point(sx, sy)
    if cur_loc == target_room_id:
        return math.hypot(room.inspect_point[0] - sx, room.inspect_point[1] - sy)

    total = 0.0
    cx, cy = sx, sy
    if cur_loc in world.rooms:
        cur_rm = world.rooms[cur_loc]
        total += math.hypot(cur_rm.doorway_point[0] - cx, cur_rm.doorway_point[1] - cy)
        cx, cy = cur_rm.corridor_entry_point

    ex, ey = room.corridor_entry_point
    dx, dy = room.doorway_point
    ix, iy = room.inspect_point
    total += math.hypot(ex - cx, ey - cy)
    total += math.hypot(dx - ex, dy - ey)
    total += math.hypot(ix - dx, iy - dy)
    return total


def plan_doorway_path(
    world: PhysicsArenaWorld,
    start_x: float,
    start_y: float,
    target_room_id: str,
) -> list[tuple[float, float]]:
    """Compute collision-free waypoint path using NetworkX A* across roadways and building doorways."""
    if target_room_id not in world.rooms:
        return []

    base_g = build_navigation_graph(world)
    g = base_g.copy()
    start_node = "robot_start"
    g.add_node(start_node, pos=(start_x, start_y))

    current_loc = world.location_for_point(start_x, start_y)
    if current_loc == target_room_id:
        return [world.rooms[target_room_id].inspect_point]

    if current_loc in world.rooms:
        # Inside another building: link directly to its doorway and corridor entry to exit cleanly
        door_n = f"{current_loc}_door"
        entry_n = f"{current_loc}_entry"
        dx, dy = g.nodes[door_n]["pos"]
        g.add_edge(start_node, door_n, weight=math.hypot(dx - start_x, dy - start_y))
        ex, ey = g.nodes[entry_n]["pos"]
        if world.has_line_of_sight(start_x, start_y, ex, ey):
            g.add_edge(start_node, entry_n, weight=math.hypot(ex - start_x, ey - start_y))
    else:
        for n, data in base_g.nodes(data=True):
            nx_p, ny_p = data["pos"]
            d = math.hypot(nx_p - start_x, ny_p - start_y)
            if d <= 7.0 and world.has_line_of_sight(start_x, start_y, nx_p, ny_p):
                g.add_edge(start_node, n, weight=d)

    goal_node = f"{target_room_id}_inspect"
    try:
        node_seq = nx.astar_path(
            g,
            start_node,
            goal_node,
            heuristic=lambda u, v: math.hypot(
                g.nodes[u]["pos"][0] - g.nodes[v]["pos"][0],
                g.nodes[u]["pos"][1] - g.nodes[v]["pos"][1],
            ),
            weight="weight",
        )
        return [g.nodes[n]["pos"] for n in node_seq[1:]]
    except Exception:
        room = world.rooms[target_room_id]
        return [room.corridor_entry_point, room.doorway_point, room.inspect_point]


def compute_lidar_dwa_control(
    world: PhysicsArenaWorld,
    robot: RobotBody,
) -> tuple[float, float]:
    """Compute (linear_velocity_v, angular_velocity_omega) using NetworkX A* + 60-beam LiDAR DWA."""
    if not robot.operational or robot.battery <= 0.0:
        return 0.0, 0.0

    if not robot.assigned_room_id or robot.assigned_room_id not in world.rooms:
        robot.current_path = []
        robot.status_phase = "STANDBY_COMPLETE"
        return 0.0, 0.0

    target_room: RoomZone = world.rooms[robot.assigned_room_id]

    if not robot.current_path:
        robot.current_path = plan_doorway_path(
            world, robot.x, robot.y, robot.assigned_room_id
        )

    while len(robot.current_path) > 1:
        wx, wy = robot.current_path[0]
        if math.hypot(robot.x - wx, robot.y - wy) < 0.85:
            robot.current_path.pop(0)
        else:
            break

    goal_x, goal_y = (
        robot.current_path[0] if robot.current_path else target_room.inspect_point
    )
    dist_to_final = math.hypot(
        robot.x - target_room.inspect_point[0],
        robot.y - target_room.inspect_point[1],
    )

    if dist_to_final < 0.85 and len(robot.current_path) <= 1:
        robot.status_phase = f"SCANNING_{target_room.room_id.upper()}"
        hx, hy, _ = target_room.hazard_pos
        desired_yaw = math.atan2(hy - robot.y, hx - robot.x)
        yaw_err = normalize_angle(desired_yaw - robot.yaw)
        omega = float(max(-2.4, min(2.4, 3.6 * yaw_err)))
        return 0.0, omega

    robot.status_phase = f"TRANSIT_{target_room.room_id.upper()}"
    desired_heading = math.atan2(goal_y - robot.y, goal_x - robot.x)

    # 60-Beam LiDAR repulsive vector field
    repulse_x = 0.0
    repulse_y = 0.0
    num_rays = len(robot.lidar_ranges)
    for i, r_dist in enumerate(robot.lidar_ranges):
        if r_dist < 0.95:
            rel_angle = (2.0 * math.pi * i) / num_rays
            if rel_angle > math.pi:
                rel_angle -= 2.0 * math.pi
            if abs(rel_angle) < 1.55:
                world_angle = robot.yaw + rel_angle
                strength = ((0.95 - r_dist) / 0.95) ** 2
                repulse_x -= strength * math.cos(world_angle)
                repulse_y -= strength * math.sin(world_angle)

    attract_x = math.cos(desired_heading)
    attract_y = math.sin(desired_heading)
    combined_x = attract_x + 0.32 * repulse_x
    combined_y = attract_y + 0.32 * repulse_y
    steered_heading = math.atan2(combined_y, combined_x)

    heading_err = normalize_angle(steered_heading - robot.yaw)
    front_clearance = min(
        [robot.lidar_ranges[0], robot.lidar_ranges[1], robot.lidar_ranges[-1]],
        default=3.0,
    )
    turn_factor = max(0.22, math.cos(min(math.pi / 2, abs(heading_err))))
    clearance_factor = min(1.0, max(0.30, (front_clearance - 0.25) / 1.0))

    cmd_v = robot.max_speed * turn_factor * clearance_factor
    if dist_to_final < 1.4:
        cmd_v = min(cmd_v, max(0.65, dist_to_final * 1.3))

    cmd_w = max(-robot.max_omega, min(robot.max_omega, 3.8 * heading_err))
    return round(cmd_v, 4), round(cmd_w, 4)
