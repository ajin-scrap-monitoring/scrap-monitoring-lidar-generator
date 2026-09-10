"""Regular-grid surface state with volume-preserving local updates."""

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from scrap_monitoring_lidar_generator.geometry import Polygon2, Vec2

type FloatArray = NDArray[np.float64]
type _Triangle2 = tuple[Vec2, Vec2, Vec2]

_GEOMETRY_TOLERANCE = 1e-12
_VOLUME_TOLERANCE = 1e-12
_KERNEL_WEIGHT_FLOOR = 1e-12


@dataclass(frozen=True, slots=True)
class VolumeChange:
    """Requested and realized volume change in cubic meters."""

    requested_m3: float
    applied_m3: float

    @property
    def unapplied_m3(self) -> float:
        """Return the non-negative part that could not be applied."""
        return max(0.0, self.requested_m3 - self.applied_m3)


class HeightField:
    """Mutable bilinear surface over a simple polygon."""

    __slots__ = (
        "_boundary",
        "_cell_size_m",
        "_floor_z_m",
        "_heights_m",
        "_top_z_m",
        "_volume_weights_m2",
        "_x_coordinates_m",
        "_y_coordinates_m",
    )

    def __init__(
        self,
        boundary: Polygon2,
        *,
        floor_z_m: float,
        top_z_m: float,
        cell_size_m: float,
    ) -> None:
        if not math.isfinite(floor_z_m) or not math.isfinite(top_z_m):
            raise ValueError("height field bounds must be finite")
        if top_z_m <= floor_z_m:
            raise ValueError("height field top must be above floor")
        if not math.isfinite(cell_size_m) or cell_size_m <= 0.0:
            raise ValueError("height field cell size must be a finite positive number")

        minimum_x = min(vertex.x for vertex in boundary.vertices)
        maximum_x = max(vertex.x for vertex in boundary.vertices)
        minimum_y = min(vertex.y for vertex in boundary.vertices)
        maximum_y = max(vertex.y for vertex in boundary.vertices)
        x_cell_count = max(1, math.ceil((maximum_x - minimum_x) / cell_size_m))
        y_cell_count = max(1, math.ceil((maximum_y - minimum_y) / cell_size_m))

        self._boundary = boundary
        self._floor_z_m = floor_z_m
        self._top_z_m = top_z_m
        self._cell_size_m = cell_size_m
        self._x_coordinates_m = minimum_x + cell_size_m * np.arange(
            x_cell_count + 1,
            dtype=np.float64,
        )
        self._y_coordinates_m = minimum_y + cell_size_m * np.arange(
            y_cell_count + 1,
            dtype=np.float64,
        )
        self._heights_m = np.full(
            (y_cell_count + 1, x_cell_count + 1),
            floor_z_m,
            dtype=np.float64,
        )
        self._volume_weights_m2 = _build_volume_weights(
            boundary,
            origin=Vec2(minimum_x, minimum_y),
            cell_size_m=cell_size_m,
            shape=self._heights_m.shape,
        )

    @property
    def boundary(self) -> Polygon2:
        """Return the horizontal surface boundary."""
        return self._boundary

    @property
    def floor_z_m(self) -> float:
        """Return the lower height bound."""
        return self._floor_z_m

    @property
    def top_z_m(self) -> float:
        """Return the upper height bound."""
        return self._top_z_m

    @property
    def cell_size_m(self) -> float:
        """Return the regular grid cell size."""
        return self._cell_size_m

    @property
    def shape(self) -> tuple[int, int]:
        """Return node counts in y-major array order."""
        return (self._heights_m.shape[0], self._heights_m.shape[1])

    @property
    def surface_area_m2(self) -> float:
        """Return the exact polygon area represented by the height field."""
        return abs(self._boundary.signed_area)

    @property
    def capacity_m3(self) -> float:
        """Return volume between the floor and top over the polygon."""
        return self.surface_area_m2 * (self._top_z_m - self._floor_z_m)

    @property
    def volume_m3(self) -> float:
        """Return volume between the current surface and floor."""
        elevations_m = self._heights_m - self._floor_z_m
        return float(np.sum(self._volume_weights_m2 * elevations_m))

    @property
    def fill_ratio(self) -> float:
        """Return current volume divided by total capacity."""
        ratio = self.volume_m3 / self.capacity_m3
        return min(1.0, max(0.0, ratio))

    @property
    def heights_m(self) -> FloatArray:
        """Return an independent copy of the grid node heights."""
        return self._heights_m.copy()

    def height_at(self, point: Vec2) -> float:
        """Return the bilinearly interpolated height at a point in the boundary."""
        if not self._boundary.contains(point):
            raise ValueError("height query point must lie inside the surface boundary")

        x_offset = (point.x - float(self._x_coordinates_m[0])) / self._cell_size_m
        y_offset = (point.y - float(self._y_coordinates_m[0])) / self._cell_size_m
        x_index = min(max(math.floor(x_offset), 0), self.shape[1] - 2)
        y_index = min(max(math.floor(y_offset), 0), self.shape[0] - 2)
        x_fraction = min(1.0, max(0.0, x_offset - x_index))
        y_fraction = min(1.0, max(0.0, y_offset - y_index))

        lower_left = self._heights_m[y_index, x_index]
        lower_right = self._heights_m[y_index, x_index + 1]
        upper_left = self._heights_m[y_index + 1, x_index]
        upper_right = self._heights_m[y_index + 1, x_index + 1]
        lower = lower_left + x_fraction * (lower_right - lower_left)
        upper = upper_left + x_fraction * (upper_right - upper_left)
        return float(lower + y_fraction * (upper - lower))

    def add_volume(
        self,
        volume_m3: float,
        *,
        center: Vec2,
        spread_radius_m: float,
    ) -> VolumeChange:
        """Raise the local surface while preserving all available requested volume."""
        requested_m3 = _require_volume(volume_m3)
        profile = self._build_local_profile(center, spread_radius_m)
        headroom_m = self._top_z_m - self._heights_m
        delta_m = _solve_height_delta(
            profile,
            headroom_m,
            self._volume_weights_m2,
            requested_m3,
        )
        self._heights_m += delta_m
        np.minimum(self._heights_m, self._top_z_m, out=self._heights_m)
        applied_m3 = float(np.sum(self._volume_weights_m2 * delta_m))
        return VolumeChange(requested_m3, min(requested_m3, applied_m3))

    def remove_volume(
        self,
        volume_m3: float,
        *,
        center: Vec2,
        spread_radius_m: float,
    ) -> VolumeChange:
        """Lower the local surface while preserving all available requested volume."""
        requested_m3 = _require_volume(volume_m3)
        profile = self._build_local_profile(center, spread_radius_m)
        removable_height_m = self._heights_m - self._floor_z_m
        delta_m = _solve_height_delta(
            profile,
            removable_height_m,
            self._volume_weights_m2,
            requested_m3,
        )
        self._heights_m -= delta_m
        np.maximum(self._heights_m, self._floor_z_m, out=self._heights_m)
        applied_m3 = float(np.sum(self._volume_weights_m2 * delta_m))
        return VolumeChange(requested_m3, min(requested_m3, applied_m3))

    def _build_local_profile(self, center: Vec2, spread_radius_m: float) -> FloatArray:
        if not self._boundary.contains(center):
            raise ValueError("volume change center must lie inside the surface boundary")
        if not math.isfinite(spread_radius_m) or spread_radius_m <= 0.0:
            raise ValueError("spread radius must be a finite positive number")

        x_distance_m = self._x_coordinates_m[np.newaxis, :] - center.x
        y_distance_m = self._y_coordinates_m[:, np.newaxis] - center.y
        with np.errstate(over="ignore", under="ignore"):
            squared_scaled_distance = np.square(x_distance_m / spread_radius_m) + np.square(
                y_distance_m / spread_radius_m
            )
            profile = np.exp(-0.5 * squared_scaled_distance)
        active_nodes = self._volume_weights_m2 > 0.0
        return np.where(active_nodes, np.maximum(profile, _KERNEL_WEIGHT_FLOOR), 0.0)


