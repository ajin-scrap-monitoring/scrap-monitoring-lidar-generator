"""Tests for vectorized polygon and triangle calculations."""

import random

import numpy as np
import pytest

from scrap_monitoring_lidar_generator.geometry import (
    Polygon2,
    Ray,
    RayBatch,
    Triangle,
    Vec2,
    Vec3,
    intersect_triangle,
)
from scrap_monitoring_lidar_generator.geometry.batch_intersections import (
    contains_xy,
    intersect_floor_batch,
    intersect_triangle_batch,
)


def test_batch_polygon_contains_matches_scalar_for_concave_boundary() -> None:
    boundary = Polygon2(
        (
            Vec2(0.0, 0.0),
            Vec2(3.0, 0.0),
            Vec2(3.0, 1.0),
            Vec2(1.0, 1.0),
            Vec2(1.0, 3.0),
            Vec2(0.0, 3.0),
        )
    )
    points = (
        Vec2(0.0, 0.0),
        Vec2(0.5, 2.5),
        Vec2(2.5, 0.5),
        Vec2(1.0, 2.0),
        Vec2(2.0, 2.0),
        Vec2(-0.1, 0.5),
    )
    x_values = np.array([point.x for point in points], dtype=np.float64)
    y_values = np.array([point.y for point in points], dtype=np.float64)

    actual = contains_xy(boundary, x_values, y_values)

    np.testing.assert_array_equal(actual, [boundary.contains(point) for point in points])


def test_batch_triangle_intersection_matches_scalar() -> None:
    triangle = Triangle(Vec3(0.0, 0.0, 1.0), Vec3(3.0, 0.0, 1.5), Vec3(0.0, 3.0, 2.0))
    rng = random.Random(9204)
    rays = tuple(
        Ray(
            Vec3(rng.uniform(-1.0, 4.0), rng.uniform(-1.0, 4.0), rng.uniform(3.0, 6.0)),
            Vec3(
                rng.uniform(-0.4, 0.4),
                rng.uniform(-0.4, 0.4),
                rng.uniform(-1.0, -0.2),
            ).normalized(),
        )
        for _ in range(300)
    )
    batch = RayBatch.from_rays(rays)

    actual = intersect_triangle_batch(
        batch,
        triangle,
        min_distance_m=0.05,
        max_distance_m=20.0,
    )
    expected = tuple(
        intersect_triangle(ray, triangle, min_distance_m=0.05, max_distance_m=20.0) for ray in rays
    )

    for actual_distance_m, expected_distance_m in zip(actual, expected, strict=True):
        if expected_distance_m is None:
            assert np.isinf(actual_distance_m)
        else:
            assert actual_distance_m == pytest.approx(expected_distance_m, abs=1e-10)


def test_batch_floor_treats_nearly_horizontal_ray_as_parallel() -> None:
    boundary = Polygon2((Vec2(0.0, 0.0), Vec2(1.0, 0.0), Vec2(1.0, 1.0), Vec2(0.0, 1.0)))
    ray = Ray(Vec3(0.5, 0.5, 1.0), Vec3(1.0, 0.0, -1e-10).normalized())

    distances_m = intersect_floor_batch(
        RayBatch.from_rays((ray,)),
        boundary,
        0.0,
        min_distance_m=0.05,
        max_distance_m=20_000_000_000.0,
    )

    assert np.isinf(distances_m[0])
