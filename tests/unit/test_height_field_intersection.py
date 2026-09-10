"""Tests for ray intersection with a bilinear height field."""

import pytest

from scrap_monitoring_lidar_generator.geometry import (
    EnvironmentScene,
    HitKind,
    Polygon2,
    Ray,
    Vec2,
    Vec3,
)
from scrap_monitoring_lidar_generator.scenario import HeightField


@pytest.fixture
def square_boundary() -> Polygon2:
    return Polygon2((Vec2(0.0, 0.0), Vec2(2.0, 0.0), Vec2(2.0, 2.0), Vec2(0.0, 2.0)))


def _flat_surface(boundary: Polygon2, *, height_m: float) -> HeightField:
    surface = HeightField(
        boundary,
        floor_z_m=0.0,
        top_z_m=height_m,
        cell_size_m=0.25,
    )
    surface.add_volume(
        surface.capacity_m3,
        center=Vec2(1.0, 1.0),
        spread_radius_m=0.5,
    )
    return surface


def test_vertical_ray_hits_flat_height_field(square_boundary: Polygon2) -> None:
    surface = _flat_surface(square_boundary, height_m=2.0)
    ray = Ray(Vec3(1.0, 1.0, 5.0), Vec3(0.0, 0.0, -1.0))

    assert surface.intersect_ray(ray) == pytest.approx(3.0)


def test_angled_ray_crosses_multiple_cells_before_hit(square_boundary: Polygon2) -> None:
    surface = _flat_surface(square_boundary, height_m=2.0)
    direction = Vec3(1.0, 0.0, -0.5).normalized()
    ray = Ray(Vec3(-1.0, 1.0, 3.0), direction)

    expected_distance_m = (3.0 - 2.0) / -direction.z

    assert surface.intersect_ray(ray) == pytest.approx(expected_distance_m)


def test_vertical_ray_matches_interpolated_local_height(square_boundary: Polygon2) -> None:
    surface = HeightField(
        square_boundary,
        floor_z_m=0.0,
        top_z_m=3.0,
        cell_size_m=0.25,
    )
    surface.add_volume(2.0, center=Vec2(0.75, 1.25), spread_radius_m=0.4)
    point = Vec2(0.63, 1.14)
    ray = Ray(Vec3(point.x, point.y, 4.0), Vec3(0.0, 0.0, -1.0))

    assert surface.intersect_ray(ray) == pytest.approx(4.0 - surface.height_at(point))


def test_angled_ray_matches_numerical_reference(square_boundary: Polygon2) -> None:
    surface = HeightField(
        square_boundary,
        floor_z_m=0.0,
        top_z_m=3.0,
        cell_size_m=0.2,
    )
    surface.add_volume(1.7, center=Vec2(1.25, 1.0), spread_radius_m=0.45)
    ray = Ray(Vec3(0.2, 1.0, 3.5), Vec3(0.4, 0.0, -1.0).normalized())
    expected_distance_m = _numerical_reference(surface, ray, maximum_m=4.0)

    assert surface.intersect_ray(ray, max_distance_m=4.0) == pytest.approx(
        expected_distance_m,
        abs=1e-9,
    )


def test_ray_over_concave_gap_has_no_surface_hit() -> None:
    boundary = Polygon2(
        (
            Vec2(0.0, 0.0),
            Vec2(2.0, 0.0),
            Vec2(2.0, 1.0),
            Vec2(1.0, 1.0),
            Vec2(1.0, 2.0),
            Vec2(0.0, 2.0),
        )
    )
    surface = HeightField(boundary, floor_z_m=0.0, top_z_m=2.0, cell_size_m=0.25)
    surface.add_volume(
        surface.capacity_m3,
        center=Vec2(0.5, 0.5),
        spread_radius_m=0.5,
    )
    ray = Ray(Vec3(1.5, 1.5, 4.0), Vec3(0.0, 0.0, -1.0))

    assert surface.intersect_ray(ray) is None