def _require_volume(volume_m3: float) -> float:
    if not math.isfinite(volume_m3) or volume_m3 < 0.0:
        raise ValueError("volume must be a finite non-negative number")
    return volume_m3


def _solve_height_delta(
    profile: FloatArray,
    available_height_m: FloatArray,
    volume_weights_m2: FloatArray,
    requested_m3: float,
) -> FloatArray:
    if requested_m3 == 0.0:
        return np.zeros_like(available_height_m)

    available_m3 = float(np.sum(volume_weights_m2 * available_height_m))
    if requested_m3 >= available_m3 - _VOLUME_TOLERANCE:
        return available_height_m.copy()

    active = (profile > 0.0) & (available_height_m > 0.0) & (volume_weights_m2 > 0.0)
    active_profile = profile[active]
    active_height = available_height_m[active]
    active_weights = volume_weights_m2[active]
    breakpoints = active_height / active_profile
    order = np.argsort(breakpoints)

    scale = 0.0
    accumulated_m3 = 0.0
    slope_m3 = float(np.sum(active_weights * active_profile))
    for flat_index in order:
        breakpoint = float(breakpoints[flat_index])
        next_volume_m3 = accumulated_m3 + (breakpoint - scale) * slope_m3
        if next_volume_m3 >= requested_m3:
            scale += (requested_m3 - accumulated_m3) / slope_m3
            break
        accumulated_m3 = next_volume_m3
        scale = breakpoint
        slope_m3 -= float(active_weights[flat_index] * active_profile[flat_index])
    else:
        return available_height_m.copy()

    return np.minimum(scale * profile, available_height_m)


