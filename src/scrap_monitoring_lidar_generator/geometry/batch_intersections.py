"""Vectorized geometry calculations shared by batch scene queries."""

import math

import numpy as np
from numpy.typing import NDArray

from scrap_monitoring_lidar_generator.geometry.batches import FloatArray, RayBatch
from scrap_monitoring_lidar_generator.geometry.polygon import Polygon2
from scrap_monitoring_lidar_generator.geometry.primitives import Triangle

type BoolArray = NDArray[np.bool]

_GEOMETRY_TOLERANCE = 1e-12
_FLOOR_PARALLEL_TOLERANCE = 1e-9
_PARALLEL_TOLERANCE = 1e-12
_BARYCENTRIC_TOLERANCE = 1e-12


def contains_xy(boundary: Polygon2, x_values: FloatArray, y_values: FloatArray) -> BoolArray:
    """Return whether each XY coordinate lies in or on a simple polygon."""
    if x_values.shape != y_values.shape:
        raise ValueError("polygon query coordinate arrays must have the same shape")

    inside = np.zeros(x_values.shape, dtype=np.bool)
    on_boundary = np.zeros(x_values.shape, dtype=np.bool)
    for start, end in boundary.edges:
        cross = (end.x - start.x) * (y_values - start.y) - (end.y - start.y) * (x_values - start.x)
        collinear = np.isclose(cross, 0.0, rtol=0.0, atol=_GEOMETRY_TOLERANCE)
        within_edge = (
            (x_values >= min(start.x, end.x) - _GEOMETRY_TOLERANCE)
            & (x_values <= max(start.x, end.x) + _GEOMETRY_TOLERANCE)
            & (y_values >= min(start.y, end.y) - _GEOMETRY_TOLERANCE)
            & (y_values <= max(start.y, end.y) + _GEOMETRY_TOLERANCE)
        )
        on_boundary |= collinear & within_edge

        crosses_y = (start.y > y_values) != (end.y > y_values)
        if start.y == end.y:
            continue
        intersection_x = start.x + (y_values - start.y) * (end.x - start.x) / (end.y - start.y)
        inside ^= crosses_y & (x_values < intersection_x)
    return inside | on_boundary


def intersect_floor_batch(
    rays: RayBatch,
    boundary: Polygon2,
    floor_z_m: float,
    *,
    min_distance_m: float,
    max_distance_m: float,
) -> FloatArray:
    """Return floor distances with infinity for rays without a valid hit."""
    distances_m = np.full(rays.count, math.inf, dtype=np.float64)
    z_directions = rays.directions[:, 2]
    moving = ~np.isclose(
        z_directions,
        0.0,
        rtol=0.0,
        atol=_FLOOR_PARALLEL_TOLERANCE,
    )
    candidates_m = np.zeros(rays.count, dtype=np.float64)
    np.divide(
        floor_z_m - rays.origins_m[:, 2],
        z_directions,
        out=candidates_m,
        where=moving,
    )
    valid = (
        moving
        & np.isfinite(candidates_m)
        & (candidates_m >= min_distance_m)
        & (candidates_m <= max_distance_m)
    )
    safe_distances_m = np.where(valid, candidates_m, 0.0)
    x_values = rays.origins_m[:, 0] + rays.directions[:, 0] * safe_distances_m
    y_values = rays.origins_m[:, 1] + rays.directions[:, 1] * safe_distances_m
    valid &= contains_xy(boundary, x_values, y_values)
    distances_m[valid] = candidates_m[valid]
    return distances_m


def intersect_triangle_batch(
    rays: RayBatch,
    triangle: Triangle,
    *,
    min_distance_m: float,
    max_distance_m: float,
) -> FloatArray:
    """Return double-sided triangle distances with infinity for misses."""
    first_edge = np.array(
        (
            triangle.b.x - triangle.a.x,
            triangle.b.y - triangle.a.y,
            triangle.b.z - triangle.a.z,
        ),
        dtype=np.float64,
    )
    second_edge = np.array(
        (
            triangle.c.x - triangle.a.x,
            triangle.c.y - triangle.a.y,
            triangle.c.z - triangle.a.z,
        ),
        dtype=np.float64,
    )
    direction_cross_second = np.cross(rays.directions, second_edge)
    determinants = np.sum(direction_cross_second * first_edge, axis=1)
    valid = np.abs(determinants) > _PARALLEL_TOLERANCE
    inverse_determinants = np.zeros(rays.count, dtype=np.float64)
    np.divide(1.0, determinants, out=inverse_determinants, where=valid)

    origin_offsets = rays.origins_m - np.array(
        (triangle.a.x, triangle.a.y, triangle.a.z),
        dtype=np.float64,
    )
    barycentric_b = np.sum(origin_offsets * direction_cross_second, axis=1) * inverse_determinants
    valid &= (barycentric_b >= -_BARYCENTRIC_TOLERANCE) & (
        barycentric_b <= 1.0 + _BARYCENTRIC_TOLERANCE
    )
    offset_cross_first = np.cross(origin_offsets, first_edge)
    barycentric_c = np.sum(rays.directions * offset_cross_first, axis=1) * inverse_determinants
    valid &= (barycentric_c >= -_BARYCENTRIC_TOLERANCE) & (
        barycentric_b + barycentric_c <= 1.0 + _BARYCENTRIC_TOLERANCE
    )
    candidates_m = np.sum(offset_cross_first * second_edge, axis=1) * inverse_determinants
    valid &= (
        np.isfinite(candidates_m)
        & (candidates_m >= min_distance_m)
        & (candidates_m <= max_distance_m)
    )

    distances_m = np.full(rays.count, math.inf, dtype=np.float64)
    distances_m[valid] = candidates_m[valid]
    return distances_m
