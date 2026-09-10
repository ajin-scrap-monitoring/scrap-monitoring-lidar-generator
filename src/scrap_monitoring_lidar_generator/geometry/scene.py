"""Environment scene composition and first-hit queries."""

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np

from scrap_monitoring_lidar_generator.geometry.batch_intersections import (
    contains_xy,
    intersect_floor_batch,
    intersect_triangle_batch,
)
from scrap_monitoring_lidar_generator.geometry.batches import FloatArray, RayBatch
from scrap_monitoring_lidar_generator.geometry.intersections import (
    DEFAULT_MIN_DISTANCE_M,
    intersect_triangle,
    validate_distance_bounds,
)
from scrap_monitoring_lidar_generator.geometry.polygon import Polygon2
from scrap_monitoring_lidar_generator.geometry.primitives import Ray, Triangle, Vec2, Vec3
from scrap_monitoring_lidar_generator.geometry.surfaces import RaySurface

_SCENE_TOLERANCE = 1e-9


class HitKind(StrEnum):
    """Environment target hit by a ray."""

    FLOOR = "floor"
    WALL = "wall"
    SURFACE = "surface"


_NO_HIT = 0
_FLOOR_HIT = 1
_WALL_HIT = 2
_SURFACE_HIT = 3
_HIT_KIND_BY_CODE = {
    _FLOOR_HIT: HitKind.FLOOR,
    _WALL_HIT: HitKind.WALL,
    _SURFACE_HIT: HitKind.SURFACE,
}


@dataclass(frozen=True, slots=True)
class RayHit:
    """Nearest environment intersection along a ray."""

    distance_m: float
    position_m: Vec3
    kind: HitKind


@dataclass(frozen=True, slots=True)
class RayHitBatch:
    """Ordered first-hit distances and targets for a ray batch."""

    distances_m: FloatArray
    hit_kinds: tuple[HitKind | None, ...]

    def __post_init__(self) -> None:
        distances_m = np.array(self.distances_m, dtype=np.float64, copy=True)
        hit_kinds = tuple(self.hit_kinds)
        if distances_m.ndim != 1 or distances_m.shape[0] != len(hit_kinds):
            raise ValueError("ray hit batch distances and targets must have the same length")
        if not bool(
            np.all((np.isfinite(distances_m) & (distances_m >= 0.0)) | np.isposinf(distances_m))
        ):
            raise ValueError("ray hit batch distances must be non-negative or positive infinity")
        if any(
            math.isinf(float(distance_m)) != (kind is None)
            for distance_m, kind in zip(
                distances_m,
                hit_kinds,
                strict=True,
            )
        ):
            raise ValueError("only a ray without a hit may have an infinite batch distance")
        distances_m.flags.writeable = False
        object.__setattr__(self, "distances_m", distances_m)
        object.__setattr__(self, "hit_kinds", hit_kinds)


