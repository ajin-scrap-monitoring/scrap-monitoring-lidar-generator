"""Tests for sensor measurement frames."""

import math

import pytest

from scrap_monitoring_lidar_generator.geometry import SensorFrame, Vec3


@pytest.fixture
def frame() -> SensorFrame:
    return SensorFrame(
        origin_m=Vec3(1.0, 2.0, 3.0),
        u0=Vec3(0.0, 0.0, -1.0),
        u90=Vec3(1.0, 0.0, 0.0),
    )


@pytest.mark.parametrize(
    ("angle_deg", "expected"),
    [
        (0.0, Vec3(0.0, 0.0, -1.0)),
        (90.0, Vec3(1.0, 0.0, 0.0)),
        (180.0, Vec3(0.0, 0.0, 1.0)),
        (270.0, Vec3(-1.0, 0.0, 0.0)),
    ],
)
def test_maps_cardinal_sensor_angles(
    frame: SensorFrame,
    angle_deg: float,
    expected: Vec3,
) -> None:
    ray = frame.ray_at(angle_deg)

    assert ray.origin == frame.origin_m
    assert ray.direction.x == pytest.approx(expected.x, abs=1e-12)
    assert ray.direction.y == pytest.approx(expected.y, abs=1e-12)
    assert ray.direction.z == pytest.approx(expected.z, abs=1e-12)


def test_normalizes_direction_from_tolerated_frame_error() -> None:
    frame = SensorFrame(
        origin_m=Vec3(0.0, 0.0, 0.0),
        u0=Vec3(1.0000005, 0.0, 0.0),
        u90=Vec3(0.0, 0.9999995, 0.0),
    )

    assert frame.ray_at(45.0).direction.length() == pytest.approx(1.0)


@pytest.mark.parametrize(
    "angle_deg",
    [-0.1, 360.0, math.nan, math.inf, -math.inf],
)
def test_rejects_invalid_sensor_angle(frame: SensorFrame, angle_deg: float) -> None:
    with pytest.raises(ValueError, match="sensor angle"):
        frame.ray_at(angle_deg)


def test_rejects_non_orthonormal_frame() -> None:
    with pytest.raises(ValueError, match="unit vector"):
        SensorFrame(Vec3(0.0, 0.0, 0.0), Vec3(2.0, 0.0, 0.0), Vec3(0.0, 1.0, 0.0))

    with pytest.raises(ValueError, match="orthogonal"):
        SensorFrame(Vec3(0.0, 0.0, 0.0), Vec3(1.0, 0.0, 0.0), Vec3(1.0, 0.0, 0.0))
