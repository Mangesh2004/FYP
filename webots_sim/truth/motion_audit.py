"""Observer-only physical safety metrics. Agents cannot import this module."""
import math


class MotionAudit:
    def __init__(self, doors):
        self.doors = doors
        self.samples = self.contacts = self.overlap = 0
        self.min_sep = float('inf')
        self.tilt, self.z = {}, {}

    def sample(self, nodes):
        self.samples += 1
        positions = {n:node.getPosition() for n,node in nodes.items()}
        names = list(nodes)
        if len(names)>1:
            self.min_sep = min(self.min_sep,math.dist(positions[names[0]][:2],positions[names[1]][:2]))
        contact = False
        for n,node in nodes.items():
            up=node.getOrientation()[8]
            tilt=math.degrees(math.acos(max(-1,min(1,up)))) if math.isfinite(up) else 180
            self.tilt[n]=max(self.tilt.get(n,0),tilt)
            z=positions[n][2]
            old=self.z.get(n,[z,z])
            self.z[n]=[min(old[0],z),max(old[1],z)]
            contact |= any(p.point[2]>.035 for p in node.getContactPoints(True))
        self.contacts += contact
        for d in self.doors.values():
            inside=[]
            for p in positions.values():
                dx,dy=p[0]-d['pos'][0],p[1]-d['pos'][1]
                normal,tangent=(dy,dx) if d['wall']=='h' else (dx,dy)
                inside.append(abs(normal)<.55 and abs(tangent)<d['width']/2+.15)
            if sum(inside)>1:
                self.overlap += 1
                break

    def summary(self):
        return dict(samples=self.samples,contact_steps=self.contacts,doorway_overlap_steps=self.overlap,
                    minimum_separation=self.min_sep,max_tilt_deg=self.tilt,z_range=self.z)