@dataclass(frozen=True, slots=True)
class EnvironmentScene:
    """Open-top environment with optional fixed and dynamic surfaces."""

    boundary: Polygon2
    floor_z_m: float
    top_z_m: float
    surface_triangles: tuple[Triangle, ...] = ()
    dynamic_surface: RaySurface | None = field(default=None, repr=False, compare=False)
    _wall_triangles: tuple[Triangle, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not math.isfinite(self.floor_z_m) or not math.isfinite(self.top_z_m):
            raise ValueError("scene heights must be finite")
        if self.top_z_m <= self.floor_z_m:
            raise ValueError("scene top must be above scene floor")

        surfaces = tuple(self.surface_triangles)
        object.__setattr__(self, "surface_triangles", surfaces)
        self._validate_surfaces(surfaces)
        self._validate_dynamic_surface()
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

        if self.dynamic_surface is not None:
            distance = self.dynamic_surface.intersect_ray(
                ray,
                min_distance_m=min_distance_m,
                max_distance_m=nearest_distance_m,
            )
            if distance is not None and (nearest_kind is None or distance < nearest_distance_m):
                nearest_distance_m = distance
                nearest_kind = HitKind.SURFACE

        if nearest_kind is None:
            return None
        return RayHit(
            distance_m=nearest_distance_m,
            position_m=ray.point_at(nearest_distance_m),
            kind=nearest_kind,
        )

    def first_hit_batch(
        self,
        rays: Iterable[Ray] | RayBatch,
        *,
        min_distance_m: float = DEFAULT_MIN_DISTANCE_M,
        max_distance_m: float = math.inf,
    ) -> RayHitBatch:
        """Return first-hit distances and targets while preserving ray order."""
        validate_distance_bounds(min_distance_m, max_distance_m)
        batch = rays if isinstance(rays, RayBatch) else RayBatch.from_rays(rays)
        nearest_distances_m = intersect_floor_batch(
            batch,
            self.boundary,
            self.floor_z_m,
            min_distance_m=min_distance_m,
            max_distance_m=max_distance_m,
        )
        hit_codes = np.where(np.isfinite(nearest_distances_m), _FLOOR_HIT, _NO_HIT).astype(np.uint8)

        for wall in self._wall_triangles:
            candidates_m = intersect_triangle_batch(
                batch,
                wall,
                min_distance_m=min_distance_m,
                max_distance_m=max_distance_m,
            )
            closer = candidates_m < nearest_distances_m
            nearest_distances_m[closer] = candidates_m[closer]
            hit_codes[closer] = _WALL_HIT

        for surface in self.surface_triangles:
            candidates_m = intersect_triangle_batch(
                batch,
                surface,
                min_distance_m=min_distance_m,
                max_distance_m=max_distance_m,
            )
            finite = np.isfinite(candidates_m)
            safe_distances_m = np.where(finite, candidates_m, 0.0)
            x_values = batch.origins_m[:, 0] + batch.directions[:, 0] * safe_distances_m
            y_values = batch.origins_m[:, 1] + batch.directions[:, 1] * safe_distances_m
            candidates_m[~contains_xy(self.boundary, x_values, y_values)] = math.inf
            closer = candidates_m < nearest_distances_m
            nearest_distances_m[closer] = candidates_m[closer]
            hit_codes[closer] = _SURFACE_HIT

        if self.dynamic_surface is not None:
            candidates_m = self.dynamic_surface.intersect_ray_batch(
                batch,
                min_distance_m=min_distance_m,
                max_distance_m=max_distance_m,
            )
            if candidates_m.shape != nearest_distances_m.shape:
                raise ValueError("dynamic surface batch result must match ray count")
            valid_candidates = (
                np.isfinite(candidates_m)
                & (candidates_m >= min_distance_m)
                & (candidates_m <= max_distance_m)
            ) | np.isposinf(candidates_m)
            if not bool(np.all(valid_candidates)):
                raise ValueError(
                    "dynamic surface batch distances must be valid or positive infinity"
                )
            closer = candidates_m < nearest_distances_m
            nearest_distances_m[closer] = candidates_m[closer]
            hit_codes[closer] = _SURFACE_HIT

        hit_kinds = tuple(
            None if int(code) == _NO_HIT else _HIT_KIND_BY_CODE[int(code)] for code in hit_codes
        )
        return RayHitBatch(nearest_distances_m, hit_kinds)

    def first_hits(
        self,
        rays: Iterable[Ray] | RayBatch,
        *,
        min_distance_m: float = DEFAULT_MIN_DISTANCE_M,
        max_distance_m: float = math.inf,
    ) -> tuple[RayHit | None, ...]:
        """Return materialized first hits for a batch in ray order."""
        batch = rays if isinstance(rays, RayBatch) else RayBatch.from_rays(rays)
        hit_batch = self.first_hit_batch(
            batch,
            min_distance_m=min_distance_m,
            max_distance_m=max_distance_m,
        )
        hits: list[RayHit | None] = []
        for index, (distance_value, kind) in enumerate(
            zip(hit_batch.distances_m, hit_batch.hit_kinds, strict=True)
        ):
            if kind is None:
                hits.append(None)
                continue
            distance_m = float(distance_value)
            position = batch.origins_m[index] + batch.directions[index] * distance_m
            hits.append(
                RayHit(
                    distance_m=distance_m,
                    position_m=Vec3(float(position[0]), float(position[1]), float(position[2])),
                    kind=kind,
                )
            )
        return tuple(hits)

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

    def _validate_dynamic_surface(self) -> None:
        surface = self.dynamic_surface
        if surface is None:
            return
        if surface.boundary != self.boundary:
            raise ValueError("dynamic surface boundary must match the scene boundary")
        if not math.isclose(
            surface.floor_z_m,
            self.floor_z_m,
            rel_tol=0.0,
            abs_tol=_SCENE_TOLERANCE,
        ) or not math.isclose(
            surface.top_z_m,
            self.top_z_m,
            rel_tol=0.0,
            abs_tol=_SCENE_TOLERANCE,
        ):
            raise ValueError("dynamic surface height bounds must match the scene bounds")

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
