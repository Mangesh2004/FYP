import json
import math

import pytest


def test_lidar_export_preserves_ray_bearings_and_rejects_nonfinite_ranges():
    from swarm.live_sensors import lidar_snapshot
    s=lidar_snapshot([2,float('inf'),float('nan'),0,-1,9],[1,.5,0,-.5,-1,-1.5],8,10,10.2)
    assert s['ranges_m']==[2,None,None,None,None,None]
    assert s['angles_rad']==[1,.5,0,-.5,-1,-1.5]
    assert s['valid_count']==1 and s['ray_count']==6 and s['nearest_m']==2
    assert s['age_s']==pytest.approx(.2)
    json.dumps(s,allow_nan=False)


def test_empty_lidar_is_explicitly_unavailable_instead_of_zero_distance():
    from swarm.live_sensors import lidar_snapshot
    s=lidar_snapshot([math.inf,math.nan],[1,-1],8,-1e9,3)
    assert s['nearest_m'] is None and s['valid_count']==0
    assert s['ranges_m']==[None,None]


def test_fire_demo_has_one_searching_robot_and_no_hazard_coordinates_in_its_config(tmp_path):
    from tools.run_fire_demo import make_demo_world
    world,cfg=make_demo_world(tmp_path)
    assert world.count('Pioneer3dx {')==1
    assert 'controller "fire_demo_agent"' in world
    assert cfg['search_rooms']==['R1'] and cfg['peer_ids']==['A']
    assert not {'fire_pos','hazards','truth','target'} & cfg.keys()
    from tests.test_no_ground_truth import violations,ROOT
    assert not violations((ROOT/'controllers/fire_demo_agent/fire_demo_agent.py').read_text())


def test_demo_output_requires_a_fresh_directory_to_prevent_stale_confirmations(tmp_path):
    from tools.run_fire_demo import prepare_output
    fresh=tmp_path/'new_run'
    assert prepare_output(fresh)==fresh.resolve()
    (fresh/'fire_telemetry_A.json').write_text('{"findings":[{"confirmed":true}]}')
    with pytest.raises(ValueError,match='fresh'):
        prepare_output(fresh)
    assert (fresh/'fire_telemetry_A.json').exists()
