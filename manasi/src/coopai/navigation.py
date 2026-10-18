"""Public geometry routes for point-agent kinematics; no physical world access."""
from math import hypot

from .eligibility import shortest_path
from .geometry import FloorPlan
from .models import Environment, Position

EPSILON = 1e-9  # Geometry comparisons only, never energy/capability relaxation.


def distance(a: Position, b: Position) -> float:
    if not isinstance(a, Position) or not isinstance(b, Position) or a.x is None or b.x is None:
        raise ValueError("coordinate positions required")
    return hypot(b.x - a.x, b.y - a.y)


def located_position(plan: FloorPlan, position: Position) -> Position:
    """Configured location-only spawns acquire their area's center, once at setup."""
    area = next((a for a in plan.areas if a.location.location_id == position.location_id), None)
    if area is None:
        raise ValueError("unknown geometric location")
    result = Position(position.location_id, *area.bounds.center) if position.x is None else position
    if not point_allowed(plan, result):
        raise ValueError("position outside area or on a solid wall")
    return result


def _on_opening(opening, x, y):
    if opening.x1 == opening.x2:
        return abs(x-opening.x1) <= EPSILON and opening.y1 + EPSILON < y < opening.y2 - EPSILON
    return abs(y-opening.y1) <= EPSILON and opening.x1 + EPSILON < x < opening.x2 - EPSILON


def point_allowed(plan: FloorPlan, position: Position) -> bool:
    if position.x is None:
        return False
    area = next((a for a in plan.areas if a.location.location_id == position.location_id), None)
    if area is None:
        return False
    r, x, y = area.bounds, position.x, position.y
    if r.x < x < r.x+r.width and r.y < y < r.y+r.height:
        return True
    if not r.x-EPSILON <= x <= r.x+r.width+EPSILON or not r.y-EPSILON <= y <= r.y+r.height+EPSILON:
        return False
    return any(position.location_id in (p.source,p.target) and p.target is not None and _on_opening(p.opening,x,y) for p in plan.passages)


def plan_route(plan: FloorPlan, start: Position, destination: str,
               topology: Environment | None = None) -> tuple[Position, ...] | None:
    topology = topology or plan.environment()
    start = located_position(plan,start)
    path = shortest_path(topology,start.location_id,destination)
    if path is None:
        return None
    areas = {a.location.location_id:a for a in plan.areas}
    route = [start]
    for source,target in zip(path,path[1:]):
        passage = next((p for p in sorted(plan.passages,key=lambda p:p.passage_id) if {p.source,p.target}=={source,target}),None)
        if passage is None:
            raise ValueError("topology edge has no geometric doorway")
        center=Position(source,*areas[source].bounds.center)
        if route[-1]!=center:
            route.append(center)
        o=passage.opening
        x,y=(o.x1+o.x2)/2,(o.y1+o.y2)/2
        route.extend((Position(source,x,y),Position(target,x,y),Position(target,*areas[target].bounds.center)))
    goal=Position(destination,*areas[destination].bounds.center)
    if route[-1] != goal:
        route.append(goal)
    validate_route(plan,topology,start,tuple(route),goal)
    return tuple(route)


def validate_route(plan: FloorPlan, topology: Environment, start: Position,
                   route: tuple[Position, ...], goal: Position) -> None:
    if not isinstance(route,tuple) or not route or len(route)>256 or any(not isinstance(p,Position) for p in route):
        raise ValueError("invalid or excessively long route")
    if route[0].location_id != start.location_id or distance(route[0],start)>EPSILON:
        raise ValueError("route does not start at own current position")
    if route[-1].location_id != goal.location_id or distance(route[-1],goal)>EPSILON:
        raise ValueError("route does not end at task destination center")
    if any(not point_allowed(plan,p) for p in route):
        raise ValueError("route crosses outside geometry or a solid wall")
    edges={(e.source,e.target) for e in topology.connections}
    for a,b in zip(route,route[1:]):
        if a.location_id == b.location_id:
            # Convex interiors keep a segment inside. Boundary-to-boundary
            # wall-following still needs an explicit solid-wall overlap check.
            for wall in plan.walls():
                horizontal=(abs(a.y-b.y)<=EPSILON and wall.y1==wall.y2 and abs(a.y-wall.y1)<=EPSILON)
                vertical=(abs(a.x-b.x)<=EPSILON and wall.x1==wall.x2 and abs(a.x-wall.x1)<=EPSILON)
                if horizontal and min(max(a.x,b.x),wall.x2)-max(min(a.x,b.x),wall.x1)>EPSILON:
                    raise ValueError("route runs along a solid wall")
                if vertical and min(max(a.y,b.y),wall.y2)-max(min(a.y,b.y),wall.y1)>EPSILON:
                    raise ValueError("route runs along a solid wall")
            continue
        if (a.location_id,b.location_id) not in edges or distance(a,b)>EPSILON:
            raise ValueError("location transitions must use a zero-distance doorway waypoint")
        if not any({p.source,p.target}=={a.location_id,b.location_id} and _on_opening(p.opening,a.x,a.y) for p in plan.passages):
            raise ValueError("location transition is not inside a connecting door")
