import math
import random
import importlib.util
from pathlib import Path
import sys
import types

import numpy as np
import pytest

from swarm.motion import proximity_points, safe_command
from swarm.mapping import OccupancyGrid


def test_low_range_measurement_includes_sensor_mount_offset():
    ranges = np.full(32, 2.0)
    ranges[0] = 0.5
    points = proximity_points(ranges)
    np.testing.assert_allclose(points, [[0.79, 0.0]], atol=1e-6)


@pytest.mark.parametrize("v,w,obstacle", [
    (-0.15, 0.6, [-0.35, 0.0]),  # recovery must not back into a wall
    (0.45, 0.0, [0.45, 0.0]),    # insufficient room to stop
    (0.0, 0.8, [0.0, 0.29]),    # spinning still sweeps the chassis
])
def test_every_motion_branch_stops_before_a_predicted_contact(v, w, obstacle):
    assert safe_command(v, w, np.array([obstacle])) == (0.0, 0.0)


def test_motion_away_from_a_close_obstacle_is_allowed():
    assert safe_command(0.15, 0.0, np.array([[-0.35, 0.0]])) == (0.15, 0.0)


def test_motion_can_increase_clearance_when_already_inside_extra_margin():
    assert safe_command(0.15, 0.0, np.array([[-0.33, 0.0]])) == (0.15, 0.0)


def test_slow_straight_escape_increases_gap_from_close_side_obstacle():
    points=np.array([[-.035,-.237],[-.093,-.36]])
    assert safe_command(.08,0,points)==(.08,0)
    assert safe_command(.08,.5,points)==(0.0,0.0)
    assert safe_command(.08,0,np.array([[.237,0]]))==(0.0,0.0)


def test_enclosing_disk_can_turn_without_reducing_close_wall_clearance():
    assert safe_command(0.0, 0.8, np.array([[0.0, -0.33]])) == (0.0, 0.8)


def test_free_space_keeps_requested_motion():
    assert safe_command(0.3, 0.4, np.zeros((0, 2))) == (0.3, 0.4)


def test_nonfinite_command_or_sensor_points_fail_closed():
    assert safe_command(math.nan, 0, np.zeros((0, 2))) == (0.0, 0.0)
    assert safe_command(0.3, 0, np.array([[math.nan, 0]])) == (0.0, 0.0)


def test_invalid_low_range_measurements_are_rejected():
    with pytest.raises(ValueError):
        proximity_points([float("nan")] * 32)


@pytest.fixture
def agent(monkeypatch):
    # Only the external Webots import and motor I/O need stand-ins. The real
    # Agent.drive/nav_step, geometry, obstacle conversion and guard all execute.
    controller = types.ModuleType("controller")
    controller.Robot = object
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, "controller", controller)
        path = Path(__file__).resolve().parents[1] / "controllers/agent/agent.py"
        spec = importlib.util.spec_from_file_location("motion_test_agent", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    a = module.Agent.__new__(module.Agent)
    class Wheel:
        velocity = None
        def setVelocity(self, value):
            self.velocity = value
    a.lm, a.rm = Wheel(), Wheel()
    a.robot = types.SimpleNamespace(getTime=lambda: 10.0)
    a.motion_scan_t, a.tilt, a.safety_stops = 10.0, 0.0, 0
    a.motion_ranges = np.full(360, np.inf)
    a.angles = np.linspace(math.pi, -math.pi, 360, endpoint=False)
    a.low_points = np.zeros((0, 2))
    a.pose = [0, 0, 0]
    a.goal = [2.0, 0.0]
    a.dt = .032
    a.grid = OccupancyGrid([-2, -2, 2, 2])
    return a


@pytest.mark.parametrize("scan_time,tilt", [(9.0, 0), (10.0, 0.3), (10.0, math.nan)])
def test_agent_stops_both_wheels_on_stale_scan_or_excessive_tilt(agent, scan_time, tilt):
    agent.motion_scan_t, agent.tilt = scan_time, tilt
    agent.drive(0.3, 0.5)
    assert (agent.lm.velocity, agent.rm.velocity) == (0.0, 0.0)


def test_agent_recovery_cannot_bypass_the_motor_guard(agent):
    agent.low_points = np.array([[-0.35, 0]])
    agent.recover_until = 11.0
    assert agent.nav_step(agent.motion_ranges) == "moving"
    assert (agent.lm.velocity, agent.rm.velocity) == (0.0, 0.0)


def test_agent_drives_with_fresh_clear_sensors(agent):
    agent.drive(0.3, 0.0)
    assert agent.lm.velocity == pytest.approx(0.3 / 0.0975)
    assert agent.rm.velocity == pytest.approx(0.3 / 0.0975)


@pytest.mark.parametrize("rpy", [(math.nan, 0, 0), (0, math.nan, 0), (0, 0, math.nan), (math.inf, 0, 0)])
def test_invalid_imu_sample_reaches_the_motor_stop(agent, rpy):
    agent.le = agent.re = types.SimpleNamespace(getValue=lambda: 0.0)
    agent.imu = types.SimpleNamespace(getRollPitchYaw=lambda: rpy)
    agent.gps = types.SimpleNamespace(getValues=lambda: [0.0, 0.0, 0.095])
    agent.enc, agent.odom_dist, agent.noise = (0.0, 0.0), 0.0, 0.0
    agent.rng = random.Random(0)
    agent.dt = 0.032
    agent.update_pose()
    agent.drive(0.3, 0)
    assert (agent.lm.velocity, agent.rm.velocity) == (0.0, 0.0)
    assert all(math.isfinite(v) for v in agent.pose)


def test_reaching_only_a_planner_fallback_is_a_failed_attempt_not_a_finished_goal(agent):
    from swarm.planning import DWA
    agent.recover_until,agent.last_plan_t = -1,-1e9
    agent.path,agent.job,agent.wp = None,{},0
    agent.planner=types.SimpleNamespace(plan=lambda start,goal:[[0.0,0.0]])
    agent.dwa=DWA()
    agent.progress_ref=(10,None)
    assert agent._nav_step(agent.motion_ranges,[1.0,0.0])=='failed'


def test_exhausted_traffic_targets_report_failure_instead_of_waiting_forever(agent):
    from swarm.traffic import DoorwayTraffic
    agent.traffic=DoorwayTraffic('A',{},['A'])
    agent.traffic.mission_goal=tuple(agent.goal)
    for i in range(4):
        agent.traffic.reject_target([i,1])
    agent.state,agent.job,agent.path,agent.wp='INSPECT',{},None,0
    agent.nav_target,agent.recover_until=None,-1
    agent.radio=types.SimpleNamespace(send=lambda msg:None)
    agent.log=lambda *args,**kwargs:None
    agent.last_traffic_t=-1e9
    assert agent.nav_step(agent.motion_ranges)=='failed'
    assert agent.lm.velocity==agent.rm.velocity==0
