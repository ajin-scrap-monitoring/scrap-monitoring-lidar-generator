"""Ray intersection implementation for a regular bilinear height field."""

import math
from itertools import pairwise

import numpy as np
from numpy.typing import NDArray

from scrap_monitoring_lidar_generator.geometry.intersections import validate_distance_bounds
from scrap_monitoring_lidar_generator.geometry.polygon import Polygon2
from scrap_monitoring_lidar_generator.geometry.primitives import Ray, Vec2

type FloatArray = NDArray[np.float64]

_DISTANCE_TOLERANCE = 1e-10
_POLYNOMIAL_TOLERANCE = 1e-12


def intersect_height_field(
    ray: Ray,
    *,
    boundary: Polygon2,
    x_coordinates_m: FloatArray,
    y_coordinates_m: FloatArray,
    heights_m: FloatArray,
    cell_size_m: float,
    min_distance_m: float,
    max_distance_m: float,
) -> float | None:
    """Return the nearest ray distance to a polygon-clipped bilinear grid."""
    validate_distance_bounds(min_distance_m, max_distance_m)
    interval = _horizontal_grid_interval(
        ray,
        minimum_x_m=float(x_coordinates_m[0]),
        maximum_x_m=float(x_coordinates_m[-1]),
        minimum_y_m=float(y_coordinates_m[0]),
        maximum_y_m=float(y_coordinates_m[-1]),
        min_distance_m=min_distance_m,
        max_distance_m=max_distance_m,
    )
    if interval is None:
        return None

    entry_distance_m, exit_distance_m = interval
    segments = _grid_segments(
        ray,
        x_coordinates_m=x_coordinates_m,
        y_coordinates_m=y_coordinates_m,
        entry_distance_m=entry_distance_m,
        exit_distance_m=exit_distance_m,
    )
    for segment_start_m, segment_end_m in segments:
        probe_distance_m = _probe_distance(segment_start_m, segment_end_m)
        probe = ray.point_at(probe_distance_m)
        x_index = _cell_index(
            probe.x,
            minimum_m=float(x_coordinates_m[0]),
            cell_size_m=cell_size_m,
            cell_count=heights_m.shape[1] - 1,
        )
        y_index = _cell_index(
            probe.y,
            minimum_m=float(y_coordinates_m[0]),
            cell_size_m=cell_size_m,
            cell_count=heights_m.shape[0] - 1,
        )
        roots_m = _intersect_cell(
            ray,
            segment_start_m=segment_start_m,
            segment_end_m=segment_end_m,
            lower_x_m=float(x_coordinates_m[x_index]),
            lower_y_m=float(y_coordinates_m[y_index]),
            cell_size_m=cell_size_m,
            lower_left_z_m=float(heights_m[y_index, x_index]),
            lower_right_z_m=float(heights_m[y_index, x_index + 1]),
            upper_left_z_m=float(heights_m[y_index + 1, x_index]),
            upper_right_z_m=float(heights_m[y_index + 1, x_index + 1]),
        )
        for distance_m in roots_m:
            position = ray.point_at(distance_m)
            if boundary.contains(Vec2(position.x, position.y)):
                return distance_m
    return None


def _horizontal_grid_interval(
    ray: Ray,
    *,
    minimum_x_m: float,
    maximum_x_m: float,
    minimum_y_m: float,
    maximum_y_m: float,
    min_distance_m: float,
    max_distance_m: float,
) -> tuple[float, float] | None:
    entry_distance_m = min_distance_m
    exit_distance_m = max_distance_m
    for origin_m, direction, lower_m, upper_m in (
        (ray.origin.x, ray.direction.x, minimum_x_m, maximum_x_m),
        (ray.origin.y, ray.direction.y, minimum_y_m, maximum_y_m),
    ):
        if direction == 0.0:
            if origin_m < lower_m or origin_m > upper_m:
                return None
            continue
        first_distance_m = (lower_m - origin_m) / direction
        second_distance_m = (upper_m - origin_m) / direction
        near_distance_m = min(first_distance_m, second_distance_m)
        far_distance_m = max(first_distance_m, second_distance_m)
        entry_distance_m = max(entry_distance_m, near_distance_m)
        exit_distance_m = min(exit_distance_m, far_distance_m)
        if exit_distance_m < entry_distance_m:
            return None
    return (entry_distance_m, exit_distance_m)


