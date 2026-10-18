"""Generate the facility world, the public blueprint and the supervisor-only placement slots.

Outputs:
    worlds/facility.wbt          Webots world (R2025a PROTOs)
    config/blueprint.json        floor plan that agents may load (rooms, doors, bounds)
    truth/slots.json             candidate hazard/victim positions (supervisor only)

Run:  python tools/build_world.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from swarm.motion import (PROXIMITY_ANGLES, PROXIMITY_HEIGHT, PROXIMITY_MOUNT_RADIUS,
                          PROXIMITY_RANGE)
URL = "https://raw.githubusercontent.com/cyberbotics/webots/R2025a/projects"

PROTOS = {
    "TexturedBackground": "objects/backgrounds/protos/TexturedBackground.proto",
    "Pioneer3dx": "robots/adept/pioneer3/protos/Pioneer3dx.proto",
    "Pedestrian": "humans/pedestrian/protos/Pedestrian.proto",
    "Wall": "objects/apartment_structure/protos/Wall.proto",
    "Roughcast": "appearances/protos/Roughcast.proto",
    "Parquetry": "appearances/protos/Parquetry.proto",
    "Asphalt": "appearances/protos/Asphalt.proto",
    "CementTiles": "appearances/protos/CementTiles.proto",
    "ThreadMetalPlate": "appearances/protos/ThreadMetalPlate.proto",
    "GlossyPaint": "appearances/protos/GlossyPaint.proto",
    "Table": "objects/tables/protos/Table.proto",
    "Chair": "objects/chairs/protos/Chair.proto",
    "Cabinet": "objects/cabinet/protos/Cabinet.proto",
    "Monitor": "objects/computers/protos/Monitor.proto",
    "WoodenPalletStack": "objects/factory/pallet/protos/WoodenPalletStack.proto",
    "MetalStorageBox": "objects/factory/containers/protos/MetalStorageBox.proto",
    "PlasticCrate": "objects/factory/containers/protos/PlasticCrate.proto",
    "CardboardBox": "objects/factory/containers/protos/CardboardBox.proto",
    "WoodenBox": "objects/factory/containers/protos/WoodenBox.proto",
    "OilBarrel": "objects/obstacles/protos/OilBarrel.proto",
    "GasCanister": "objects/factory/gas_canister/protos/GasCanister.proto",
    "PipeSection": "objects/factory/pipes/protos/PipeSection.proto",
    "TrafficCone": "objects/traffic/protos/TrafficCone.proto",
    "FireExtinguisher": "objects/factory/fire_extinguisher/protos/FireExtinguisher.proto",
}

WALL_T = 0.2
WALL_H = 2.0
HIDE_Z = -20.0

ROOMS = {
    "R1": {"name": "Chemistry Lab", "rect": [-7.0, 1.0, 0.0, 5.0], "floor": "CementTiles"},
    "R2": {"name": "Storage Hall", "rect": [0.0, 1.0, 7.0, 5.0], "floor": "ThreadMetalPlate"},
    "R3": {"name": "Control Office", "rect": [-7.0, -5.0, 0.0, -1.0], "floor": "Parquetry"},
    "R4": {"name": "Plant Room", "rect": [0.0, -5.0, 7.0, -1.0], "floor": "ThreadMetalPlate"},
}
DOORS = {
    "D1": {"between": ["CORRIDOR", "R1"], "pos": [-3.5, 1.0], "width": 1.2, "wall": "h"},
    "D2": {"between": ["CORRIDOR", "R2"], "pos": [3.5, 1.0], "width": 1.2, "wall": "h"},
    "D3": {"between": ["CORRIDOR", "R3"], "pos": [-3.5, -1.0], "width": 1.2, "wall": "h"},
    "D4": {"between": ["CORRIDOR", "R4"], "pos": [3.5, -1.0], "width": 1.2, "wall": "h"},
    "D12": {"between": ["R1", "R2"], "pos": [0.0, 3.0], "width": 1.1, "wall": "v"},
    "D34": {"between": ["R3", "R4"], "pos": [0.0, -3.0], "width": 1.1, "wall": "v"},
}
CORRIDOR = [-7.0, -1.0, 7.0, 1.0]
ENTRANCE = {"pos": [-7.0, 0.0], "width": 2.0}
COMMANDER_POS = [-9.6, 2.4]
SPAWNS = [[-6.3, 0.45], [-6.3, -0.45], [-5.6, 0.45], [-5.6, -0.45], [-8.2, 0.4], [-8.2, -0.4]]
MAP_BOUNDS = [-11.0, -6.0, 8.0, 6.0]

# static furniture footprints (x0, y0, x1, y1) used to keep random placements clear
FURNITURE_RECTS: list[list[float]] = []


# ---------------------------------------------------------------- helpers
def f(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".") if abs(v) > 1e-9 else "0"


def vec(*vals: float) -> str:
    return " ".join(f(v) for v in vals)


def rot_z(yaw: float) -> str:
    return f"0 0 1 {f(yaw)}"


def axis_angle(yaw: float, pitch: float) -> str:
    """Rotation Rz(yaw) * Ry(pitch) as Webots axis-angle (positive pitch tilts +x downward)."""
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    m = [
        [cy * cp, -sy, cy * sp],
        [sy * cp, cy, sy * sp],
        [-sp, 0.0, cp],
    ]
    angle = math.acos(max(-1.0, min(1.0, (m[0][0] + m[1][1] + m[2][2] - 1) / 2)))
    if angle < 1e-9:
        return "0 0 1 0"
    x = m[2][1] - m[1][2]
    y = m[0][2] - m[2][0]
    z = m[1][0] - m[0][1]
    n = math.sqrt(x * x + y * y + z * z)
    return f"{f(x / n)} {f(y / n)} {f(z / n)} {f(angle)}"


def wall(name: str, cx: float, cy: float, sx: float, sy: float, color: str = "1 1 1") -> str:
    return (
        f'Wall {{\n  translation {vec(cx, cy, 0)}\n  name "{name}"\n'
        f"  size {vec(sx, sy, WALL_H)}\n"
        f"  appearance Roughcast {{ colorOverride {color} textureTransform TextureTransform {{ scale {f(max(sx, sy) / 2)} 1 }} }}\n}}\n"
    )


def segments(start: float, end: float, gaps: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out, cur = [], start
    for g0, g1 in sorted(gaps):
        if g0 > cur:
            out.append((cur, g0))
        cur = max(cur, g1)
    if cur < end:
        out.append((cur, end))
    return out


# ---------------------------------------------------------------- building
def building_walls() -> str:
    s = []
    x0, y0, x1, y1 = -7.0, -5.0, 7.0, 5.0
    t = WALL_T
    s.append(wall("outer_south", 0, y0, x1 - x0 + t, t))
    s.append(wall("outer_north", 0, y1, x1 - x0 + t, t))
    s.append(wall("outer_east", x1, 0, t, y1 - y0))
    eg = ENTRANCE["width"] / 2
    for i, (a, b) in enumerate(segments(y0, y1, [(-eg, eg)])):
        s.append(wall(f"outer_west_{i}", x0, (a + b) / 2, t, b - a))
    for yline, tag in ((1.0, "n"), (-1.0, "s")):
        gaps = [(d["pos"][0] - d["width"] / 2, d["pos"][0] + d["width"] / 2)
                for d in DOORS.values() if d["wall"] == "h" and d["pos"][1] == yline]
        for i, (a, b) in enumerate(segments(x0, x1, gaps)):
            s.append(wall(f"corridor_{tag}_{i}", (a + b) / 2, yline, b - a, t, "0.93 0.93 0.9"))
    for ya, yb, tag in ((1.0, 5.0, "n"), (-5.0, -1.0, "s")):
        gaps = [(d["pos"][1] - d["width"] / 2, d["pos"][1] + d["width"] / 2)
                for d in DOORS.values() if d["wall"] == "v" and ya < d["pos"][1] < yb]
        for i, (a, b) in enumerate(segments(ya, yb, gaps)):
            s.append(wall(f"partition_{tag}_{i}", 0, (a + b) / 2, t, b - a, "0.95 0.92 0.85"))
    return "".join(s)


def floors() -> str:
    s = [
        "DEF GROUND Solid {\n  children [\n    Shape {\n      appearance Asphalt { textureTransform TextureTransform { scale 12 12 } }\n"
        "      geometry Plane { size 34 26 }\n    }\n  ]\n  name \"ground\"\n  boundingObject Plane { size 34 26 }\n  locked TRUE\n}\n"
    ]

    def tile(name: str, rect: list[float], appearance: str) -> str:
        x0, y0, x1, y1 = rect
        return (
            f"Pose {{\n  translation {vec((x0 + x1) / 2, (y0 + y1) / 2, 0.004)}\n  children [\n    Shape {{\n"
            f"      appearance {appearance}\n      geometry Plane {{ size {vec(x1 - x0, y1 - y0)} }}\n    }}\n  ]\n}}\n"
        )

    s.append(tile("corridor", CORRIDOR, "CementTiles { textureTransform TextureTransform { scale 7 1 } }"))
    for rid, r in ROOMS.items():
        kind = r["floor"]
        if kind == "Parquetry":
            app = 'Parquetry { type "light strip" textureTransform TextureTransform { scale 3.5 2 } }'
        elif kind == "ThreadMetalPlate":
            app = "ThreadMetalPlate { textureTransform TextureTransform { scale 3.5 2 } }"
        else:
            app = "CementTiles { colorOverride 0.92 0.95 1 textureTransform TextureTransform { scale 3.5 2 } }"
        s.append(tile(rid, r["rect"], app))
    # hazard-yellow entrance strip and parking bay for the command post
    s.append(
        "Pose {\n  translation -8.2 0 0.006\n  children [\n    Shape {\n"
        "      appearance PBRAppearance { baseColor 0.95 0.75 0.05 roughness 0.8 metalness 0 }\n"
        "      geometry Plane { size 0.25 2.4 }\n    }\n  ]\n}\n"
    )
    return "".join(s)


def furn(rect: list[float]) -> None:
    FURNITURE_RECTS.append(rect)


def furniture() -> str:
    s = []
    # R1 Chemistry Lab: lab benches on the north wall, cabinet on the west wall
    for i, x in enumerate((-5.6, -2.4)):
        s.append(f'Table {{\n  translation {vec(x, 4.45, 0)}\n  rotation {rot_z(math.pi / 2)}\n  name "lab bench {i}"\n  size 0.8 1.8 0.9\n}}\n')
        furn([x - 0.95, 4.0, x + 0.95, 4.9])
    s.append(f'Cabinet {{\n  translation {vec(-6.85, 2.6, 0)}\n  name "lab cabinet"\n}}\n')
    furn([-6.9, 2.1, -6.3, 3.1])
    s.append(f'FireExtinguisher {{\n  translation {vec(-0.35, 1.3, 0)}\n  name "lab extinguisher"\n  enablePhysics FALSE\n}}\n')
    furn([-0.6, 1.1, -0.1, 1.6])
    # R2 Storage Hall: pallet stacks and storage boxes
    s.append(f'WoodenPalletStack {{\n  translation {vec(6.2, 4.3, 0)}\n  name "pallets 0"\n  palletNumber 7\n}}\n')
    furn([5.5, 3.8, 6.9, 4.9])
    s.append(f'WoodenPalletStack {{\n  translation {vec(6.2, 2.0, 0)}\n  name "pallets 1"\n  palletNumber 4\n}}\n')
    furn([5.5, 1.5, 6.9, 2.5])
    s.append(f'MetalStorageBox {{\n  translation {vec(1.6, 4.55, 0)}\n  name "storage box"\n}}\n')
    furn([1.0, 4.1, 2.3, 4.9])
    for i, x in enumerate((3.0, 3.65)):
        s.append(f'WoodenBox {{\n  translation {vec(x, 4.6, 0.3)}\n  name "crate {i}"\n}}\n')
    furn([2.6, 4.2, 4.1, 4.9])
    # R3 Control Office: desks with monitors, chairs, cabinet
    for i, x in enumerate((-5.6, -2.4)):
        s.append(f'Table {{\n  translation {vec(x, -4.45, 0)}\n  rotation {rot_z(math.pi / 2)}\n  name "desk {i}"\n  size 0.8 1.6 0.74\n}}\n')
        s.append(f'Monitor {{\n  translation {vec(x, -4.55, 0.74)}\n  rotation {rot_z(math.pi / 2)}\n  name "monitor {i}"\n}}\n')
        s.append(f'Chair {{\n  translation {vec(x, -3.75, 0)}\n  rotation {rot_z(-math.pi / 2)}\n  name "chair {i}"\n  color 0.2 0.25 0.35\n}}\n')
        furn([x - 0.85, -4.9, x + 0.85, -3.4])
    s.append(f'Cabinet {{\n  translation {vec(-6.85, -2.4, 0)}\n  name "office cabinet"\n}}\n')
    furn([-6.9, -2.9, -6.3, -1.9])
    # R4 Plant Room: vertical pipes, oil barrels, storage box
    for i, (x, y) in enumerate(((6.55, -4.55), (6.2, -4.55), (6.55, -4.2))):
        s.append(f'PipeSection {{\n  translation {vec(x, y, 1.0)}\n  name "riser {i}"\n  height 2.0\n  radius 0.09\n}}\n')
    furn([5.9, -4.9, 6.9, -3.9])
    for i, (x, y) in enumerate(((6.4, -1.6), (5.7, -1.6))):
        s.append(f'OilBarrel {{\n  translation {vec(x, y, 0.44)}\n  name "oil drum {i}"\n}}\n')
    furn([5.2, -2.1, 6.9, -1.1])
    s.append(f'MetalStorageBox {{\n  translation {vec(2.0, -4.55, 0)}\n  name "tool box"\n}}\n')
    furn([1.4, -4.9, 2.7, -4.1])
    # outside: cones around the command post
    for i, (x, y) in enumerate(((-8.4, 3.6), (-10.8, 3.6), (-8.4, 1.2), (-10.8, 1.2))):
        s.append(f'TrafficCone {{\n  translation {vec(x, y, 0)}\n  name "site cone {i}"\n}}\n')
    return "".join(s)


# ---------------------------------------------------------------- hazard pools (hidden underground)
def fire_node(i: int) -> str:
    flames = []
    for j, (dx, dy, h, r, col) in enumerate((
        (0.0, 0.0, 0.85, 0.22, "1 0.45 0.05"),
        (0.12, 0.08, 0.6, 0.16, "1 0.7 0.1"),
        (-0.12, 0.05, 0.55, 0.15, "1 0.3 0.02"),
        (0.04, -0.13, 0.65, 0.14, "1 0.8 0.2"),
        (-0.05, -0.05, 0.4, 0.1, "1 0.95 0.6"),
    )):
        flames.append(
            f"      Pose {{\n        translation {vec(dx, dy, 0.3 + h / 2)}\n        children [\n          Shape {{\n"
            f"            appearance PBRAppearance {{ baseColor {col} emissiveColor {col} emissiveIntensity 2 transparency 0.15 roughness 1 metalness 0 }}\n"
            f"            geometry Cone {{ bottomRadius {f(r)} height {f(h)} }}\n          }}\n        ]\n      }}\n"
        )
    smoke = []
    for j, (dx, dy, z, r) in enumerate(((0.05, 0.0, 1.35, 0.35), (-0.15, 0.1, 1.7, 0.45), (0.2, -0.1, 1.95, 0.4))):
        smoke.append(
            f"      Pose {{\n        translation {vec(dx, dy, z)}\n        children [\n          Shape {{\n"
            f"            appearance PBRAppearance {{ baseColor 0.25 0.25 0.25 transparency 0.55 roughness 1 metalness 0 }}\n"
            f"            geometry Sphere {{ radius {f(r)} subdivision 2 }}\n          }}\n        ]\n      }}\n"
        )
    # labelled child Solid has no bounding object, so recognition boxes cover base + flames
    core = (
        "    Solid {\n      children [\n"
        "    Pose {\n      translation 0 0 0.12\n      children [\n        Shape {\n"
        "          appearance PBRAppearance { baseColor 0.08 0.06 0.05 roughness 1 metalness 0 }\n"
        "          geometry Box { size 0.7 0.55 0.24 }\n        }\n      ]\n    }\n"
        "    Pose {\n      translation 0.05 0.02 0.27\n      rotation 0 0 1 0.5\n      children [\n        Shape {\n"
        "          appearance PBRAppearance { baseColor 0.15 0.08 0.04 roughness 1 metalness 0 }\n"
        "          geometry Box { size 0.6 0.12 0.08 }\n        }\n      ]\n    }\n"
        + "".join(flames) +
        f"      ]\n      name \"flames {i}\"\n      model \"fire\"\n      recognitionColors [ 1 0.45 0.05 ]\n    }}\n"
    )
    return (
        f"DEF FIRE_{i} Solid {{\n  translation {vec(30 + i * 3, 20, HIDE_Z)}\n  children [\n"
        f"    DEF FIRE_{i}_LIGHT PointLight {{\n      attenuation 0 0 1\n      color 1 0.55 0.15\n      intensity 3.5\n"
        f"      location 0 0 0.75\n      radius 9\n      on FALSE\n    }}\n"
        + core + "".join(smoke) +
        f"  ]\n  name \"fire {i}\"\n"
        "  boundingObject Pose { translation 0 0 0.12 children [ Box { size 0.7 0.55 0.24 } ] }\n}\n"
    )


def gas_node(i: int) -> str:
    vapor = []
    for j, (dx, dy, z, r) in enumerate(((0.35, 0.1, 0.25, 0.32), (0.55, -0.15, 0.2, 0.26), (0.2, -0.3, 0.35, 0.28), (0.45, 0.35, 0.45, 0.22))):
        vapor.append(
            f"    Pose {{\n      translation {vec(dx, dy, z)}\n      children [\n        Shape {{\n"
            f"          appearance PBRAppearance {{ baseColor 0.55 0.9 0.35 emissiveColor 0.25 0.45 0.1 transparency 0.62 roughness 1 metalness 0 }}\n"
            f"          geometry Sphere {{ radius {f(r)} subdivision 2 }}\n        }}\n      ]\n    }}\n"
        )
    return (
        f"DEF GAS_{i} Solid {{\n  translation {vec(30 + i * 3, 24, HIDE_Z)}\n  children [\n"
        "    GasCanister {\n      name \"leaking cylinder\"\n      enablePhysics FALSE\n    }\n"
        "    Pose {\n      translation -0.32 0.12 0\n      children [\n        GasCanister {\n          name \"cylinder 2\"\n          enablePhysics FALSE\n        }\n      ]\n    }\n"
        "    Pose {\n      translation 0.19 0 1.05\n      rotation 0.5774 0.5774 0.5774 2.094\n      children [\n        Shape {\n"
        "          appearance PBRAppearance { baseColor 1 0.85 0 roughness 0.6 metalness 0 }\n"
        "          geometry Box { size 0.26 0.26 0.01 }\n        }\n      ]\n    }\n"
        "    Pose {\n      translation 0.195 0 1.05\n      rotation 0.5774 0.5774 0.5774 2.094\n      children [\n        Shape {\n"
        "          appearance PBRAppearance { baseColor 0.05 0.05 0.05 roughness 0.6 metalness 0 }\n"
        "          geometry Box { size 0.12 0.12 0.012 }\n        }\n      ]\n    }\n"
        + "".join(vapor) +
        f"  ]\n  name \"gas leak {i}\"\n  model \"gas_leak\"\n  recognitionColors [ 0.55 0.9 0.35 ]\n"
        "  boundingObject Pose { translation -0.15 0.06 0.75 children [ Box { size 0.75 0.5 1.5 } ] }\n}\n"
    )


def victim_node(i: int) -> str:
    shirts = ["0.85 0.2 0.15", "0.15 0.35 0.8", "0.9 0.85 0.2", "0.25 0.6 0.3"]
    pants = ["0.2 0.2 0.25", "0.35 0.3 0.2", "0.1 0.1 0.12", "0.55 0.5 0.45"]
    return (
        f"DEF VICTIM_{i} Pedestrian {{\n  translation {vec(30 + i * 3, 28, HIDE_Z)}\n  name \"victim {i}\"\n"
        f"  controller \"<none>\"\n  shirtColor {shirts[i % 4]}\n  pantsColor {pants[i % 4]}\n  enableBoundingObject TRUE\n}}\n"
    )


def debris_node(i: int) -> str:
    boxes = []
    for j, (dx, dy, dz, kind, sz, yaw) in enumerate((
        (0.0, -0.4, 0.3, "CardboardBox", 0.6, 0.1),
        (0.05, 0.25, 0.3, "WoodenBox", 0.6, -0.2),
        (-0.05, -0.1, 0.85, "CardboardBox", 0.5, 0.4),
        (0.0, 0.55, 0.2, "CardboardBox", 0.4, 0.3),
    )):
        boxes.append(
            f"    {kind} {{\n      translation {vec(dx, dy, dz)}\n      rotation {rot_z(yaw)}\n      name \"debris {i} box {j}\"\n"
            f"      size {vec(sz, sz, sz)}\n    }}\n"
        )
    return (
        f"DEF DEBRIS_{i} Solid {{\n  translation {vec(30 + i * 3, 32, HIDE_Z)}\n  children [\n" + "".join(boxes) +
        f"  ]\n  name \"debris {i}\"\n  model \"debris\"\n  recognitionColors [ 0.6 0.45 0.3 ]\n}}\n"
    )


def distractor_nodes() -> str:
    s = []
    s.append(f'DEF DISTRACT_0 PlasticCrate {{\n  translation {vec(30, 36, HIDE_Z)}\n  name "red crate"\n  color 0.85 0.15 0.05\n}}\n')
    s.append(f'DEF DISTRACT_1 TrafficCone {{\n  translation {vec(33, 36, HIDE_Z)}\n  name "orange cone"\n}}\n')
    s.append(f'DEF DISTRACT_2 GasCanister {{\n  translation {vec(36, 36, HIDE_Z)}\n  name "intact cylinder"\n  enablePhysics FALSE\n}}\n')
    s.append(f'DEF DISTRACT_3 OilBarrel {{\n  translation {vec(39, 36, HIDE_Z)}\n  name "spare drum"\n}}\n')
    return "".join(s)


# ---------------------------------------------------------------- robots
def lidar_block() -> str:
    return (
        "    Pose {\n      translation 0 0 0.215\n      children [\n        Shape {\n"
        "          appearance PBRAppearance { baseColor 0.15 0.15 0.17 roughness 0.5 metalness 0.6 }\n"
        "          geometry Cylinder { height 0.13 radius 0.03 }\n        }\n      ]\n    }\n"
        "    Lidar {\n      translation 0 0 0.3\n      children [\n        Pose {\n          translation 0 0 -0.02\n          children [\n            Shape {\n"
        "              appearance PBRAppearance { baseColor 0.05 0.05 0.06 roughness 0.3 metalness 0.2 }\n"
        "              geometry Cylinder { height 0.035 radius 0.05 }\n            }\n          ]\n        }\n      ]\n"
        "      name \"lidar\"\n      horizontalResolution 360\n      fieldOfView 6.2832\n      verticalFieldOfView 0.05\n"
        "      numberOfLayers 1\n      minRange 0.12\n      maxRange 8\n      noise 0.004\n    }\n"
    )


def common_devices(color: str) -> str:
    return (
        lidar_block() + proximity_devices() +
        "    Pose {\n      translation 0.02 0 0.158\n      children [\n        Shape {\n"
        f"          appearance PBRAppearance {{ baseColor {color} roughness 0.4 metalness 0 }}\n"
        "          geometry Box { size 0.3 0.24 0.012 }\n        }\n      ]\n    }\n"
        "    Camera {\n      translation 0.17 0 0.21\n      children [\n        Pose {\n          translation -0.02 0 0\n          rotation 0 1 0 1.5708\n          children [\n            Shape {\n"
        "              appearance PBRAppearance { baseColor 0.1 0.1 0.1 roughness 0.3 metalness 0.5 }\n"
        "              geometry Cylinder { height 0.04 radius 0.022 }\n            }\n          ]\n        }\n      ]\n"
        "      name \"camera\"\n      fieldOfView 1.2\n      width 320\n      height 240\n      near 0.05\n      far 15\n    }\n"
        "    InertialUnit {\n      name \"imu\"\n      noise 0.002\n    }\n"
        "    GPS {\n      name \"gps\"\n      accuracy 0.03\n    }\n"
        "    Emitter {\n      name \"radio_tx\"\n      range 40\n      channel 1\n      bufferSize -1\n    }\n"
        "    Receiver {\n      name \"radio_rx\"\n      channel 1\n      bufferSize -1\n    }\n"
    )


def proximity_devices() -> str:
    """Low outward range rays; generic sensors measure bounding objects, including low chassis.

    They intentionally have no collision geometry or added mass. Mounts and range
    are explicit simulated hardware, independent of task allocation and scene labels.
    """
    devices = []
    for i, yaw in enumerate(PROXIMITY_ANGLES):
        x, y = PROXIMITY_MOUNT_RADIUS * math.cos(yaw), PROXIMITY_MOUNT_RADIUS * math.sin(yaw)
        devices.append(
            f'    DistanceSensor {{\n      translation {vec(x, y, PROXIMITY_HEIGHT)}\n'
            f'      rotation {rot_z(yaw)}\n      name "proximity_{i:02d}"\n'
            f'      type "generic"\n      lookupTable [ 0 0 0, {PROXIMITY_RANGE} {PROXIMITY_RANGE} 0.002 ]\n'
            '      numberOfRays 1\n    }\n')
    return "".join(devices)


def thermal_devices() -> str:
    s = []
    for name, yaw in (("thermal_front", 0.0), ("thermal_left", math.pi / 2), ("thermal_back", math.pi), ("thermal_right", -math.pi / 2)):
        dx, dy = 0.09 * math.cos(yaw), 0.09 * math.sin(yaw)
        s.append(
            f"    LightSensor {{\n      translation {vec(dx, dy, 0.19)}\n      rotation {axis_angle(yaw, 0.14)}\n"
            "      children [\n        Shape {\n          appearance PBRAppearance { baseColor 0.9 0.3 0.1 roughness 0.4 metalness 0 }\n"
            "          geometry Box { size 0.02 0.03 0.02 }\n        }\n      ]\n"
            f"      name \"{name}\"\n      lookupTable [ 0 0 0, 100 100 0 ]\n      occlusion TRUE\n    }}\n"
        )
    return "".join(s)


def gas_devices() -> str:
    return (
        "    Pose {\n      translation 0.12 0.08 0.2\n      children [\n        Shape {\n"
        "          appearance PBRAppearance { baseColor 0.85 0.85 0.2 roughness 0.4 metalness 0 }\n"
        "          geometry Cylinder { height 0.08 radius 0.018 }\n        }\n      ]\n    }\n"
        "    Receiver {\n      name \"gas_sensor\"\n      channel 7\n      bufferSize -1\n    }\n"
    )


def agent(def_name: str, name: str, pos: list[float], color: str, specialty: str) -> str:
    extra = thermal_devices() if specialty == "thermal" else gas_devices()
    return (
        f"DEF {def_name} Pioneer3dx {{\n  translation {vec(pos[0], pos[1], 0.0975)}\n  name \"{name}\"\n"
        f"  controller \"agent\"\n  controllerArgs []\n  supervisor FALSE\n  extensionSlot [\n"
        + common_devices(color) + extra + "  ]\n}\n"
    )


def commander() -> str:
    x, y = COMMANDER_POS
    return (
        f"DEF COMMANDER Robot {{\n  translation {vec(x, y, 0)}\n  children [\n"
        "    Pose {\n      translation 0 0 0.8\n      children [\n        Shape {\n"
        "          appearance GlossyPaint { baseColor 0.96 0.96 0.96 }\n          geometry Box { size 2.4 1.3 1.1 }\n        }\n      ]\n    }\n"
        "    Pose {\n      translation 0 -0.652 0.75\n      children [\n        Shape {\n"
        "          appearance PBRAppearance { baseColor 0.85 0.1 0.1 roughness 0.5 metalness 0 }\n          geometry Box { size 2.4 0.005 0.16 }\n        }\n      ]\n    }\n"
        "    Pose {\n      translation 0 0.652 0.75\n      children [\n        Shape {\n"
        "          appearance PBRAppearance { baseColor 0.85 0.1 0.1 roughness 0.5 metalness 0 }\n          geometry Box { size 2.4 0.005 0.16 }\n        }\n      ]\n    }\n"
        + "".join(
            f"    Pose {{\n      translation {vec(wx, wy, 0.22)}\n      rotation 1 0 0 1.5708\n      children [\n        Shape {{\n"
            "          appearance PBRAppearance { baseColor 0.05 0.05 0.05 roughness 0.9 metalness 0 }\n"
            "          geometry Cylinder { height 0.18 radius 0.22 }\n        }\n      ]\n    }\n"
            for wx in (-0.8, 0.8) for wy in (-0.62, 0.62)
        ) +
        "    Pose {\n      translation 0.9 0.4 2.35\n      children [\n        Shape {\n"
        "          appearance PBRAppearance { baseColor 0.7 0.7 0.72 roughness 0.3 metalness 0.8 }\n"
        "          geometry Cylinder { height 2.4 radius 0.025 }\n        }\n      ]\n    }\n"
        "    Pose {\n      translation 0.9 0.4 3.6\n      children [\n        Shape {\n"
        "          appearance PBRAppearance { baseColor 0.9 0.1 0.1 emissiveColor 0.9 0.1 0.1 roughness 0.3 metalness 0 }\n"
        "          geometry Sphere { radius 0.06 }\n        }\n      ]\n    }\n"
        "    Emitter {\n      translation 0.9 0.4 3.5\n      name \"radio_tx\"\n      range 40\n      channel 1\n      bufferSize -1\n    }\n"
        "    Receiver {\n      translation 0.9 0.4 3.5\n      name \"radio_rx\"\n      channel 1\n      bufferSize -1\n    }\n"
        "  ]\n  name \"commander\"\n  boundingObject Pose { translation 0 0 0.8 children [ Box { size 2.4 1.3 1.1 } ] }\n"
        "  controller \"commander\"\n  controllerArgs []\n}\n"
    )


def supervisor() -> str:
    return (
        "DEF MISSION_SUPERVISOR Robot {\n  translation 0 0 -30\n  children [\n"
        "    Emitter {\n      name \"gas_bus\"\n      range -1\n      channel 7\n      bufferSize -1\n    }\n"
        "  ]\n  name \"mission_supervisor\"\n  controller \"mission_supervisor\"\n  supervisor TRUE\n}\n"
    )


def viewpoint() -> str:
    eye = (-1.5, -13.0, 16.5)
    target = (-1.5, -0.3, 0.0)
    d = [target[i] - eye[i] for i in range(3)]
    yaw = math.atan2(d[1], d[0])
    pitch = math.atan2(-d[2], math.hypot(d[0], d[1]))
    return f"Viewpoint {{\n  orientation {axis_angle(yaw, pitch)}\n  position {vec(*eye)}\n  near 0.1\n}}\n"


# ---------------------------------------------------------------- placement slots
def free(x: float, y: float, margin: float) -> bool:
    for x0, y0, x1, y1 in FURNITURE_RECTS:
        if x0 - margin <= x <= x1 + margin and y0 - margin <= y <= y1 + margin:
            return False
    for d in DOORS.values():
        if math.hypot(x - d["pos"][0], y - d["pos"][1]) < 1.35:
            return False
    return True


def slots() -> dict:
    out = {}
    for rid, r in ROOMS.items():
        x0, y0, x1, y1 = r["rect"]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        pts = []
        y = y0 + 0.75
        while y <= y1 - 0.6:
            x = x0 + 0.75
            while x <= x1 - 0.6:
                if free(x, y, 0.45) and math.hypot(x - cx, y - cy) > 1.0:
                    pts.append([round(x, 2), round(y, 2)])
                x += 0.8
            y += 0.8
        out[rid] = pts
    return out


def blueprint() -> dict:
    rooms = {}
    for rid, r in ROOMS.items():
        x0, y0, x1, y1 = r["rect"]
        rooms[rid] = {"name": r["name"], "rect": r["rect"], "center": [(x0 + x1) / 2, (y0 + y1) / 2]}
    doors = {}
    for did, d in DOORS.items():
        x, y = d["pos"]
        if d["wall"] == "h":
            a, b = [x, y - 0.8], [x, y + 0.8]
        else:
            a, b = [x - 0.8, y], [x + 0.8, y]
        doors[did] = {**d, "approach": [a, b]}
    return {
        "map_bounds": MAP_BOUNDS,
        "building": [-7.0, -5.0, 7.0, 5.0],
        "corridor": CORRIDOR,
        "entrance": ENTRANCE,
        "rooms": rooms,
        "doors": doors,
        "commander": COMMANDER_POS,
        "note": "Public floor plan given to responders before entry. Contains no hazard, victim or blockage information.",
    }


def capture_rig() -> str:
    return (
        "DEF CAPTURE_RIG Robot {\n  translation 0 0 0.3075\n  children [\n"
        "    Camera {\n      name \"camera\"\n      fieldOfView 1.2\n      width 320\n      height 240\n      near 0.05\n      far 15\n"
        "      recognition Recognition {\n        maxRange 8\n        occlusion 1\n      }\n    }\n  ]\n"
        "  name \"capture_rig\"\n  controller \"dataset_capture\"\n  supervisor TRUE\n}\n"
    )


def prop_pioneer(i: int, pos: list[float]) -> str:
    return (f"DEF PROP_ROBOT_{i} Pioneer3dx {{\n  translation {vec(pos[0], pos[1], 0.0975)}\n  name \"prop robot {i}\"\n"
            "  controller \"<none>\"\n  extensionSlot [\n" + lidar_block() + "  ]\n}\n")


def header(furniture_text: str) -> list[str]:
    parts = ["#VRML_SIM R2025a utf8\n\n"]
    for p in PROTOS.values():
        parts.append(f'EXTERNPROTO "{URL}/{p}"\n')
    parts.append("\nWorldInfo {\n  info [ \"FYP decentralized 2-robot disaster response facility\" ]\n"
                 "  title \"Facility\"\n  basicTimeStep 32\n  contactProperties [ ContactProperties { coulombFriction [ 0.9 ] } ]\n}\n")
    parts.append(viewpoint())
    parts.append('TexturedBackground {\n  texture "noon_park_empty"\n  luminosity 1.2\n}\n')
    parts.append("DEF SUN DirectionalLight {\n  direction 0 0 -1\n  intensity 2.4\n  castShadows FALSE\n}\n")
    parts.append(floors())
    parts.append(building_walls())
    parts.append(furniture_text)
    return parts


def build_capture() -> None:
    FURNITURE_RECTS.clear()
    parts = header(furniture())
    for i in range(4):
        parts.append(fire_node(i))
    for i in range(4):
        parts.append(gas_node(i))
    for i in range(4):
        parts.append(victim_node(i))
    for i in range(3):
        parts.append(debris_node(i))
    parts.append(distractor_nodes())
    parts.append(prop_pioneer(0, [30, 40]))
    parts.append(prop_pioneer(1, [33, 40]))
    parts.append(commander().replace('controller "commander"', 'controller "<none>"'))
    parts.append(capture_rig())
    (ROOT / "worlds" / "dataset_capture.wbt").write_text("".join(parts), encoding="utf-8")
    (ROOT / "truth" / "furniture.json").write_text(json.dumps(FURNITURE_RECTS), encoding="utf-8")
    print("wrote dataset_capture.wbt")


def build() -> None:
    FURNITURE_RECTS.clear()
    parts = header(furniture())
    for i in range(3):
        parts.append(fire_node(i))
    for i in range(3):
        parts.append(gas_node(i))
    for i in range(2):
        parts.append(victim_node(i))
    for i in range(3):
        parts.append(debris_node(i))
    parts.append(distractor_nodes())
    parts.append(agent("AGENT_A", "agent_A", SPAWNS[0], "0.95 0.45 0.1", "thermal"))
    parts.append(agent("AGENT_B", "agent_B", SPAWNS[1], "0.1 0.6 0.65", "gas"))
    parts.append(commander())
    parts.append(supervisor())

    (ROOT / "worlds").mkdir(exist_ok=True)
    (ROOT / "worlds" / "facility.wbt").write_text("".join(parts), encoding="utf-8")
    (ROOT / "config").mkdir(exist_ok=True)
    (ROOT / "config" / "blueprint.json").write_text(json.dumps(blueprint(), indent=2), encoding="utf-8")
    (ROOT / "truth").mkdir(exist_ok=True)
    sl = slots()
    (ROOT / "truth" / "slots.json").write_text(json.dumps({"rooms": sl, "spawns": SPAWNS}, indent=2), encoding="utf-8")
    print("wrote facility.wbt; slots per room:", {k: len(v) for k, v in sl.items()})


if __name__ == "__main__":
    build()
    build_capture()
