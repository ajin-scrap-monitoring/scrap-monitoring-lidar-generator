"""Environment scene composition and first-hit queries."""

import math
from dataclasses import dataclass, field
from enum import StrEnum

from scrap_monitoring_lidar_generator.geometry.intersections import (
    DEFAULT_MIN_DISTANCE_M,
    intersect_triangle,
    validate_distance_bounds,
)
from scrap_monitoring_lidar_generator.geometry.polygon import Polygon2
from scrap_monitoring_lidar_generator.geometry.primitives import Ray, Triangle, Vec2, Vec3

_SCENE_TOLERANCE = 1e-9


class HitKind(StrEnum):
    """Environment target hit by a ray."""

    FLOOR = "floor"
    WALL = "wall"
    SURFACE = "surface"


@dataclass(frozen=True, slots=True)
class RayHit:
    """Nearest environment intersection along a ray."""

    distance_m: float
    position_m: Vec3
    kind: HitKind


@dataclass(frozen=True, slots=True)
class EnvironmentScene:
    """Open-top environment with optional fixed surface triangles."""

    boundary: Polygon2
    floor_z_m: float
    top_z_m: float
    surface_triangles: tuple[Triangle, ...] = ()
    _wall_triangles: tuple[Triangle, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not math.isfinite(self.floor_z_m) or not math.isfinite(self.top_z_m):
            raise ValueError("scene heights must be finite")
        if self.top_z_m <= self.floor_z_m:
            raise ValueError("scene top must be above scene floor")

        surfaces = tuple(self.surface_triangles)
        object.__setattr__(self, "surface_triangles", surfaces)
        self._validate_surfaces(surfaces)
        object.__setattr__(self, "_wall_triangles", self._build_wall_triangles())

    def first_hit(
        self,
        ray: Ray,
        *,
        min_distance_m: float = DEFAULT_MIN_DISTANCE_M,
        max_distance_m: float = math.inf,
    ) -> RayHit | None:
        """Return the nearest floor, wall, or fixed-surface hit."""
        validate_distance_bounds(min_distance_m, max_distance_m)
        nearest_distance_m = max_distance_m
        nearest_kind: HitKind | None = None

        floor_distance = self._intersect_floor(
            ray,
            min_distance_m=min_distance_m,
            max_distance_m=nearest_distance_m,
        )
        if floor_distance is not None:
            nearest_distance_m = floor_distance
            nearest_kind = HitKind.FLOOR

        for wall in self._wall_triangles:
            distance = intersect_triangle(
                ray,
                wall,
                min_distance_m=min_distance_m,
                max_distance_m=nearest_distance_m,
            )
            if distance is not None and (nearest_kind is None or distance < nearest_distance_m):
                nearest_distance_m = distance
                nearest_kind = HitKind.WALL

        for surface in self.surface_triangles:
            distance = intersect_triangle(
                ray,
                surface,
                min_distance_m=min_distance_m,
                max_distance_m=nearest_distance_m,
            )
            if distance is None or (nearest_kind is not None and distance >= nearest_distance_m):
                continue
            position = ray.point_at(distance)
            if not self.boundary.contains(Vec2(position.x, position.y)):
                continue
            nearest_distance_m = distance
            nearest_kind = HitKind.SURFACE

        if nearest_kind is None:
            return None
        return RayHit(
            distance_m=nearest_distance_m,
            position_m=ray.point_at(nearest_distance_m),
            kind=nearest_kind,
        )

    def _intersect_floor(
        self,
        ray: Ray,
        *,
        min_distance_m: float,
        max_distance_m: float,
    ) -> float | None:
        if math.isclose(ray.direction.z, 0.0, rel_tol=0.0, abs_tol=_SCENE_TOLERANCE):
            return None
        distance_m = (self.floor_z_m - ray.origin.z) / ray.direction.z
        if distance_m < min_distance_m or distance_m > max_distance_m:
            return None
        point = ray.point_at(distance_m)
        if not self.boundary.contains(Vec2(point.x, point.y)):
            return None
        return distance_m

    def _validate_surfaces(self, surfaces: tuple[Triangle, ...]) -> None:
        for triangle in surfaces:
            for vertex in (triangle.a, triangle.b, triangle.c):
                if not self.boundary.contains(Vec2(vertex.x, vertex.y)):
                    raise ValueError("surface vertices must lie inside the scene boundary")
                if not (
                    self.floor_z_m - _SCENE_TOLERANCE <= vertex.z <= self.top_z_m + _SCENE_TOLERANCE
                ):
                    raise ValueError("surface vertices must lie between floor and top")

    def _build_wall_triangles(self) -> tuple[Triangle, ...]:
        walls: list[Triangle] = []
        for start, end in self.boundary.edges:
            lower_start = Vec3(start.x, start.y, self.floor_z_m)
            lower_end = Vec3(end.x, end.y, self.floor_z_m)
            upper_start = Vec3(start.x, start.y, self.top_z_m)
            upper_end = Vec3(end.x, end.y, self.top_z_m)
            walls.extend(
                (
                    Triangle(lower_start, lower_end, upper_end),
                    Triangle(lower_start, upper_end, upper_start),
                )
            )
        return tuple(walls)
