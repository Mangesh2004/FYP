"""Peer-to-peer doorway right-of-way using ordered requests and explicit ACKs.

Only public door geometry, own pose and received messages enter decisions.
Requests/ACKs repeat to tolerate loss. A request's logical clock + agent ID
give a total order; an owner releases only after physically clearing the door.
"""
from __future__ import annotations

import math


class DoorwayTraffic:
    TIMEOUT = 6.0

    def __init__(self, me, doors, peer_ids):
        self.me, self.doors = me, doors
        self.peers = set(peer_ids) - {me}
        self.clock, self.serial = 0, 0
        self.request = None
        self.remote = {}
        self.acks = set()
        self.pose = [0, 0, 0]
        self.state, self.target = 'idle', None
        self.events = []
        self.started = None
        self.mission_goal = None
        self.parking = None
        self.failed_targets = set()

    def reject_target(self,point):
        self.failed_targets.add(tuple(round(x,2) for x in point))
        self.parking=None
        self.events.append(dict(state='replan',door=self.request['door'] if self.request else None,
                                reason='unreachable_staging',target=list(point)))

    def target_allowed(self,p,clear):
        return tuple(round(x,2) for x in p) not in self.failed_targets and (clear is None or clear(p))

    def parking_point(self,door,side,sign,pose,clear):
        if clear is None and not self.failed_targets:
            return self.point(door,side*1.35,sign*1.2)
        if self.parking and self.parking[0]==(door,side,sign) and self.target_allowed(self.parking[1],clear):
            return self.parking[1]
        candidates=[self.point(door,side*n,t) for n in [.85,1.35,1.8]
                    for t in [sign*1.2,sign*1.6,-sign*1.2,-sign*1.6]]
        candidates.sort(key=lambda p:math.dist(p,pose[:2]))
        point=next((p for p in candidates if self.target_allowed(p,clear)),None)
        if point is None:
            return None
        self.parking=((door,side,sign),point)
        return point

    @property
    def crossing(self):
        return bool(self.request and self.request.get('occupied'))

    def _state(self, value):
        if value != self.state:
            self.events.append({'state': value, 'door': self.request['door'] if self.request else None})
        self.state = value

    def coordinates(self, door, pos):
        d = self.doors[door]
        dx, dy = pos[0]-d['pos'][0], pos[1]-d['pos'][1]
        return (dy, dx) if d['wall'] == 'h' else (dx, dy)

    def point(self, door, normal, tangent):
        d = self.doors[door]
        return ([d['pos'][0]+tangent, d['pos'][1]+normal] if d['wall']=='h'
                else [d['pos'][0]+normal, d['pos'][1]+tangent])

    def receive(self, msg, now):
        src = msg.get('src')
        if src not in self.peers or msg.get('type') != 'traffic':
            return
        old = self.remote.get(src)
        if old and msg.get('serial',0) <= old.get('serial',0):
            return
        self.clock = max(self.clock, int(msg.get('clock',0)))+1
        self.remote[src] = {**msg, 'heard':now}

    def message(self):
        self.serial += 1
        return dict(type='traffic', src=self.me, serial=self.serial, clock=self.clock,
                    pose=self.pose[:], request=dict(self.request) if self.request else None,
                    acks=[list(a) for a in sorted(self.acks)])

    @staticmethod
    def priority(req):
        return req['ticket'][1], req['ticket'][0]

    def _next_door(self, pos, goal, path):
        points = [pos[:2]] + (path if path else [goal])
        for p,q in zip(points,points[1:]):
            for door,d in self.doors.items():
                pn,pt = self.coordinates(door,p)
                qn,qt = self.coordinates(door,q)
                if pn*qn > 0 or abs(pn-qn)<1e-6:
                    continue
                tangent = pt+(qt-pt)*abs(pn)/(abs(pn)+abs(qn))
                near_n,near_t = self.coordinates(door,pos)
                if abs(tangent) < d['width']/2 and math.hypot(near_n,near_t)<2.5:
                    side = (1 if near_n>0 else -1) if abs(near_n)>1e-6 else (-1 if qn>pn else 1)
                    return door, side
        return None

    def update(self, pose, goal, path, now, clear=None):
        self.pose = list(pose)
        self.started = now if self.started is None else self.started
        self.acks.clear()
        self.target = None
        logical_goal=tuple(goal) if goal is not None else None
        cancellable=not self.crossing
        if self.crossing:
            normal,_=self.coordinates(self.request['door'],pose)
            # A granted reservation is not proof that the chassis entered the
            # doorway. Cancel an abandoned approach only while safely before it.
            cancellable=normal*self.request['side']>.9
        if logical_goal!=self.mission_goal and cancellable:
            self.request=None
            self.failed_targets.clear()
        # Remember a pending cancellation while close to the doorway, so a
        # later safe retreat can release it even if the new goal is unchanged.
        if cancellable:
            self.mission_goal=logical_goal
        if self.crossing:
            n,_ = self.coordinates(self.request['door'],pose)
            if n*self.request['side'] < -.9:
                self.request = None
                self.failed_targets.clear()
                self._state('idle')
        if len(self.failed_targets)>=4:
            self.target=list(pose[:2])
            self._state('blocked')
            return self.target
        if self.request is None and goal is not None:
            choice = self._next_door(pose,goal,path)
            if choice:
                self.clock += 1
                self.request = dict(door=choice[0], side=choice[1], ticket=[self.me,self.clock], occupied=False)
        yield_for = None
        for src,remote in self.remote.items():
            req = remote.get('request')
            if not req or req['door'] not in self.doors or now-remote['heard']>self.TIMEOUT:
                continue
            n,t = self.coordinates(req['door'],pose)
            conflict = self.request and (self.request['door']==req['door'] or (abs(n)<2.2 and abs(t)<2))
            mine_first = conflict and (self.crossing or (not req.get('occupied') and self.priority(self.request)<self.priority(req)))
            if mine_first:
                continue
            if abs(n)<2.2 and abs(t)<2.0:
                yield_for = (req['door'],1 if n>=0 else -1,1 if t>=-.1 else -1)
                if abs(t)>=.75:
                    self.acks.add(tuple(req['ticket']))
            else:
                self.acks.add(tuple(req['ticket']))
        if yield_for and not self.crossing:
            door,side,offset = yield_for
            self.target = self.parking_point(door,side,offset,pose,clear)
            if self.target is None:
                self.target=list(pose[:2])
                self._state('blocked')
                return self.target
            self._state('yield')
            return self.target
        if self.request:
            req = self.request
            needed = {p for p in self.peers if now-self.remote.get(p,{}).get('heard',self.started)<=self.TIMEOUT}
            granted = all(tuple(req['ticket']) in [tuple(x) for x in self.remote.get(p,{}).get('acks',[])] for p in needed)
            if granted or self.crossing:
                req['occupied'] = True
                candidates=[self.point(req['door'],-req['side']*n,t) for n in [1.35,1.65]
                            for t in [0,.5,-.5,.8,-.8]]
                self.target = next((p for p in candidates if self.target_allowed(p,clear)),None)
                if self.target is None:
                    self.target=list(pose[:2])
                    self._state('blocked')
                    return self.target
                self._state('crossing')
            else:
                self.target = self.point(req['door'],req['side']*1.35,0)
                self._state('waiting')
        else:
            self._state('idle')
        return self.target