def _build_volume_weights(
    boundary: Polygon2,
    *,
    origin: Vec2,
    cell_size_m: float,
    shape: tuple[int, int],
) -> FloatArray:
    weights_m2 = np.zeros(shape, dtype=np.float64)
    y_cell_count = shape[0] - 1
    x_cell_count = shape[1] - 1

    for triangle in _triangulate(boundary):
        minimum_x = min(vertex.x for vertex in triangle)
        maximum_x = max(vertex.x for vertex in triangle)
        minimum_y = min(vertex.y for vertex in triangle)
        maximum_y = max(vertex.y for vertex in triangle)
        first_x = max(0, math.floor((minimum_x - origin.x) / cell_size_m))
        last_x = min(x_cell_count - 1, math.floor((maximum_x - origin.x) / cell_size_m))
        first_y = max(0, math.floor((minimum_y - origin.y) / cell_size_m))
        last_y = min(y_cell_count - 1, math.floor((maximum_y - origin.y) / cell_size_m))

        for y_index in range(first_y, last_y + 1):
            lower_y = origin.y + y_index * cell_size_m
            upper_y = lower_y + cell_size_m
            for x_index in range(first_x, last_x + 1):
                lower_x = origin.x + x_index * cell_size_m
                upper_x = lower_x + cell_size_m
                clipped = _clip_to_rectangle(
                    list(triangle),
                    lower_x=lower_x,
                    upper_x=upper_x,
                    lower_y=lower_y,
                    upper_y=upper_y,
                )
                if len(clipped) < 3:
                    continue
                normalized = [
                    Vec2(
                        (point.x - lower_x) / cell_size_m,
                        (point.y - lower_y) / cell_size_m,
                    )
                    for point in clipped
                ]
                corner_weights = _bilinear_integral_weights(normalized)
                for y_offset, x_offset, weight in (
                    (0, 0, corner_weights[0]),
                    (0, 1, corner_weights[1]),
                    (1, 0, corner_weights[2]),
                    (1, 1, corner_weights[3]),
                ):
                    weights_m2[y_index + y_offset, x_index + x_offset] += (
                        weight * cell_size_m * cell_size_m
                    )

    negative_tolerance = _GEOMETRY_TOLERANCE * abs(boundary.signed_area)
    if bool(np.any(weights_m2 < -negative_tolerance)):
        raise RuntimeError("height field integration produced a negative node weight")
    np.maximum(weights_m2, 0.0, out=weights_m2)
    represented_area_m2 = float(np.sum(weights_m2))
    polygon_area_m2 = abs(boundary.signed_area)
    if represented_area_m2 <= 0.0:
        raise RuntimeError("height field integration produced no surface area")
    weights_m2 *= polygon_area_m2 / represented_area_m2
    return weights_m2


