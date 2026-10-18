"""Continuous 3D/2.5D Rigid-Body Physics, Shapely Geometry, 60-Beam LiDAR & 128x128 Camera Engine.

Models a 36m x 28m Multi-Building Industrial & Office Facility with 6 distinct building zones,
interior partition walls, outdoor roadways, trees/bollards, and 2 specialized 4WD UGV robots:
  - UGV-1 (Alpha-SAR): Thermal Imaging & Structural Search-and-Rescue Specialist
  - UGV-2 (Beta-Hazmat): Chemical/Gas Detection & Hazmat Containment Specialist
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Literal

import numpy as np
from shapely.geometry import LineString, Point


@dataclass(frozen=True)
class WallSegment:
    x1: float
    y1: float
    x2: float
    y2: float
    height: float = 3.8
    thickness: float = 0.25
    tag: str = "building_wall"
    building_id: str | None = None


@dataclass
class RoomZone:
    room_id: str
    name: str
    building_style: str  # "boiler_plant", "chem_lab", "office_wing", "substation", "tank_depot", "warehouse"
    bounds: tuple[float, float, float, float]  # (x_min, y_min, x_max, y_max)
    roof_height: float
    facade_color: str
    doorway_point: tuple[float, float]
    corridor_entry_point: tuple[float, float]
    inspect_point: tuple[float, float]
    hazard_pos: tuple[float, float, float]
    true_hazard_class: str  # "fire_hazard", "gas_hazard", "trapped_person", "thermal_overheat", "clear_room"
    required_sensor: str  # "thermal", "gas", "sar_visual", "any"
    base_hierarchy_tier: int  # 1 = Critical Life/Fire/Gas, 2 = High Infrastructure, 3 = Routine Clearance
    scan_progress: float = 0.0
    completed_by: str | None = None
    completed_at_time: float | None = None
    detected_by_vision: bool = False
    detected_confidence: float = 0.0


@dataclass
class ObstacleBox:
    obs_id: str
    kind: str  # "bollard", "tree", "tank", "crate", "vehicle"
    x: float
    y: float
    radius: float
    height: float = 1.2


@dataclass
class RobotBody:
    agent_id: str
    name: str
    specialty_title: str
    color_hex: str
    vehicle_mode: Literal["rover", "drone"]
    primary_sensors: set[str]
    backup_sensors: set[str]
    operations: set[str]
    x: float
    y: float
    z: float
    yaw: float
    vx: float = 0.0
    vy: float = 0.0
    vz: float = 0.0
    omega: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    radius: float = 0.42
    mass: float = 18.0
    max_speed: float = 3.8
    max_omega: float = 3.2
    battery: float = 1.0
    operational: bool = True
    comms_online: bool = True
    assigned_room_id: str | None = None
    task_bundle_queue: list[str] = field(default_factory=list)
    status_phase: str = "IDLE_STAGING"
    odometry_distance: float = 0.0
    lidar_ranges: list[float] = field(default_factory=lambda: [10.0] * 60)
    lidar_hit_points: list[tuple[float, float]] = field(default_factory=list)
    current_path: list[tuple[float, float]] = field(default_factory=list)
    last_camera_rgb: np.ndarray | None = None
    last_vision_class: str = "corridor_wall"
    last_vision_conf: float = 0.0
    last_vision_severity: float = 0.0
    last_vision_bbox: tuple[int, int, int, int] | None = None

    @property
    def sensors(self) -> set[str]:
        return self.primary_sensors | self.backup_sensors


def ray_segment_intersection(
    ox: float,
    oy: float,
    dx: float,
    dy: float,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    max_range: float,
) -> float | None:
    sx = x2 - x1
    sy = y2 - y1
    denom = dx * sy - dy * sx
    if abs(denom) < 1e-8:
        return None
    qpx = x1 - ox
    qpy = y1 - oy
    t = (qpx * sy - qpy * sx) / denom
    u = (qpx * dy - qpy * dx) / denom
    if 0.0 <= t <= max_range and 0.0 <= u <= 1.0:
        return t
    return None


def ray_circle_intersection(
    ox: float,
    oy: float,
    dx: float,
    dy: float,
    cx: float,
    cy: float,
    r: float,
    max_range: float,
) -> float | None:
    fx = ox - cx
    fy = oy - cy
    b = 2.0 * (fx * dx + fy * dy)
    c = (fx * fx + fy * fy) - r * r
    disc = b * b - 4.0 * c
    if disc < 0.0:
        return None
    sqrt_disc = math.sqrt(disc)
    t1 = (-b - sqrt_disc) * 0.5
    if 0.0 <= t1 <= max_range:
        return t1
    return None


def point_to_segment_projection(
    px: float, py: float, x1: float, y1: float, x2: float, y2: float
) -> tuple[float, float, float]:
    vx = x2 - x1
    vy = y2 - y1
    l2 = vx * vx + vy * vy
    if l2 < 1e-9:
        return x1, y1, math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * vx + (py - y1) * vy) / l2))
    cx = x1 + t * vx
    cy = y1 + t * vy
    return cx, cy, math.hypot(px - cx, py - cy)


class PhysicsArenaWorld:
    """Continuous 3D/2.5D 36m x 28m Multi-Building Industrial & Office Facility."""

    WIDTH: float = 36.0
    HEIGHT: float = 28.0
    LIDAR_NUM_RAYS: int = 60
    LIDAR_MAX_RANGE: float = 10.0

    def __init__(
        self,
        *,
        vehicle_mode: Literal["rover", "drone"] = "rover",
        swarm_mode: Literal["2_ugv_industrial", "4_agents", "2_cars_classroom"] = "2_ugv_industrial",
        seed: int = 42,
    ) -> None:
        self.vehicle_mode: Literal["rover", "drone"] = vehicle_mode
        self.swarm_mode: Literal["2_ugv_industrial", "4_agents", "2_cars_classroom"] = swarm_mode
        self.seed = seed
        self.rng = np.random.default_rng(seed)
        self.sim_time: float = 0.0
        self.step_count: int = 0
        self.walls: list[WallSegment] = []
        self.shapely_walls: list[LineString] = []
        self.rooms: dict[str, RoomZone] = {}
        self.obstacles: list[ObstacleBox] = []
        self.robots: dict[str, RobotBody] = {}
        self._build_industrial_complex()
        self._spawn_robots()
        self.update_all_sensors()

    def _add_building_enclosure(
        self,
        building_id: str,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        door_side: Literal["south", "north"],
        door_x_center: float,
        door_width: float = 2.4,
        height: float = 3.8,
    ) -> None:
        half_d = door_width * 0.5
        dx0 = door_x_center - half_d
        dx1 = door_x_center + half_d

        # West & East walls
        self.walls.append(WallSegment(x0, y0, x0, y1, height, 0.25, "building_wall", building_id))
        self.walls.append(WallSegment(x1, y0, x1, y1, height, 0.25, "building_wall", building_id))

        if door_side == "south":
            # Solid North wall, split South wall for doorway
            self.walls.append(WallSegment(x0, y0, x1, y0, height, 0.25, "building_wall", building_id))
            self.walls.append(WallSegment(x0, y1, dx0, y1, height, 0.25, "building_wall", building_id))
            self.walls.append(WallSegment(dx1, y1, x1, y1, height, 0.25, "building_wall", building_id))
            # Interior baffle wall with wide internal passage
            mid_y = y0 + (y1 - y0) * 0.52
            self.walls.append(WallSegment(x0, mid_y, x0 + 2.1, mid_y, height * 0.85, 0.18, "interior_wall", building_id))
            self.walls.append(WallSegment(x1 - 2.1, mid_y, x1, mid_y, height * 0.85, 0.18, "interior_wall", building_id))
        else:
            # Solid South wall, split North wall for doorway
            self.walls.append(WallSegment(x0, y1, x1, y1, height, 0.25, "building_wall", building_id))
            self.walls.append(WallSegment(x0, y0, dx0, y0, height, 0.25, "building_wall", building_id))
            self.walls.append(WallSegment(dx1, y0, x1, y0, height, 0.25, "building_wall", building_id))
            mid_y = y0 + (y1 - y0) * 0.48
            self.walls.append(WallSegment(x0, mid_y, x0 + 2.1, mid_y, height * 0.85, 0.18, "interior_wall", building_id))
            self.walls.append(WallSegment(x1 - 2.1, mid_y, x1, mid_y, height * 0.85, 0.18, "interior_wall", building_id))

    def _build_industrial_complex(self) -> None:
        """Construct the 36m x 28m 6-building industrial & office facility."""
        self.walls = [
            # Outer compound perimeter fence/wall
            WallSegment(0.0, 0.0, self.WIDTH, 0.0, 2.2, 0.3, "perimeter"),
            WallSegment(self.WIDTH, 0.0, self.WIDTH, self.HEIGHT, 2.2, 0.3, "perimeter"),
            WallSegment(self.WIDTH, self.HEIGHT, 0.0, self.HEIGHT, 2.2, 0.3, "perimeter"),
            WallSegment(0.0, self.HEIGHT, 0.0, 0.0, 2.2, 0.3, "perimeter"),
        ]

        # 6 Multi-Room Industrial & Office Buildings
        self._add_building_enclosure("zone-1-boiler", 1.5, 1.5, 10.5, 10.0, "south", 6.0, 2.4, 4.2)
        self._add_building_enclosure("zone-4-substation", 14.0, 1.5, 22.0, 10.0, "south", 18.0, 2.4, 3.6)
        self._add_building_enclosure("zone-2-chemlab", 25.5, 1.5, 34.5, 10.0, "south", 30.0, 2.4, 3.9)

        self._add_building_enclosure("zone-3-offices", 1.5, 18.0, 10.5, 26.5, "north", 6.0, 2.4, 4.0)
        self._add_building_enclosure("zone-6-warehouse", 14.0, 18.0, 22.0, 26.5, "north", 18.0, 2.4, 4.4)
        self._add_building_enclosure("zone-5-gasdepot", 25.5, 18.0, 34.5, 26.5, "north", 30.0, 2.4, 3.6)

        self.shapely_walls = [
            LineString([(w.x1, w.y1), (w.x2, w.y2)]) for w in self.walls
        ]

        self.rooms = {
            "zone-1-boiler": RoomZone(
                room_id="zone-1-boiler",
                name="Bldg A: Refinery Boiler Hall",
                building_style="boiler_plant",
                bounds=(1.5, 1.5, 10.5, 10.0),
                roof_height=4.2,
                facade_color="#cbd5e1",
                doorway_point=(6.0, 10.0),
                corridor_entry_point=(6.0, 12.2),
                inspect_point=(6.0, 5.2),
                hazard_pos=(6.0, 3.2, 0.9),
                true_hazard_class="fire_hazard",
                required_sensor="thermal",
                base_hierarchy_tier=1,
            ),
            "zone-2-chemlab": RoomZone(
                room_id="zone-2-chemlab",
                name="Bldg B: Chemical Synthesis Lab",
                building_style="chem_lab",
                bounds=(25.5, 1.5, 34.5, 10.0),
                roof_height=3.9,
                facade_color="#e2e8f0",
                doorway_point=(30.0, 10.0),
                corridor_entry_point=(30.0, 12.2),
                inspect_point=(30.0, 5.2),
                hazard_pos=(30.0, 3.2, 0.9),
                true_hazard_class="gas_hazard",
                required_sensor="gas",
                base_hierarchy_tier=1,
            ),
            "zone-3-offices": RoomZone(
                room_id="zone-3-offices",
                name="Bldg C: Collapsed Admin Offices",
                building_style="office_wing",
                bounds=(1.5, 18.0, 10.5, 26.5),
                roof_height=4.0,
                facade_color="#f1f5f9",
                doorway_point=(6.0, 18.0),
                corridor_entry_point=(6.0, 15.8),
                inspect_point=(6.0, 22.8),
                hazard_pos=(6.0, 24.8, 0.9),
                true_hazard_class="trapped_person",
                required_sensor="sar_visual",
                base_hierarchy_tier=1,
            ),
            "zone-4-substation": RoomZone(
                room_id="zone-4-substation",
                name="Bldg D: HV Power Substation",
                building_style="substation",
                bounds=(14.0, 1.5, 22.0, 10.0),
                roof_height=3.6,
                facade_color="#d1d5db",
                doorway_point=(18.0, 10.0),
                corridor_entry_point=(18.0, 12.2),
                inspect_point=(18.0, 5.2),
                hazard_pos=(18.0, 3.2, 0.9),
                true_hazard_class="fire_hazard",
                required_sensor="thermal",
                base_hierarchy_tier=2,
            ),
            "zone-5-gasdepot": RoomZone(
                room_id="zone-5-gasdepot",
                name="Bldg E: Pressurized Tank Depot",
                building_style="tank_depot",
                bounds=(25.5, 18.0, 34.5, 26.5),
                roof_height=3.6,
                facade_color="#e5e7eb",
                doorway_point=(30.0, 18.0),
                corridor_entry_point=(30.0, 15.8),
                inspect_point=(30.0, 22.8),
                hazard_pos=(30.0, 24.8, 0.9),
                true_hazard_class="gas_hazard",
                required_sensor="gas",
                base_hierarchy_tier=2,
            ),
            "zone-6-warehouse": RoomZone(
                room_id="zone-6-warehouse",
                name="Bldg F: Central Logistics Warehouse",
                building_style="warehouse",
                bounds=(14.0, 18.0, 22.0, 26.5),
                roof_height=4.4,
                facade_color="#cbd5e1",
                doorway_point=(18.0, 18.0),
                corridor_entry_point=(18.0, 15.8),
                inspect_point=(18.0, 22.8),
                hazard_pos=(18.0, 24.8, 0.8),
                true_hazard_class="clear_room",
                required_sensor="any",
                base_hierarchy_tier=3,
            ),
        }

        # Realistic outdoor industrial bollards, trees, and storage tanks in courtyards
        self.obstacles = [
            ObstacleBox("tree-nw", "tree", 12.2, 11.2, 0.45, 2.8),
            ObstacleBox("tree-ne", "tree", 23.8, 11.2, 0.45, 2.8),
            ObstacleBox("tree-sw", "tree", 12.2, 16.8, 0.45, 2.8),
            ObstacleBox("tree-se", "tree", 23.8, 16.8, 0.45, 2.8),
            ObstacleBox("tank-b1", "tank", 3.2, 7.8, 0.55, 1.8),
            ObstacleBox("tank-b2", "tank", 32.8, 7.8, 0.55, 1.8),
            ObstacleBox("crate-b3", "crate", 8.8, 20.2, 0.50, 1.2),
            ObstacleBox("crate-b6", "crate", 15.6, 20.2, 0.50, 1.4),
        ]

    def _spawn_robots(self) -> None:
        base_z = 1.65 if self.vehicle_mode == "drone" else 0.25
        # 2 Specialized UGVs with primary specialties + emergency cross-backup capability
        self.robots = {
            "UGV-1": RobotBody(
                agent_id="UGV-1",
                name="UGV-1 Alpha [Thermal & SAR Specialist]",
                specialty_title="Primary: Thermal & SAR | Backup: Hazmat",
                color_hex="#0284c7",
                vehicle_mode=self.vehicle_mode,
                primary_sensors={"thermal", "sar_visual"},
                backup_sensors={"gas"},
                operations={"exploration", "structural_sar"},
                x=15.0,
                y=13.2,
                z=base_z,
                yaw=math.pi,
                battery=0.98,
            ),
            "UGV-2": RobotBody(
                agent_id="UGV-2",
                name="UGV-2 Beta [Gas & Hazmat Specialist]",
                specialty_title="Primary: Gas & Hazmat | Backup: Thermal/SAR",
                color_hex="#ea580c",
                vehicle_mode=self.vehicle_mode,
                primary_sensors={"gas"},
                backup_sensors={"thermal", "sar_visual"},
                operations={"exploration", "hazmat_containment"},
                x=21.0,
                y=14.8,
                z=base_z,
                yaw=0.0,
                battery=0.96,
            ),
        }

    def location_for_point(self, x: float, y: float) -> str:
        for room_id, room in self.rooms.items():
            x0, y0, x1, y1 = room.bounds
            if x0 <= x <= x1 and y0 <= y <= y1:
                return room_id
        return "avenue_roadway"

    def has_line_of_sight(self, x1: float, y1: float, x2: float, y2: float) -> bool:
        """Fast segment raycast to check if any wall blocks line-of-sight."""
        dist = math.hypot(x2 - x1, y2 - y1)
        if dist < 1e-6:
            return True
        dx = (x2 - x1) / dist
        dy = (y2 - y1) / dist
        for wall in self.walls:
            t = ray_segment_intersection(x1, y1, dx, dy, wall.x1, wall.y1, wall.x2, wall.y2, dist - 0.06)
            if t is not None and t > 0.04:
                return False
        return True

    def cast_lidar_for_robot(self, robot: RobotBody) -> None:
        ranges: list[float] = []
        hit_points: list[tuple[float, float]] = []
        for i in range(self.LIDAR_NUM_RAYS):
            angle = robot.yaw + (2.0 * math.pi * i) / self.LIDAR_NUM_RAYS
            dx = math.cos(angle)
            dy = math.sin(angle)
            best_t = self.LIDAR_MAX_RANGE

            for wall in self.walls:
                t = ray_segment_intersection(
                    robot.x, robot.y, dx, dy, wall.x1, wall.y1, wall.x2, wall.y2, best_t
                )
                if t is not None and t < best_t:
                    best_t = t

            for obs in self.obstacles:
                t = ray_circle_intersection(
                    robot.x, robot.y, dx, dy, obs.x, obs.y, obs.radius, best_t
                )
                if t is not None and t < best_t:
                    best_t = t

            for other_id, other in self.robots.items():
                if other_id == robot.agent_id:
                    continue
                t = ray_circle_intersection(
                    robot.x, robot.y, dx, dy, other.x, other.y, other.radius, best_t
                )
                if t is not None and t < best_t:
                    best_t = t

            ranges.append(round(best_t, 3))
            hit_points.append((round(robot.x + dx * best_t, 3), round(robot.y + dy * best_t, 3)))

        robot.lidar_ranges = ranges
        robot.lidar_hit_points = hit_points

    def render_onboard_camera_rgb(
        self, robot: RobotBody, width: int = 128, height: int = 128
    ) -> np.ndarray:
        """Render a realistic 128x128x3 daylight perspective RGB camera frame from the UGV's pose."""
        img = np.zeros((height, width, 3), dtype=np.float32)
        horizon = int(height * 0.46 + math.sin(robot.pitch) * 10)
        horizon = max(20, min(height - 20, horizon))

        # Daylight sky / bright industrial ceiling gradient
        for y in range(horizon):
            t = y / max(1, horizon)
            img[y, :, 0] = 0.78 + 0.14 * t
            img[y, :, 1] = 0.85 + 0.10 * t
            img[y, :, 2] = 0.94 + 0.05 * t

        # Concrete/asphalt ground with distance shading
        for y in range(horizon, height):
            t = (y - horizon) / max(1, height - horizon)
            img[y, :, 0] = 0.62 - 0.14 * t
            img[y, :, 1] = 0.64 - 0.14 * t
            img[y, :, 2] = 0.67 - 0.13 * t

        # Raycast 32 horizontal columns for architectural concrete walls
        fov = math.radians(72.0)
        col_w = width // 32
        for col_group in range(32):
            c0 = col_group * col_w
            c1 = c0 + col_w
            rel_angle = -0.5 * fov + (col_group + 0.5) / 32.0 * fov
            ray_angle = robot.yaw + rel_angle
            dx = math.cos(ray_angle)
            dy = math.sin(ray_angle)
            dist = self.LIDAR_MAX_RANGE
            for wall in self.walls:
                t = ray_segment_intersection(
                    robot.x, robot.y, dx, dy, wall.x1, wall.y1, wall.x2, wall.y2, dist
                )
                if t is not None and t < dist:
                    dist = t
            if dist < self.LIDAR_MAX_RANGE - 0.05:
                perp_dist = max(0.45, dist * math.cos(rel_angle))
                wall_half_h = int(min(height // 2 - 2, max(8.0, 56.0 / perp_dist)))
                shade = float(np.clip(0.88 - 0.045 * perp_dist, 0.52, 0.88))
                y_top = max(0, horizon - wall_half_h)
                y_bot = min(height, horizon + wall_half_h)
                img[y_top:y_bot, c0:c1, :] = np.array(
                    [shade * 0.96, shade * 0.98, shade], dtype=np.float32
                )
                # Architectural roof trim & baseboard lines
                img[y_top : min(height, y_top + 2), c0:c1, :] = np.array([0.35, 0.42, 0.52], dtype=np.float32)

        # Project visible 3D room hazard targets into the 128x128 camera frame
        yy, xx = np.ogrid[:height, :width]
        visible_bbox: tuple[int, int, int, int] | None = None

        for room in self.rooms.values():
            hx, hy, _hz = room.hazard_pos
            vx = hx - robot.x
            vy = hy - robot.y
            dist = math.hypot(vx, vy)
            if dist > 9.5 or dist < 0.3:
                continue
            angle_to_target = math.atan2(vy, vx)
            rel = (angle_to_target - robot.yaw + math.pi) % (2.0 * math.pi) - math.pi
            if abs(rel) > 0.52 * fov:
                continue
            if not self.has_line_of_sight(robot.x, robot.y, hx, hy):
                continue

            cx = int(width * (0.5 + rel / fov))
            cy = int(horizon + max(-8, min(18, int(10.0 / max(0.7, dist)))))
            scale = int(np.clip(48.0 / max(0.9, dist * 0.48), 14, 36))
            cx = int(np.clip(cx, scale + 2, width - scale - 2))
            cy = int(np.clip(cy, scale + 2, height - scale - 2))

            from .vision_ai import draw_realistic_hazard_pattern

            draw_realistic_hazard_pattern(
                img, room.true_hazard_class, cx, cy, scale, self.rng
            )

            visible_bbox = (
                max(2, cx - scale),
                max(2, cy - scale),
                min(width - 2, cx + scale),
                min(height - 2, cy + scale),
            )

        noise = self.rng.normal(0.0, 0.012, size=img.shape).astype(np.float32)
        img = np.clip(img + noise, 0.0, 1.0)
        robot.last_camera_rgb = img
        robot.last_vision_bbox = visible_bbox
        return img

    def update_all_sensors(self) -> None:
        render_cam = (self.step_count == 0) or (self.step_count % 3 == 0)
        for robot in self.robots.values():
            if robot.operational and robot.battery > 0.0:
                self.cast_lidar_for_robot(robot)
                if render_cam or robot.last_camera_rgb is None:
                    self.render_onboard_camera_rgb(robot)

    def step_physics(
        self,
        control_commands: dict[str, tuple[float, float]],
        dt: float = 0.05,
    ) -> None:
        self.sim_time = round(self.sim_time + dt, 4)
        self.step_count += 1

        for agent_id, robot in self.robots.items():
            if not robot.operational or robot.battery <= 0.0:
                robot.vx = 0.0
                robot.vy = 0.0
                robot.omega = 0.0
                continue

            cmd_v, cmd_w = control_commands.get(agent_id, (0.0, 0.0))
            cmd_v = float(np.clip(cmd_v, -1.0, robot.max_speed))
            cmd_w = float(np.clip(cmd_w, -robot.max_omega, robot.max_omega))

            alpha_v = min(1.0, dt * 8.5)
            alpha_w = min(1.0, dt * 10.0)
            current_speed = math.hypot(robot.vx, robot.vy)
            new_speed = current_speed + alpha_v * (cmd_v - current_speed)
            robot.omega = robot.omega + alpha_w * (cmd_w - robot.omega)
            robot.yaw = (robot.yaw + robot.omega * dt + math.pi) % (2.0 * math.pi) - math.pi

            robot.vx = new_speed * math.cos(robot.yaw)
            robot.vy = new_speed * math.sin(robot.yaw)
            robot.z = 1.65 if robot.vehicle_mode == "drone" else 0.25

            old_x, old_y = robot.x, robot.y
            robot.x += robot.vx * dt
            robot.y += robot.vy * dt

            for wall in self.walls:
                cx, cy, dist = point_to_segment_projection(
                    robot.x, robot.y, wall.x1, wall.y1, wall.x2, wall.y2
                )
                min_clear = robot.radius + wall.thickness * 0.35
                if dist < min_clear:
                    overlap = min_clear - dist
                    if dist > 1e-6:
                        nx = (robot.x - cx) / dist
                        ny = (robot.y - cy) / dist
                    else:
                        nx, ny = 0.0, 1.0
                    robot.x += nx * overlap
                    robot.y += ny * overlap

            for obs in self.obstacles:
                dx = robot.x - obs.x
                dy = robot.y - obs.y
                dist = math.hypot(dx, dy)
                min_d = robot.radius + obs.radius
                if dist < min_d and dist > 1e-6:
                    overlap = min_d - dist
                    robot.x += (dx / dist) * overlap
                    robot.y += (dy / dist) * overlap

            robot.x = float(np.clip(robot.x, robot.radius, self.WIDTH - robot.radius))
            robot.y = float(np.clip(robot.y, robot.radius, self.HEIGHT - robot.radius))

            step_dist = math.hypot(robot.x - old_x, robot.y - old_y)
            robot.odometry_distance += step_dist

            base_drain = 0.0004 * dt
            motion_drain = 0.0012 * step_dist + 0.00015 * abs(robot.omega) * dt
            robot.battery = max(0.0, round(robot.battery - (base_drain + motion_drain), 5))
            if robot.battery <= 0.0:
                robot.operational = False
                robot.status_phase = "POWER_DEPLETED"

        # Resolve inter-UGV collision separation
        robot_list = list(self.robots.values())
        for i in range(len(robot_list)):
            for j in range(i + 1, len(robot_list)):
                r1, r2 = robot_list[i], robot_list[j]
                dx = r2.x - r1.x
                dy = r2.y - r1.y
                dist = math.hypot(dx, dy)
                min_d = r1.radius + r2.radius + 0.15
                if dist < min_d and dist > 1e-6:
                    push = 0.5 * (min_d - dist)
                    nx, ny = dx / dist, dy / dist
                    r1.x -= nx * push
                    r1.y -= ny * push
                    r2.x += nx * push
                    r2.y += ny * push

        self.update_all_sensors()
