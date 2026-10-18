import pytest
from swarm.traffic import DoorwayTraffic

DOORS = {'D': {'pos':[0,0], 'wall':'v', 'width':1.2}}


def exchange(a,b,now):
    for src,dst in [(a,b),(b,a)]:
        dst.receive(src.message(),now)


def test_opposed_requests_choose_one_owner_and_waiter_moves_out_of_lane():
    a,b=DoorwayTraffic('A',DOORS,['A','B']),DoorwayTraffic('B',DOORS,['A','B'])
    a.update([-1.8,0,0],[2.4,0],None,0)
    b.update([1.8,0,3.14],[-2.4,0],None,0)
    for i in range(4):
        exchange(a,b,i*.1)
        a.update([-1.8,0,0],[2.4,0],None,i*.1)
        target=b.update([1.35,1.0,3.14],[-2.4,0],None,i*.1)
    assert a.crossing
    assert not b.crossing
    assert target[1] >= .8


def test_request_needs_peer_acknowledgement_and_ignores_wrong_ticket():
    a=DoorwayTraffic('A',DOORS,['A','B'])
    a.update([-1.8,0,0],[2.4,0],None,0)
    assert not a.crossing
    a.receive({'src':'B','type':'traffic','serial':1,'clock':3,'pose':[2,1,0],
               'request':None,'acks':[['A',999]]},.1)
    a.update([-1.8,0,0],[2.4,0],None,.1)
    assert not a.crossing


def test_release_allows_waiting_peer_and_old_messages_cannot_reopen_lock():
    a,b=DoorwayTraffic('A',DOORS,['A','B']),DoorwayTraffic('B',DOORS,['A','B'])
    for obj,pos,goal in [(a,[-1.8,0,0],[2.4,0]),(b,[1.8,0,0],[-2.4,0])]:
        obj.update(pos,goal,None,0)
    old=a.message()
    for i in range(4):
        exchange(a,b,.1*i)
        a.update([-1.8,0,0],[2.4,0],None,.1*i)
        b.update([1.35,1,0],[-2.4,0],None,.1*i)
    a.update([1.1,0,0],[2.4,0],None,1)
    b.receive(a.message(),1)
    a.receive(b.message(),1)
    a.update([2.4,0,0],[2.4,0],None,1.1)
    b.receive(a.message(),1.1)
    b.update([1.35,1,0],[-2.4,0],None,1.1)
    assert b.crossing
    b.receive(old,1.2)
    assert b.remote['A']['request'] is None


def test_unreachable_attempt_is_not_a_successful_completion():
    from swarm.tasks import TaskBook
    from swarm.blueprint import Blueprint
    book=TaskBook(Blueprint())
    book.mark_done('inspect:R1','A',1,'unreachable')
    assert 'inspect:R1' not in book.done
    assert any(t['id']=='inspect:R1' for t in book.open_tasks())


def test_abandoned_unoccupied_request_is_cancelled():
    a=DoorwayTraffic('A',DOORS,['A','B'])
    a.update([-1.8,0,0],[2.4,0],None,0)
    assert a.request is not None
    a.update([-1.8,0,0],None,None,.1)
    assert a.request is None


def test_parking_target_uses_local_free_space_instead_of_obstructed_fixed_point():
    a,b=DoorwayTraffic('A',DOORS,['A','B']),DoorwayTraffic('B',DOORS,['A','B'])
    b.update([-1.8,0,0],[2.4,0],None,0)
    a.receive(b.message(),0)
    target=a.update([.7,-.5,0],[2.4,-.5],None,.1,clear=lambda p:p[0]<1)
    assert target[0]<1
    assert abs(target[1])>=1


def test_overlapping_approaches_to_different_doors_have_one_priority_order():
    doors={'D1':dict(pos=[0,1],wall='h',width=1.2),
           'D3':dict(pos=[0,-1],wall='h',width=1.2)}
    a,b=DoorwayTraffic('A',doors,['A','B']),DoorwayTraffic('B',doors,['A','B'])
    a.update([-.5,0,0],[-.5,3],None,0)
    b.update([.5,0,0],[.5,-3],None,0)
    for i in range(3):
        exchange(a,b,.1*i)
        a.update([-.5,0,0],[-.5,3],None,.1*i)
        b.update([1.2,-.35,0],[.5,-3],None,.1*i)
    assert a.crossing
    assert b.state=='yield'


def test_clear_but_unreachable_parking_target_is_reselected_then_fails_explicitly():
    a,b=DoorwayTraffic('A',DOORS,['A','B']),DoorwayTraffic('B',DOORS,['A','B'])
    b.update([-1.8,0,0],[2.4,0],None,0)
    a.receive(b.message(),0)
    seen=[]
    for i in range(4):
        p=a.update([.7,-.5,0],[2.4,-.5],None,.1*i,clear=lambda p:True)
        assert p not in seen
        seen.append(p)
        a.reject_target(p)
    a.update([.7,-.5,0],[2.4,-.5],None,.5,clear=lambda p:True)
    assert a.state=='blocked'


def test_occupied_ticket_can_release_after_clearance_even_after_target_failures():
    a=DoorwayTraffic('A',DOORS,['A'])
    a.update([-1.8,0,0],[2.4,0],None,0)
    assert a.crossing
    for i in range(4):
        a.reject_target([1.35,float(i)])
    a.update([1.1,0,0],[2.4,0],None,1)
    assert a.request is None


def test_no_locally_clear_parking_candidate_reports_blocked():
    a,b=DoorwayTraffic('A',DOORS,['A','B']),DoorwayTraffic('B',DOORS,['A','B'])
    b.update([-1.8,0,0],[2.4,0],None,0)
    a.receive(b.message(),0)
    a.update([.7,-.5,0],[2.4,-.5],None,.1,clear=lambda p:False)
    assert a.state=='blocked'


def test_no_locally_clear_exit_reports_blocked_and_retains_ownership():
    a=DoorwayTraffic('A',DOORS,['A'])
    a.update([-.2,0,0],[2.4,0],None,0,clear=lambda p:False)
    assert a.crossing
    assert a.state=='blocked'


@pytest.mark.parametrize('pose,path', [([-1.8,0,0],[[0,0],[2.4,0]]),([0,0,0],[[2.4,0]])])
def test_path_waypoint_exactly_on_door_plane_still_reserves_crossing(pose,path):
    a=DoorwayTraffic('A',DOORS,['A','B'])
    a.update(pose,[2.4,0],path,0)
    assert a.request is not None
    assert a.request['side']==-1


@pytest.mark.parametrize('normal,should_release',[(-2.1,True),(-.2,False)])
def test_changed_goal_cancels_reserved_route_only_when_safely_before_door(normal,should_release):
    a=DoorwayTraffic('A',DOORS,['A'])
    a.update([normal,0,0],[2.4,0],None,0)
    assert a.crossing
    for i in range(4):
        a.reject_target([1.35,float(i)])
    a.update([normal,0,0],[-2.4,1.2],None,1)
    assert (a.request is None)==should_release
    assert (a.state=='idle')==should_release


def test_abandoned_reserved_route_releases_after_retreat_to_safe_approach():
    a=DoorwayTraffic('A',DOORS,['A'])
    a.update([-.2,0,0],[2.4,0],None,0)
    a.update([-.2,0,0],[-2.4,1.2],None,1)
    assert a.crossing
    a.update([-1.2,0,0],[-2.4,1.2],None,2)
    assert a.request is None
    assert a.state=='idle'
