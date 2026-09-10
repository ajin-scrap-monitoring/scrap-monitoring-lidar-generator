"""Ray intersection calculations."""

import math

from scrap_monitoring_lidar_generator.geometry.primitives import Ray, Triangle

DEFAULT_MIN_DISTANCE_M = 1e-9
_PARALLEL_TOLERANCE = 1e-12
_BARYCENTRIC_TOLERANCE = 1e-12


def intersect_triangle(
    ray: Ray,
    triangle: Triangle,
    *,
    min_distance_m: float = DEFAULT_MIN_DISTANCE_M,
    max_distance_m: float = math.inf,
) -> float | None:
    """Return the nearest allowed ray distance to a double-sided triangle."""
    validate_distance_bounds(min_distance_m, max_distance_m)

    edge_ab = triangle.b - triangle.a
    edge_ac = triangle.c - triangle.a
    direction_cross_ac = ray.direction.cross(edge_ac)
    determinant = edge_ab.dot(direction_cross_ac)
    if math.isclose(determinant, 0.0, rel_tol=0.0, abs_tol=_PARALLEL_TOLERANCE):
        return None

    inverse_determinant = 1.0 / determinant
    origin_offset = ray.origin - triangle.a
    barycentric_b = inverse_determinant * origin_offset.dot(direction_cross_ac)
    if barycentric_b < -_BARYCENTRIC_TOLERANCE or barycentric_b > 1 + _BARYCENTRIC_TOLERANCE:
        return None

    offset_cross_ab = origin_offset.cross(edge_ab)
    barycentric_c = inverse_determinant * ray.direction.dot(offset_cross_ab)
    if barycentric_c < -_BARYCENTRIC_TOLERANCE:
        return None
    if barycentric_b + barycentric_c > 1 + _BARYCENTRIC_TOLERANCE:
        return None

    distance_m = inverse_determinant * edge_ac.dot(offset_cross_ab)
    if distance_m < min_distance_m or distance_m > max_distance_m:
        return None
    return distance_m


def validate_distance_bounds(min_distance_m: float, max_distance_m: float) -> None:
    """Validate an inclusive ray distance interval."""
    if not math.isfinite(min_distance_m) or min_distance_m < 0:
        raise ValueError("minimum distance must be a finite non-negative number")
    if math.isnan(max_distance_m) or max_distance_m < min_distance_m:
        raise ValueError("maximum distance must be at least the minimum distance")
