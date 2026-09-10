"""Simple two-dimensional polygon model."""

import math
from dataclasses import dataclass

from scrap_monitoring_lidar_generator.geometry.primitives import Vec2

_GEOMETRY_TOLERANCE = 1e-12

type Edge2 = tuple[Vec2, Vec2]


@dataclass(frozen=True, slots=True)
class Polygon2:
    """A finite simple polygon with implicit closing edge."""

    vertices: tuple[Vec2, ...]

    def __post_init__(self) -> None:
        vertices = tuple(self.vertices)
        object.__setattr__(self, "vertices", vertices)
        if len(vertices) < 3:
            raise ValueError("polygon must contain at least 3 vertices")
        if len(vertices) != len(set(vertices)):
            raise ValueError("polygon must contain unique vertices")

        doubled_area = math.fsum(left.x * right.y - right.x * left.y for left, right in self.edges)
        if not math.isfinite(doubled_area):
            raise ValueError("polygon area calculation must be finite")
        if math.isclose(doubled_area, 0.0, rel_tol=0.0, abs_tol=_GEOMETRY_TOLERANCE):
            raise ValueError("polygon must enclose a non-zero area")

        edges = self.edges
        for first_index, first in enumerate(edges):
            for second_index in range(first_index + 1, len(edges)):
                if _edges_are_adjacent(first_index, second_index, len(edges)):
                    continue
                if _segments_intersect(*first, *edges[second_index]):
                    raise ValueError("polygon must not self-intersect")

    @property
    def edges(self) -> tuple[Edge2, ...]:
        """Return boundary edges including the implicit closing edge."""
        return tuple(
            (vertex, self.vertices[(index + 1) % len(self.vertices)])
            for index, vertex in enumerate(self.vertices)
        )

    @property
    def signed_area(self) -> float:
        """Return signed area, positive for counter-clockwise winding."""
        return 0.5 * math.fsum(left.x * right.y - right.x * left.y for left, right in self.edges)

    def contains(self, point: Vec2, *, include_boundary: bool = True) -> bool:
        """Return whether a point lies inside the polygon."""
        if any(_point_on_segment(point, start, end) for start, end in self.edges):
            return include_boundary

        inside = False
        for start, end in self.edges:
            if (start.y > point.y) == (end.y > point.y):
                continue
            intersection_x = start.x + (point.y - start.y) * (end.x - start.x) / (end.y - start.y)
            if point.x < intersection_x:
                inside = not inside
        return inside


def _edges_are_adjacent(first: int, second: int, edge_count: int) -> bool:
    return second == first + 1 or (first == 0 and second == edge_count - 1)


def _segments_intersect(a: Vec2, b: Vec2, c: Vec2, d: Vec2) -> bool:
    first = _orientation(a, b, c)
    second = _orientation(a, b, d)
    third = _orientation(c, d, a)
    fourth = _orientation(c, d, b)

    if first * second < 0 and third * fourth < 0:
        return True
    return (
        (first == 0 and _point_on_segment(c, a, b))
        or (second == 0 and _point_on_segment(d, a, b))
        or (third == 0 and _point_on_segment(a, c, d))
        or (fourth == 0 and _point_on_segment(b, c, d))
    )


def _orientation(a: Vec2, b: Vec2, c: Vec2) -> int:
    cross_product = (b - a).cross(c - a)
    if not math.isfinite(cross_product):
        raise ValueError("polygon intersection calculation must be finite")
    if math.isclose(cross_product, 0.0, rel_tol=0.0, abs_tol=_GEOMETRY_TOLERANCE):
        return 0
    return 1 if cross_product > 0 else -1


def _point_on_segment(point: Vec2, start: Vec2, end: Vec2) -> bool:
    if _orientation(start, end, point) != 0:
        return False
    return (
        min(start.x, end.x) - _GEOMETRY_TOLERANCE
        <= point.x
        <= max(start.x, end.x) + _GEOMETRY_TOLERANCE
        and min(start.y, end.y) - _GEOMETRY_TOLERANCE
        <= point.y
        <= max(start.y, end.y) + _GEOMETRY_TOLERANCE
    )
