"""Regression cases derived from missed confirmations in physical mission logs."""
import math
import types

import numpy as np
import pytest

from tests.test_motion_safety import agent
from swarm.blueprint import Blueprint
from swarm.mapping import OccupancyGrid
from swarm.planning import GlobalPlanner, DWA
from swarm.tasks import TaskBook
from swarm.fusion import EvidenceTracker


@pytest.fixture
def verifier(agent):
    agent.bp=Blueprint()
    agent.grid=OccupancyGrid(agent.bp.bounds)
    agent.planner=GlobalPlanner(agent.grid)
    agent.dwa=DWA()
    agent.book=TaskBook(agent.bp)
    agent.capability='gas'
    agent.name='B'
    agent.alloc=types.SimpleNamespace()
    agent.log=lambda *args,**kwargs:None
    agent.recover_until=-1
    return agent


def test_verification_approach_stays_in_hazard_room_with_wall_clearance(verifier):
    a=verifier
    a.pose=[-4.7,0,0]
    tid=a.book.add_verify('gas',[.83,2.33],'R2','A',1)
    a.start_task(tid)
    assert .4<a.goal[0]<6.6
    assert 1.4<a.goal[1]<4.6
    assert 1.8-1e-6<=math.dist(a.goal,[.83,2.33])<2.6


def test_nearby_hazard_across_wall_does_not_shortcut_navigation(verifier):
    a=verifier
    a.pose=[-4.6,.42,2.25]  # observed corridor pose; cylinder is behind the north wall
    tid=a.book.add_verify('gas',[-5.51,1.83],'R1','A',1)
    a.start_task(tid)
    a.verify_step(a.motion_ranges)
    assert a.job['phase']=='goto'


def test_strong_sensor_without_camera_match_is_not_permanently_rejected(verifier):
    a=verifier
    a.pose=[1,3,0]
    tid=a.book.add_verify('gas',[1.55,2.55],'R2','A',1)
    a.start_task(tid)
    a.job.update(phase='measure',samples=[487.0]*32,visual_hits=0)
    a.decide_verification()
    assert tid not in a.book.done
    assert not any(f.get('rejected') for f in a.book.findings.values())


def test_one_outlying_sensor_sample_cannot_confirm_hazard(verifier):
    a=verifier
    a.pose=[1,3,0]
    tid=a.book.add_verify('gas',[1.55,2.55],'R2','A',1)
    a.start_task(tid)
    a.job.update(phase='measure',samples=[5.0]*15+[2000.0],visual_hits=8)
    a.decide_verification()
    assert not any(f.get('confirmed') for f in a.book.findings.values())


def test_turn_blocked_by_safety_guard_has_a_bounded_view_retry(verifier):
    a=verifier
    a.pose=[1,3,0]
    tid=a.book.add_verify('gas',[1.55,2.55],'R2','A',1)
    a.start_task(tid)
    a.low_points=np.array([[0,.25]])
    a.job.update(phase='face',face_t0=-1)
    a.verify_step(a.motion_ranges)
    assert a.job['view_index']==1
    assert a.job['phase']=='goto'


def test_sensor_disagreement_is_preserved_across_all_viewpoints(verifier):
    a=verifier
    a.pose=[1,3,0]
    tid=a.book.add_verify('gas',[1.55,2.55],'R2','A',1)
    a.start_task(tid)
    a.job.update(samples=[487.0]*16,visual_hits=0)
    a.decide_verification()
    a.job.update(view_index=len(a.job['views'])-1,samples=[5.0]*16,visual_hits=8,seen_t=10)
    a.decide_verification()
    assert a.book.done[tid]['result']=='inconclusive'


def test_confirmation_reports_recent_camera_location_instead_of_old_estimate(verifier):
    a=verifier
    a.pose=[1,3,0]
    tid=a.book.add_verify('gas',[.1,2.5],'R2','A',1)
    a.start_task(tid)
    a.job.update(samples=[487.0]*16,visual_hits=3,visual_quality_hits=3,seen_t=10,
                 visual_positions=[[1.5,2.5],[1.6,2.6],[1.55,2.55]],
                 quality_positions=[[1.5,2.5],[1.6,2.6],[1.55,2.55]],quality_seen_t=10)
    a.decide_verification()
    confirmed=[f for f in a.book.findings.values() if f.get('confirmed')]
    assert confirmed[0]['pos']==[1.55,2.55]


def test_camera_matches_from_before_measurement_cannot_confirm(verifier):
    a=verifier
    a.pose=[1,3,0]
    tid=a.book.add_verify('gas',[1.55,2.55],'R2','A',1)
    a.start_task(tid)
    a.job.update(samples=[487.0]*16,visual_hits=8,seen_t=1)
    a.decide_verification()
    assert not any(f.get('confirmed') for f in a.book.findings.values())