def test_ray_outside_distance_range_is_ignored(square_boundary: Polygon2) -> None:
    surface = _flat_surface(square_boundary, height_m=2.0)
    ray = Ray(Vec3(1.0, 1.0, 5.0), Vec3(0.0, 0.0, -1.0))

    assert surface.intersect_ray(ray, max_distance_m=2.99) is None
    assert surface.intersect_ray(ray, min_distance_m=3.01) is None


def test_upward_and_coplanar_rays_have_no_hit(square_boundary: Polygon2) -> None:
    surface = _flat_surface(square_boundary, height_m=2.0)

    assert surface.intersect_ray(Ray(Vec3(1.0, 1.0, 3.0), Vec3(0.0, 0.0, 1.0))) is None
    assert surface.intersect_ray(Ray(Vec3(0.5, 1.0, 2.0), Vec3(1.0, 0.0, 0.0))) is None


def test_scene_observes_height_field_updates(square_boundary: Polygon2) -> None:
    surface = HeightField(
        square_boundary,
        floor_z_m=0.0,
        top_z_m=3.0,
        cell_size_m=0.25,
    )
    scene = EnvironmentScene(
        square_boundary,
        floor_z_m=0.0,
        top_z_m=3.0,
        dynamic_surface=surface,
    )
    ray = Ray(Vec3(1.0, 1.0, 4.0), Vec3(0.0, 0.0, -1.0))

    empty_hit = scene.first_hit(ray)
    surface.add_volume(1.5, center=Vec2(1.0, 1.0), spread_radius_m=0.4)
    filled_hit = scene.first_hit(ray)

    assert empty_hit is not None
    assert empty_hit.kind is HitKind.FLOOR
    assert filled_hit is not None
    assert filled_hit.kind is HitKind.SURFACE
    assert filled_hit.distance_m < empty_hit.distance_m


def test_scene_rejects_dynamic_surface_with_different_domain(square_boundary: Polygon2) -> None:
    other_boundary = Polygon2((Vec2(0.0, 0.0), Vec2(1.0, 0.0), Vec2(1.0, 1.0), Vec2(0.0, 1.0)))
    different_boundary = HeightField(
        other_boundary,
        floor_z_m=0.0,
        top_z_m=3.0,
        cell_size_m=0.25,
    )
    different_height = HeightField(
        square_boundary,
        floor_z_m=0.0,
        top_z_m=2.0,
        cell_size_m=0.25,
    )

    with pytest.raises(ValueError, match="boundary"):
        EnvironmentScene(
            square_boundary,
            floor_z_m=0.0,
            top_z_m=3.0,
            dynamic_surface=different_boundary,
        )
    with pytest.raises(ValueError, match="height bounds"):
        EnvironmentScene(
            square_boundary,
            floor_z_m=0.0,
            top_z_m=3.0,
            dynamic_surface=different_height,
        )


def _numerical_reference(surface: HeightField, ray: Ray, *, maximum_m: float) -> float:
    def difference(distance_m: float) -> float:
        position = ray.point_at(distance_m)
        return position.z - surface.height_at(Vec2(position.x, position.y))

    previous_distance_m = 0.0
    previous_difference = difference(previous_distance_m)
    for index in range(1, 4001):
        distance_m = maximum_m * index / 4000
        position = ray.point_at(distance_m)
        point = Vec2(position.x, position.y)
        if not surface.boundary.contains(point):
            continue
        current_difference = difference(distance_m)
        if previous_difference > 0.0 and current_difference <= 0.0:
            lower_m = previous_distance_m
            upper_m = distance_m
            for _ in range(60):
                middle_m = 0.5 * (lower_m + upper_m)
                if difference(middle_m) > 0.0:
                    lower_m = middle_m
                else:
                    upper_m = middle_m
            return 0.5 * (lower_m + upper_m)
        previous_distance_m = distance_m
        previous_difference = current_difference
    raise AssertionError("reference ray did not cross the surface")
