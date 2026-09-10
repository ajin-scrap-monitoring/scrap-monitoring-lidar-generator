"""Tests for vector, ray, and triangle primitives."""

import math

import pytest

from scrap_monitoring_lidar_generator.geometry import Ray, Triangle, Vec2, Vec3


def test_vec3_operations() -> None:
    first = Vec3(1.0, 2.0, 3.0)
    second = Vec3(-2.0, 0.5, 1.0)

    assert first + second == Vec3(-1.0, 2.5, 4.0)
    assert first - second == Vec3(3.0, 1.5, 2.0)
    assert first.dot(second) == pytest.approx(2.0)
    assert first.cross(second) == Vec3(0.5, -7.0, 4.5)
    assert 2.0 * first == Vec3(2.0, 4.0, 6.0)


def test_normalizes_vec3() -> None:
    result = Vec3(3.0, 0.0, 4.0).normalized()

    assert result == Vec3(0.6, 0.0, 0.8)
    assert result.length() == pytest.approx(1.0)


def test_normalizes_large_finite_vector_without_overflow() -> None:
    result = Vec3(1e308, 1e308, 0.0).normalized()

    assert result.length() == pytest.approx(1.0)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_rejects_non_finite_vector(value: float) -> None:
    with pytest.raises(ValueError, match="must be finite"):
        Vec2(value, 0.0)


def test_rejects_zero_vector_normalization() -> None:
    with pytest.raises(ValueError, match="zero-length"):
        Vec3(0.0, 0.0, 0.0).normalized()


def test_ray_uses_physical_distance() -> None:
    ray = Ray(Vec3(1.0, 2.0, 3.0), Vec3(0.0, 0.0, -1.0))

    assert ray.point_at(2.5) == Vec3(1.0, 2.0, 0.5)


def test_rejects_non_unit_ray_direction() -> None:
    with pytest.raises(ValueError, match="unit vector"):
        Ray(Vec3(0.0, 0.0, 0.0), Vec3(0.0, 0.0, -2.0))


def test_rejects_degenerate_triangle() -> None:
    with pytest.raises(ValueError, match="non-degenerate"):
        Triangle(Vec3(0.0, 0.0, 0.0), Vec3(1.0, 0.0, 0.0), Vec3(2.0, 0.0, 0.0))