def _triangulate(boundary: Polygon2) -> tuple[_Triangle2, ...]:
    vertices = list(boundary.vertices)
    if boundary.signed_area < 0.0:
        vertices.reverse()
    vertices = _remove_collinear_vertices(vertices)

    triangles: list[_Triangle2] = []
    while len(vertices) > 3:
        for index, current in enumerate(vertices):
            previous = vertices[index - 1]
            following = vertices[(index + 1) % len(vertices)]
            if (current - previous).cross(following - current) <= _GEOMETRY_TOLERANCE:
                continue
            if any(
                _point_in_triangle(candidate, previous, current, following)
                for candidate_index, candidate in enumerate(vertices)
                if candidate_index
                not in {(index - 1) % len(vertices), index, (index + 1) % len(vertices)}
            ):
                continue
            triangles.append((previous, current, following))
            del vertices[index]
            break
        else:
            raise RuntimeError("simple polygon could not be triangulated")
    triangles.append((vertices[0], vertices[1], vertices[2]))
    return tuple(triangles)


def _remove_collinear_vertices(vertices: list[Vec2]) -> list[Vec2]:
    simplified = vertices
    changed = True
    while changed and len(simplified) > 3:
        changed = False
        result: list[Vec2] = []
        for index, current in enumerate(simplified):
            previous = simplified[index - 1]
            following = simplified[(index + 1) % len(simplified)]
            if math.isclose(
                (current - previous).cross(following - current),
                0.0,
                rel_tol=0.0,
                abs_tol=_GEOMETRY_TOLERANCE,
            ):
                changed = True
                continue
            result.append(current)
        simplified = result
    return simplified


def _point_in_triangle(point: Vec2, first: Vec2, second: Vec2, third: Vec2) -> bool:
    return (
        (second - first).cross(point - first) >= -_GEOMETRY_TOLERANCE
        and (third - second).cross(point - second) >= -_GEOMETRY_TOLERANCE
        and (first - third).cross(point - third) >= -_GEOMETRY_TOLERANCE
    )


def _clip_to_rectangle(
    polygon: list[Vec2],
    *,
    lower_x: float,
    upper_x: float,
    lower_y: float,
    upper_y: float,
) -> list[Vec2]:
    clipped = _clip_axis(polygon, axis="x", bound=lower_x, keep_greater=True)
    clipped = _clip_axis(clipped, axis="x", bound=upper_x, keep_greater=False)
    clipped = _clip_axis(clipped, axis="y", bound=lower_y, keep_greater=True)
    return _clip_axis(clipped, axis="y", bound=upper_y, keep_greater=False)


def _clip_axis(
    polygon: list[Vec2],
    *,
    axis: str,
    bound: float,
    keep_greater: bool,
) -> list[Vec2]:
    if not polygon:
        return []

    result: list[Vec2] = []
    previous = polygon[-1]
    previous_value = previous.x if axis == "x" else previous.y
    previous_inside = previous_value >= bound if keep_greater else previous_value <= bound
    for current in polygon:
        current_value = current.x if axis == "x" else current.y
        current_inside = current_value >= bound if keep_greater else current_value <= bound
        if current_inside != previous_inside:
            fraction = (bound - previous_value) / (current_value - previous_value)
            if axis == "x":
                result.append(Vec2(bound, previous.y + fraction * (current.y - previous.y)))
            else:
                result.append(Vec2(previous.x + fraction * (current.x - previous.x), bound))
        if current_inside:
            result.append(current)
        previous = current
        previous_value = current_value
        previous_inside = current_inside
    return result


def _bilinear_integral_weights(polygon: list[Vec2]) -> tuple[float, float, float, float]:
    doubled_area = 0.0
    first_moment_x = 0.0
    first_moment_y = 0.0
    mixed_moment = 0.0
    for index, current in enumerate(polygon):
        following = polygon[(index + 1) % len(polygon)]
        cross = current.x * following.y - following.x * current.y
        doubled_area += cross
        first_moment_x += (current.x + following.x) * cross
        first_moment_y += (current.y + following.y) * cross
        mixed_moment += (
            2.0 * current.x * current.y
            + current.x * following.y
            + following.x * current.y
            + 2.0 * following.x * following.y
        ) * cross

    area = 0.5 * doubled_area
    if area <= _GEOMETRY_TOLERANCE:
        return (0.0, 0.0, 0.0, 0.0)
    integral_x = first_moment_x / 6.0
    integral_y = first_moment_y / 6.0
    integral_xy = mixed_moment / 24.0
    return (
        area - integral_x - integral_y + integral_xy,
        integral_x - integral_xy,
        integral_y - integral_xy,
        integral_xy,
    )
