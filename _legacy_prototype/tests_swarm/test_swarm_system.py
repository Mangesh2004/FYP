"""End-to-End Verification Suite for the 6-Building Industrial Complex & 2-UGV Decentralized Swarm."""
from __future__ import annotations

import numpy as np

from swarm_sim.navigation_controller import plan_doorway_path, shortest_navigation_distance
from swarm_sim.physics_world import PhysicsArenaWorld
from swarm_sim.server import SwarmSimulationSession
from swarm_sim.vision_ai import (
    HAZARD_CLASSES,
    SwarmAIEngine,
    generate_synthetic_camera_sample,
    render_annotated_hud_jpeg,
)


def test_torchvision_mobilenet_v3_and_annotated_hud() -> None:
    engine = SwarmAIEngine()
    assert engine.training_metadata.get("backbone") == "torchvision.mobilenet_v3_small"
    rng = np.random.default_rng(123)

    correct = 0
    total = 25
    for i in range(total):
        c_idx = i % len(HAZARD_CLASSES)
        img, _sev = generate_synthetic_camera_sample(c_idx, rng, height=128, width=128)
        pred = engine.classify_camera_frame(img, bbox_hint=(32, 32, 96, 96))
        if pred.class_index == c_idx:
            correct += 1
        assert 0.0 <= pred.confidence <= 1.0
        assert 0.0 <= pred.severity <= 1.0

    assert correct / total >= 0.88, f"Expected >=88% MobileNetV3 accuracy, got {correct}/{total}"

    # Verify OpenCV bounding-box HUD renderer produces valid JPEG data URL
    sample_img, _ = generate_synthetic_camera_sample(1, rng, height=128, width=128)
    hud_url = render_annotated_hud_jpeg(sample_img, "fire_hazard", 0.96, (28, 28, 92, 92))
    assert hud_url.startswith("data:image/jpeg;base64,")


def test_6_building_shapely_geometry_and_networkx_astar() -> None:
    world = PhysicsArenaWorld(vehicle_mode="rover", swarm_mode="2_ugv_industrial")
    assert len(world.rooms) == 6
    assert set(world.robots.keys()) == {"UGV-1", "UGV-2"}

    ugv1 = world.robots["UGV-1"]
    assert len(ugv1.lidar_ranges) == 60
    assert ugv1.last_camera_rgb is not None
    assert ugv1.last_camera_rgb.shape == (128, 128, 3)

    # Check NetworkX A* path planning into each of the 6 buildings
    for rid in world.rooms:
        waypoints = plan_doorway_path(world, ugv1.x, ugv1.y, rid)
        dist = shortest_navigation_distance(world, (ugv1.x, ugv1.y), rid)
        assert len(waypoints) >= 2
        assert dist > 3.0


def test_2_ugv_cbba_bundle_queue_and_full_6_building_verification() -> None:
    session = SwarmSimulationSession()
    session.reset(vehicle_mode="rover", swarm_mode="2_ugv_industrial")

    # Verify initial multi-task CBBA bundle queues partition the 6 buildings between UGV-1 and UGV-2
    q1 = session.mesh.brains["UGV-1"].bundle_queue
    q2 = session.mesh.brains["UGV-2"].bundle_queue
    assert "zone-1-boiler" in q1 and "zone-3-offices" in q1
    assert "zone-2-chemlab" in q2 and "zone-5-gasdepot" in q2
    assert set(q1).isdisjoint(set(q2))
    assert len(q1) + len(q2) == 6

    for _ in range(520):
        session.step_once(dt=0.05)
        if all(r.completed_by is not None for r in session.world.rooms.values()):
            break

    snap = session.snapshot()
    assert snap["completed_rooms"] == 6, f"Expected 6/6 buildings verified, got {snap['completed_rooms']}"
    session.mesh.close()


def test_comms_denied_commander_link_severed_autonomy() -> None:
    session = SwarmSimulationSession()
    session.reset(vehicle_mode="rover", swarm_mode="2_ugv_industrial")
    session.toggle_commander_link()
    assert session.mesh.commander_link_active is False

    for _ in range(520):
        session.step_once(dt=0.05)
        if all(r.completed_by is not None for r in session.world.rooms.values()):
            break

    snap = session.snapshot()
    assert snap["commander_link_active"] is False
    assert snap["completed_rooms"] == 6
    session.mesh.close()


def test_single_ugv_power_failure_and_p2p_failover_across_6_buildings() -> None:
    """When UGV-2 loses power early, UGV-1 detects P2P UDP silence and absorbs UGV-2's buildings via backup sensors."""
    session = SwarmSimulationSession()
    session.reset(vehicle_mode="rover", swarm_mode="2_ugv_industrial")

    for step_idx in range(1250):
        if step_idx == 15:
            session.kill_agent("UGV-2")
        session.step_once(dt=0.05)
        if all(r.completed_by is not None for r in session.world.rooms.values()):
            break

    snap = session.snapshot()
    assert snap["completed_rooms"] == 6, f"Surviving UGV-1 should complete all 6 buildings, got {snap['completed_rooms']}"
    assert session.world.rooms["zone-2-chemlab"].completed_by == "UGV-1"
    session.mesh.close()
