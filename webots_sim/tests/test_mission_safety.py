import math
from types import SimpleNamespace

import pytest
from truth.motion_audit import MotionAudit


def test_audit_counts_obstacle_contact_tilt_and_door_conflict_from_physics():
    def node(pos,up,contacts):
        return SimpleNamespace(getPosition=lambda:pos,
            getOrientation=lambda:[1,0,0,0,1,0,0,0,up],
            getContactPoints=lambda descendants:[SimpleNamespace(point=p) for p in contacts])
    nodes={'A':node([-.35,0,.095],1,[[0,0,0]]),
           'B':node([.35,0,.095],math.sqrt(3)/2,[[.1,0,.1]])}
    audit=MotionAudit({'D':{'pos':[0,0],'wall':'v','width':1.2}})
    audit.sample(nodes)
    s=audit.summary()
    assert s['contact_steps']==1
    assert s['doorway_overlap_steps']==1
    assert s['minimum_separation']==pytest.approx(.7)
    assert s['max_tilt_deg']['B']==pytest.approx(30)