def _grid_segments(
    ray: Ray,
    *,
    x_coordinates_m: FloatArray,
    y_coordinates_m: FloatArray,
    entry_distance_m: float,
    exit_distance_m: float,
) -> tuple[tuple[float, float], ...]:
    if math.isclose(
        entry_distance_m,
        exit_distance_m,
        rel_tol=0.0,
        abs_tol=_DISTANCE_TOLERANCE,
    ):
        return ((entry_distance_m, exit_distance_m),)

    breakpoints_m = [entry_distance_m, exit_distance_m]
    for origin_m, direction, coordinates_m in (
        (ray.origin.x, ray.direction.x, x_coordinates_m),
        (ray.origin.y, ray.direction.y, y_coordinates_m),
    ):
        if direction == 0.0:
            continue
        crossings_m = (coordinates_m[1:-1] - origin_m) / direction
        selected = crossings_m[(crossings_m > entry_distance_m) & (crossings_m < exit_distance_m)]
        breakpoints_m.extend(float(distance_m) for distance_m in selected)

    breakpoints_m.sort()
    unique_breakpoints_m = [breakpoints_m[0]]
    for distance_m in breakpoints_m[1:]:
        if math.isclose(
            distance_m,
            unique_breakpoints_m[-1],
            rel_tol=0.0,
            abs_tol=_DISTANCE_TOLERANCE,
        ):
            continue
        unique_breakpoints_m.append(distance_m)
    return tuple(pairwise(unique_breakpoints_m))


def _probe_distance(segment_start_m: float, segment_end_m: float) -> float:
    if math.isinf(segment_end_m):
        return segment_start_m
    return segment_start_m + 0.5 * (segment_end_m - segment_start_m)


def _cell_index(value_m: float, *, minimum_m: float, cell_size_m: float, cell_count: int) -> int:
    offset = math.floor((value_m - minimum_m) / cell_size_m)
    return min(max(offset, 0), cell_count - 1)


def _intersect_cell(
    ray: Ray,
    *,
    segment_start_m: float,
    segment_end_m: float,
    lower_x_m: float,
    lower_y_m: float,
    cell_size_m: float,
    lower_left_z_m: float,
    lower_right_z_m: float,
    upper_left_z_m: float,
    upper_right_z_m: float,
) -> tuple[float, ...]:
    segment_origin = ray.point_at(segment_start_m)
    u_start = (segment_origin.x - lower_x_m) / cell_size_m
    v_start = (segment_origin.y - lower_y_m) / cell_size_m
    u_rate = ray.direction.x / cell_size_m
    v_rate = ray.direction.y / cell_size_m

    x_slope = lower_right_z_m - lower_left_z_m
    y_slope = upper_left_z_m - lower_left_z_m
    mixed_slope = upper_right_z_m - lower_right_z_m - upper_left_z_m + lower_left_z_m
    surface_constant = (
        lower_left_z_m + x_slope * u_start + y_slope * v_start + mixed_slope * u_start * v_start
    )
    surface_linear = (
        x_slope * u_rate + y_slope * v_rate + mixed_slope * (u_start * v_rate + v_start * u_rate)
    )
    surface_quadratic = mixed_slope * u_rate * v_rate
    coefficients = (
        -surface_quadratic,
        ray.direction.z - surface_linear,
        segment_origin.z - surface_constant,
    )

    segment_length_m = segment_end_m - segment_start_m
    accepted: list[float] = []
    for offset_m in _real_roots(*coefficients):
        if offset_m < -_DISTANCE_TOLERANCE:
            continue
        if not math.isinf(segment_length_m) and offset_m > segment_length_m + _DISTANCE_TOLERANCE:
            continue
        bounded_offset_m = max(0.0, offset_m)
        if not math.isinf(segment_length_m):
            bounded_offset_m = min(segment_length_m, bounded_offset_m)
        accepted.append(segment_start_m + bounded_offset_m)
    return tuple(sorted(set(accepted)))


def _real_roots(quadratic: float, linear: float, constant: float) -> tuple[float, ...]:
    if quadratic == 0.0:
        if linear == 0.0:
            return ()
        return (-constant / linear,)

    discriminant = linear * linear - 4.0 * quadratic * constant
    discriminant_scale = max(1.0, linear * linear, abs(4.0 * quadratic * constant))
    if discriminant < 0.0:
        if discriminant < -_POLYNOMIAL_TOLERANCE * discriminant_scale:
            return ()
        discriminant = 0.0
    square_root = math.sqrt(discriminant)
    if square_root == 0.0:
        return (-linear / (2.0 * quadratic),)

    stable_term = -0.5 * (linear + math.copysign(square_root, linear))
    if stable_term == 0.0:
        return (-linear / (2.0 * quadratic),)
    return tuple(sorted((stable_term / quadratic, constant / stable_term)))
