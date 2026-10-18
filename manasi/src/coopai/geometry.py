"""Axis-aligned floor-plan geometry, independent of hazards and rendering."""
from dataclasses import dataclass
from itertools import combinations

from .models import Connection, Environment, Location, _id, _number, _tick


@dataclass(frozen=True)
class Rectangle:
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        for value in (self.x, self.y):
            _number(value, low=-float("inf"))
        for value in (self.width, self.height):
            _number(value)
            if value == 0:
                raise ValueError("rectangle dimensions must be positive")
        _number(self.x + self.width, low=-float("inf"))
        _number(self.y + self.height, low=-float("inf"))

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2


@dataclass(frozen=True)
class Segment:
    x1: float
    y1: float
    x2: float
    y2: float

    def __post_init__(self) -> None:
        for value in (self.x1, self.y1, self.x2, self.y2):
            _number(value, low=-float("inf"))
        if not ((self.x1 == self.x2 and self.y1 < self.y2) or
                (self.y1 == self.y2 and self.x1 < self.x2)):
            raise ValueError("segment must be horizontal/vertical with increasing endpoints")


def _on_boundary(rect: Rectangle, line: Segment) -> bool:
    if line.x1 == line.x2:
        return line.x1 in (rect.x, rect.x + rect.width) and rect.y < line.y1 < line.y2 < rect.y + rect.height
    return line.y1 in (rect.y, rect.y + rect.height) and rect.x < line.x1 < line.x2 < rect.x + rect.width


def _boundary_overlap(rect: Rectangle, line: Segment) -> bool:
    """Detect even partial contact with another area's perimeter."""
    if line.x1 == line.x2:
        return line.x1 in (rect.x, rect.x + rect.width) and max(rect.y, line.y1) < min(rect.y + rect.height, line.y2)
    return line.y1 in (rect.y, rect.y + rect.height) and max(rect.x, line.x1) < min(rect.x + rect.width, line.x2)


@dataclass(frozen=True)
class AreaGeometry:
    location: Location
    area_type: str
    bounds: Rectangle

    def __post_init__(self) -> None:
        if not isinstance(self.location, Location) or not isinstance(self.bounds, Rectangle):
            raise ValueError("invalid location or bounds")
        if not isinstance(self.area_type, str) or self.area_type not in {"room", "corridor"}:
            raise ValueError("area_type must be room or corridor")


@dataclass(frozen=True)
class Passage:
    passage_id: str
    source: str
    target: str | None
    opening: Segment
    travel_ticks: int = 1

    def __post_init__(self) -> None:
        _id(self.passage_id)
        _id(self.source)
        if self.target is not None:
            _id(self.target)
            if self.target == self.source:
                raise ValueError("passage endpoints must differ")
        if not isinstance(self.opening, Segment):
            raise ValueError("invalid opening")
        _tick(self.travel_ticks)
        if self.travel_ticks == 0:
            raise ValueError("travel time must be positive")


@dataclass(frozen=True)
class FloorPlan:
    areas: tuple[AreaGeometry, ...]
    passages: tuple[Passage, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.areas, tuple) or not self.areas or any(not isinstance(x, AreaGeometry) for x in self.areas):
            raise ValueError("areas must be a nonempty tuple of AreaGeometry")
        if not isinstance(self.passages, tuple) or any(not isinstance(x, Passage) for x in self.passages):
            raise ValueError("passages must be a tuple of Passage")
        areas = {a.location.location_id: a for a in self.areas}
        if len(areas) != len(self.areas):
            raise ValueError("duplicate location ID")
        if len({p.passage_id for p in self.passages}) != len(self.passages):
            raise ValueError("duplicate passage ID")
        for a, b in combinations(self.areas, 2):
            r, s = a.bounds, b.bounds
            if max(r.x, s.x) < min(r.x + r.width, s.x + s.width) and max(r.y, s.y) < min(r.y + r.height, s.y + s.height):
                raise ValueError("area interiors overlap")
        for passage in self.passages:
            endpoints = (passage.source,) if passage.target is None else (passage.source, passage.target)
            for endpoint in endpoints:
                if endpoint not in areas:
                    raise ValueError(f"passage {passage.passage_id}: unknown location {endpoint}")
                if not _on_boundary(areas[endpoint].bounds, passage.opening):
                    raise ValueError(f"passage {passage.passage_id}: opening must lie strictly within endpoint boundary")
            if passage.target is None and any(_boundary_overlap(a.bounds, passage.opening) for key, a in areas.items() if key != passage.source):
                raise ValueError("exterior entry cannot open into another area")
        for a, b in combinations(self.passages, 2):
            p, q = a.opening, b.opening
            horizontal = p.y1 == p.y2 == q.y1 == q.y2
            vertical = p.x1 == p.x2 == q.x1 == q.x2
            if (horizontal and max(p.x1, q.x1) < min(p.x2, q.x2)) or (vertical and max(p.y1, q.y1) < min(p.y2, q.y2)):
                raise ValueError("passage openings overlap")
        self.environment()  # Reuse existing topology validation (including duplicate edges).

    def environment(self) -> Environment:
        """Public directed topology derived from explicit bidirectional passages."""
        edges = tuple(Connection(a, b, p.travel_ticks) for p in self.passages if p.target is not None
                      for a, b in ((p.source, p.target), (p.target, p.source)))
        return Environment(tuple(a.location for a in self.areas), edges)

    def walls(self) -> tuple[Segment, ...]:
        """Perimeter wall segments excluding door openings, suitable for drawing."""
        walls = set()
        for area in self.areas:
            r = area.bounds
            sides = (Segment(r.x, r.y, r.x + r.width, r.y),
                     Segment(r.x, r.y + r.height, r.x + r.width, r.y + r.height),
                     Segment(r.x, r.y, r.x, r.y + r.height),
                     Segment(r.x + r.width, r.y, r.x + r.width, r.y + r.height))
            for side in sides:
                horizontal = side.y1 == side.y2
                start, end = (side.x1, side.x2) if horizontal else (side.y1, side.y2)
                gaps = []
                for passage in self.passages:
                    if area.location.location_id not in (passage.source, passage.target):
                        continue
                    o = passage.opening
                    if horizontal and o.y1 == o.y2 == side.y1:
                        gaps.append((o.x1, o.x2))
                    elif not horizontal and o.x1 == o.x2 == side.x1:
                        gaps.append((o.y1, o.y2))
                cursor = start
                for left, right in (*sorted(gaps), (end, end)):
                    if cursor < left:
                        walls.add(Segment(cursor, side.y1, left, side.y1) if horizontal else Segment(side.x1, cursor, side.x1, left))
                    cursor = right
        return tuple(sorted(walls, key=lambda s: (s.x1, s.y1, s.x2, s.y2)))
