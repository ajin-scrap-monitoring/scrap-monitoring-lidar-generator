"""Tests for environment scene first-hit behavior."""

import pytest

from scrap_monitoring_lidar_generator.geometry import (
    EnvironmentScene,
    HitKind,
    Polygon2,
    Ray,
    Triangle,
    Vec2,
    Vec3,
)


@pytest.fixture
def square_boundary() -> Polygon2:
    return Polygon2((Vec2(0.0, 0.0), Vec2(4.0, 0.0), Vec2(4.0, 4.0), Vec2(0.0, 4.0)))


def test_floor_is_first_hit_in_empty_scene(square_boundary: Polygon2) -> None:
    scene = EnvironmentScene(square_boundary, floor_z_m=0.0, top_z_m=5.0)
    ray = Ray(Vec3(2.0, 2.0, 4.0), Vec3(0.0, 0.0, -1.0))

    hit = scene.first_hit(ray)

    assert hit is not None
    assert hit.kind is HitKind.FLOOR
    assert hit.distance_m == pytest.approx(4.0)
    assert hit.position_m == Vec3(2.0, 2.0, 0.0)


def test_fixed_surface_precedes_floor(square_boundary: Polygon2) -> None:
    surface = (
        Triangle(Vec3(1.0, 1.0, 2.0), Vec3(3.0, 1.0, 2.0), Vec3(3.0, 3.0, 2.0)),
        Triangle(Vec3(1.0, 1.0, 2.0), Vec3(3.0, 3.0, 2.0), Vec3(1.0, 3.0, 2.0)),
    )
    scene = EnvironmentScene(
        square_boundary,
        floor_z_m=0.0,
        top_z_m=5.0,
        surface_triangles=surface,
    )
    ray = Ray(Vec3(2.0, 2.0, 4.0), Vec3(0.0, 0.0, -1.0))

    hit = scene.first_hit(ray)

    assert hit is not None
    assert hit.kind is HitKind.SURFACE
    assert hit.distance_m == pytest.approx(2.0)
    assert hit.position_m == Vec3(2.0, 2.0, 2.0)


def test_horizontal_ray_hits_nearest_wall(square_boundary: Polygon2) -> None:
    scene = EnvironmentScene(square_boundary, floor_z_m=0.0, top_z_m=5.0)
    ray = Ray(Vec3(1.0, 2.0, 2.0), Vec3(1.0, 0.0, 0.0))

    hit = scene.first_hit(ray)

    assert hit is not None
    assert hit.kind is HitKind.WALL
    assert hit.distance_m == pytest.approx(3.0)
    assert hit.position_m == Vec3(4.0, 2.0, 2.0)


def test_wall_origin_does_not_create_zero_distance_hit(square_boundary: Polygon2) -> None:
    scene = EnvironmentScene(square_boundary, floor_z_m=0.0, top_z_m=5.0)
    outward_ray = Ray(Vec3(0.0, 2.0, 2.0), Vec3(-1.0, 0.0, 0.0))
    inward_ray = Ray(Vec3(0.0, 2.0, 2.0), Vec3(1.0, 0.0, 0.0))

    assert scene.first_hit(outward_ray) is None
    inward_hit = scene.first_hit(inward_ray)
    assert inward_hit is not None
    assert inward_hit.distance_m == pytest.approx(4.0)


def test_open_top_has_no_intersection(square_boundary: Polygon2) -> None:
    scene = EnvironmentScene(square_boundary, floor_z_m=0.0, top_z_m=5.0)
    ray = Ray(Vec3(2.0, 2.0, 4.0), Vec3(0.0, 0.0, 1.0))

    assert scene.first_hit(ray) is None


def test_hit_outside_distance_range_is_ignored(square_boundary: Polygon2) -> None:
    scene = EnvironmentScene(square_boundary, floor_z_m=0.0, top_z_m=5.0)
    ray = Ray(Vec3(2.0, 2.0, 4.0), Vec3(0.0, 0.0, -1.0))

    assert scene.first_hit(ray, max_distance_m=3.99) is None


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [(-1.0, 10.0), (float("inf"), float("inf")), (2.0, 1.0), (0.0, float("nan"))],
)
def test_rejects_invalid_distance_range(
    square_boundary: Polygon2,
    minimum: float,
    maximum: float,
) -> None:
    scene = EnvironmentScene(square_boundary, floor_z_m=0.0, top_z_m=5.0)
    ray = Ray(Vec3(2.0, 2.0, 4.0), Vec3(0.0, 0.0, 1.0))

    with pytest.raises(ValueError):
        scene.first_hit(ray, min_distance_m=minimum, max_distance_m=maximum)


def test_surface_portion_outside_concave_boundary_is_ignored() -> None:
    boundary = Polygon2(
        (
            Vec2(0.0, 0.0),
            Vec2(4.0, 0.0),
            Vec2(4.0, 4.0),
            Vec2(2.0, 2.0),
            Vec2(0.0, 4.0),
        )
    )
    crossing_surface = (Triangle(Vec3(0.5, 3.5, 2.0), Vec3(3.5, 3.5, 2.0), Vec3(2.0, 0.5, 2.0)),)
    scene = EnvironmentScene(
        boundary,
        floor_z_m=0.0,
        top_z_m=5.0,
        surface_triangles=crossing_surface,
    )
    ray = Ray(Vec3(2.0, 3.0, 4.0), Vec3(0.0, 0.0, -1.0))

    assert scene.first_hit(ray) is None


def test_rejects_surface_outside_boundary(square_boundary: Polygon2) -> None:
    surface = (Triangle(Vec3(3.0, 1.0, 2.0), Vec3(5.0, 1.0, 2.0), Vec3(3.0, 3.0, 2.0)),)

    with pytest.raises(ValueError, match="inside the scene boundary"):
        EnvironmentScene(
            square_boundary,
            floor_z_m=0.0,
            top_z_m=5.0,
            surface_triangles=surface,
        )


def test_rejects_surface_outside_height_range(square_boundary: Polygon2) -> None:
    surface = (Triangle(Vec3(1.0, 1.0, 6.0), Vec3(3.0, 1.0, 6.0), Vec3(1.0, 3.0, 6.0)),)

    with pytest.raises(ValueError, match="between floor and top"):
        EnvironmentScene(
            square_boundary,
            floor_z_m=0.0,
            top_z_m=5.0,
            surface_triangles=surface,
        )