def test_stale_gas_reading_is_not_used_as_a_fresh_sensor_sample(verifier):
    a=verifier
    a.pose=[1,3,0]
    tid=a.book.add_verify('gas',[1.55,2.55],'R2','A',1)
    a.start_task(tid)
    a.gas_ppm,a.specialty_t=487,1
    a.job.update(phase='measure',samples=[],m_t0=5,visual_hits=8,seen_t=10)
    a.verify_step(a.motion_ranges)
    assert tid not in a.book.done
    assert not any(f.get('confirmed') for f in a.book.findings.values())


def test_perceive_counts_independent_frames_and_uses_current_camera_bearing(verifier):
    # Only the external camera/detector I/O are supplied. Real RGB conversion,
    # localization, evidence tracking and Agent.perceive execute together.
    a=verifier
    a.pose=[0,0,0]
    a.cfg={}
    a.cam_w,a.cam_h,a.cam_fov,a.lidar_max=320,240,1.2,8
    a.cam=types.SimpleNamespace(getImage=lambda:bytes(320*240*4))
    detection=dict(cls='gas_leak',box=[65,32,125,208],score=.95)
    a.detector=types.SimpleNamespace(available=True,detect=lambda rgb:[detection,detection])
    a.tracker=EvidenceTracker()
    a.state,a.task='VERIFY','fixture_verify'
    a.job=dict(pos=[2.1,.6],hazard='gas',phase='measure',visual_hits=0)
    a.perceive(a.motion_ranges)
    a.perceive(a.motion_ranges)  # same capture time must not supply another observation
    assert a.job['visual_hits']==1
    assert len(a.job['visual_positions'])==1
    assert a.job['aim']==pytest.approx(.271,abs=.002)
    for t in [10.512,11.024]:
        a.robot=types.SimpleNamespace(getTime=lambda:t)
        a.perceive(a.motion_ranges)
    assert a.job['visual_hits']==3
    assert len(a.job['visual_positions'])==3


def test_distant_fire_view_is_refined_before_reporting_a_location(verifier):
    # Captured calibration box: real source was 5.4 m away, while the
    # height-only projection put it 3.7 m away. A hot reading cannot fix depth.
    a=verifier
    a.capability='thermal'
    a.pose=[.9507,-2.5752,.0913]
    tid=a.book.add_verify('fire',[3.09,-2.62],'R4','B',1)
    a.start_task(tid)
    a.cfg={}
    a.cam_w,a.cam_h,a.cam_fov,a.lidar_max=320,240,1.2,8
    a.cam=types.SimpleNamespace(getImage=lambda:bytes(320*240*4))
    d=dict(cls='fire',score=.998,box=[148.84,87.57,218.06,153.50])
    a.detector=types.SimpleNamespace(available=True,detect=lambda rgb:[d])
    a.tracker=EvidenceTracker()
    a.job.update(phase='measure',samples=[.32]*16)
    for t in [10,10.512,11.024]:
        a.robot=types.SimpleNamespace(getTime=lambda:t)
        a.perceive(a.motion_ranges)
    a.decide_verification()
    assert not any(f.get('confirmed') for f in a.book.findings.values())
    assert a.job['view_index']==1
    assert a.goal[0]>2.0


@pytest.mark.parametrize('quality_time',[1,10])
def test_small_boxes_cannot_refresh_or_contaminate_detailed_camera_evidence(verifier,quality_time):
    a=verifier
    a.pose=[1,3,0]
    tid=a.book.add_verify('gas',[.1,2.5],'R2','A',1)
    a.start_task(tid)
    good=[[1.5,2.5],[1.6,2.6],[1.55,2.55]]
    a.job.update(samples=[487.0]*16,visual_hits=8,visual_quality_hits=3,seen_t=10,
                 visual_positions=good+[[.1,2.5]]*5,quality_positions=good,quality_seen_t=quality_time)
    a.decide_verification()
    confirmed=[f for f in a.book.findings.values() if f.get('confirmed')]
    if quality_time==10:
        assert confirmed[0]['pos']==[1.55,2.55]
    else:
        assert not confirmed


def test_visual_only_person_reports_also_wait_for_independent_detailed_frames(verifier):
    a=verifier
    a.pose=[0,0,0]
    a.cfg={}
    a.cam_w,a.cam_h,a.cam_fov,a.lidar_max=320,240,1.2,8
    a.cam=types.SimpleNamespace(getImage=lambda:bytes(320*240*4))
    detections=[dict(cls='person',box=[135,87,185,153],score=.95)]
    a.detector=types.SimpleNamespace(available=True,detect=lambda rgb:detections)
    a.tracker=EvidenceTracker()
    a.state,a.job='INSPECT',{}
    for t in [10,10.512,11.024]:
        a.robot=types.SimpleNamespace(getTime=lambda:t)
        a.perceive(a.motion_ranges)
    assert not any(f.get('confirmed') for f in a.book.findings.values())
    detections[:]=[dict(cls='person',box=[130,20,190,220],score=.95)]
    for t in [12,12.512,13.024]:
        a.robot=types.SimpleNamespace(getTime=lambda:t)
        a.perceive(a.motion_ranges)
    confirmed=[f for f in a.book.findings.values() if f.get('confirmed')]
    assert len(confirmed)==1
    assert confirmed[0]['pos'][0]==pytest.approx(2.2,abs=.05)
