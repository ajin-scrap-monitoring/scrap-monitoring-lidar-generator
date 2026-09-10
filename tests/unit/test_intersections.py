"""Tests for ray and triangle intersections."""

import math

import pytest

from scrap_monitoring_lidar_generator.geometry import Ray, Triangle, Vec3, intersect_triangle


@pytest.fixture
def horizontal_triangle() -> Triangle:
    return Triangle(
        Vec3(-1.0, -1.0, 0.0),
        Vec3(1.0, -1.0, 0.0),
        Vec3(0.0, 1.0, 0.0),
    )


def test_intersects_triangle_from_both_sides(horizontal_triangle: Triangle) -> None:
    downward = Ray(Vec3(0.0, 0.0, 2.0), Vec3(0.0, 0.0, -1.0))
    upward = Ray(Vec3(0.0, 0.0, -2.0), Vec3(0.0, 0.0, 1.0))

    assert intersect_triangle(downward, horizontal_triangle) == pytest.approx(2.0)
    assert intersect_triangle(upward, horizontal_triangle) == pytest.approx(2.0)


def test_intersects_triangle_edge(horizontal_triangle: Triangle) -> None:
    ray = Ray(Vec3(0.0, -1.0, 1.0), Vec3(0.0, 0.0, -1.0))

    assert intersect_triangle(ray, horizontal_triangle) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "ray",
    [
        Ray(Vec3(2.0, 0.0, 1.0), Vec3(0.0, 0.0, -1.0)),
        Ray(Vec3(0.0, 0.0, 1.0), Vec3(1.0, 0.0, 0.0)),
        Ray(Vec3(0.0, 0.0, -1.0), Vec3(0.0, 0.0, -1.0)),
        Ray(Vec3(0.0, 0.0, 0.0), Vec3(0.0, 0.0, 1.0)),
    ],
)
def test_returns_none_without_positive_intersection(
    ray: Ray, horizontal_triangle: Triangle
) -> None:
    assert intersect_triangle(ray, horizontal_triangle) is None


def test_applies_distance_range(horizontal_triangle: Triangle) -> None:
    ray = Ray(Vec3(0.0, 0.0, 2.0), Vec3(0.0, 0.0, -1.0))

    assert intersect_triangle(ray, horizontal_triangle, max_distance_m=1.99) is None
    assert intersect_triangle(ray, horizontal_triangle, max_distance_m=2.0) == pytest.approx(2.0)


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [(-1.0, 1.0), (math.inf, math.inf), (1.0, 0.5), (0.0, math.nan)],
)
def test_rejects_invalid_distance_bounds(
    minimum: float, maximum: float, horizontal_triangle: Triangle
) -> None:
    ray = Ray(Vec3(0.0, 0.0, 2.0), Vec3(0.0, 0.0, -1.0))

    with pytest.raises(ValueError):
        intersect_triangle(
            ray,
            horizontal_triangle,
            min_distance_m=minimum,
            max_distance_m=maximum,
        )
