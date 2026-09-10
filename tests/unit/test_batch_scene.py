"""Tests for batch first-hit scene queries."""

import random

import pytest

from scrap_monitoring_lidar_generator.geometry import (
    EnvironmentScene,
    Polygon2,
    Ray,
    RayBatch,
    Triangle,
    Vec2,
    Vec3,
)
from scrap_monitoring_lidar_generator.scenario import HeightField


def test_batch_scene_matches_scalar_first_hits() -> None:
    boundary = Polygon2((Vec2(0.0, 0.0), Vec2(4.0, 0.0), Vec2(4.0, 4.0), Vec2(0.0, 4.0)))
    fixed_surface = (
        Triangle(Vec3(0.5, 0.5, 3.0), Vec3(1.5, 0.5, 3.0), Vec3(1.5, 1.5, 3.0)),
        Triangle(Vec3(0.5, 0.5, 3.0), Vec3(1.5, 1.5, 3.0), Vec3(0.5, 1.5, 3.0)),
    )
    dynamic_surface = HeightField(
        boundary,
        floor_z_m=0.0,
        top_z_m=5.0,
        cell_size_m=0.2,
    )
    dynamic_surface.add_volume(20.0, center=Vec2(2.8, 2.4), spread_radius_m=0.7)
    scene = EnvironmentScene(
        boundary,
        floor_z_m=0.0,
        top_z_m=5.0,
        surface_triangles=fixed_surface,
        dynamic_surface=dynamic_surface,
    )
    rng = random.Random(19021)
    rays = [
        Ray(
            Vec3(rng.uniform(-1.0, 5.0), rng.uniform(-1.0, 5.0), rng.uniform(2.0, 7.0)),
            Vec3(
                rng.uniform(-1.0, 1.0),
                rng.uniform(-1.0, 1.0),
                rng.uniform(-1.0, 1.0),
            ).normalized(),
        )
        for _ in range(500)
    ]
    rays.extend(
        (
            Ray(Vec3(2.0, 2.0, 6.0), Vec3(0.0, 0.0, -1.0)),
            Ray(Vec3(0.0, 2.0, 2.0), Vec3(1.0, 0.0, 0.0)),
            Ray(Vec3(2.0, 2.0, 6.0), Vec3(0.0, 0.0, 1.0)),
        )
    )
    expected = tuple(scene.first_hit(ray, min_distance_m=0.05, max_distance_m=20.0) for ray in rays)

    actual = scene.first_hits(
        RayBatch.from_rays(rays),
        min_distance_m=0.05,
        max_distance_m=20.0,
    )

    for actual_hit, expected_hit in zip(actual, expected, strict=True):
        if expected_hit is None:
            assert actual_hit is None
            continue
        assert actual_hit is not None
        assert actual_hit.kind is expected_hit.kind
        assert actual_hit.distance_m == pytest.approx(expected_hit.distance_m, abs=1e-9)
        assert actual_hit.position_m.x == pytest.approx(expected_hit.position_m.x, abs=1e-9)
        assert actual_hit.position_m.y == pytest.approx(expected_hit.position_m.y, abs=1e-9)
        assert actual_hit.position_m.z == pytest.approx(expected_hit.position_m.z, abs=1e-9)


def test_empty_batch_produces_no_hits() -> None:
    boundary = Polygon2((Vec2(0.0, 0.0), Vec2(1.0, 0.0), Vec2(1.0, 1.0), Vec2(0.0, 1.0)))
    scene = EnvironmentScene(boundary, floor_z_m=0.0, top_z_m=2.0)

    assert scene.first_hits(()) == ()
