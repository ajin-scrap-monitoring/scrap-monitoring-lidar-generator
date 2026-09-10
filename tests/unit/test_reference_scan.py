"""Tests for undistorted reference scans."""

import pytest

from scrap_monitoring_lidar_generator.geometry import (
    EnvironmentScene,
    HitKind,
    Polygon2,
    SensorFrame,
    Triangle,
    Vec2,
    Vec3,
)
from scrap_monitoring_lidar_generator.measurement import ReferenceScanner


@pytest.fixture
def scene() -> EnvironmentScene:
    boundary = Polygon2((Vec2(0.0, 0.0), Vec2(4.0, 0.0), Vec2(4.0, 4.0), Vec2(0.0, 4.0)))
    return EnvironmentScene(boundary, floor_z_m=0.0, top_z_m=4.0)


@pytest.fixture
def scanner() -> ReferenceScanner:
    frame = SensorFrame(
        origin_m=Vec3(2.0, 2.0, 2.0),
        u0=Vec3(0.0, 0.0, -1.0),
        u90=Vec3(1.0, 0.0, 0.0),
    )
    return ReferenceScanner(sensor_id="sensor-a", frame=frame)


def test_generates_first_hit_distances_in_measurement_order(
    scanner: ReferenceScanner,
    scene: EnvironmentScene,
) -> None:
    scan = scanner.generate(scene, [270.0, 0.0, 90.0, 180.0])

    assert scan.sensor_id == "sensor-a"
    assert [point.angle_deg for point in scan.points] == [270.0, 0.0, 90.0, 180.0]
    assert [point.distance_m for point in scan.points] == pytest.approx([2.0, 2.0, 2.0, 0.0])
    assert [point.hit_kind for point in scan.points] == [
        HitKind.WALL,
        HitKind.FLOOR,
        HitKind.WALL,
        None,
    ]


def test_preserves_arbitrary_start_angle_and_point_count(
    scanner: ReferenceScanner,
    scene: EnvironmentScene,
) -> None:
    angles = [350.0, 5.0, 20.0]

    scan = scanner.generate(scene, iter(angles))

    assert [point.angle_deg for point in scan.points] == angles
    assert len(scan.points) == 3


def test_returns_zero_when_hit_is_outside_measurable_range(
    scanner: ReferenceScanner,
    scene: EnvironmentScene,
) -> None:
    limited = ReferenceScanner(
        sensor_id=scanner.sensor_id,
        frame=scanner.frame,
        min_distance_m=0.05,
        max_distance_m=1.9,
    )

    point = limited.generate(scene, [0.0]).points[0]

    assert point.distance_m == 0.0
    assert point.hit_kind is None


def test_measure_uses_supplied_scene_state(
    scanner: ReferenceScanner,
    scene: EnvironmentScene,
) -> None:
    raised_surface = (
        Triangle(Vec3(1.0, 1.0, 1.0), Vec3(3.0, 1.0, 1.0), Vec3(3.0, 3.0, 1.0)),
        Triangle(Vec3(1.0, 1.0, 1.0), Vec3(3.0, 3.0, 1.0), Vec3(1.0, 3.0, 1.0)),
    )
    changed_scene = EnvironmentScene(
        scene.boundary,
        floor_z_m=scene.floor_z_m,
        top_z_m=scene.top_z_m,
        surface_triangles=raised_surface,
    )

    floor_point = scanner.measure(scene, 0.0)
    surface_point = scanner.measure(changed_scene, 0.0)

    assert floor_point.distance_m == pytest.approx(2.0)
    assert floor_point.hit_kind is HitKind.FLOOR
    assert surface_point.distance_m == pytest.approx(1.0)
    assert surface_point.hit_kind is HitKind.SURFACE


def test_same_input_produces_same_reference_scan(
    scanner: ReferenceScanner,
    scene: EnvironmentScene,
) -> None:
    angles = [13.0, 127.5, 301.25]

    assert scanner.generate(scene, angles) == scanner.generate(scene, angles)


def test_rejects_empty_scan(scanner: ReferenceScanner, scene: EnvironmentScene) -> None:
    with pytest.raises(ValueError, match="at least one point"):
        scanner.generate(scene, [])


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [(0.0, 30.0), (-1.0, 30.0), (1.0, 0.5), (0.05, float("inf"))],
)
def test_rejects_invalid_measurable_range(
    scanner: ReferenceScanner,
    minimum: float,
    maximum: float,
) -> None:
    with pytest.raises(ValueError):
        ReferenceScanner(
            sensor_id=scanner.sensor_id,
            frame=scanner.frame,
            min_distance_m=minimum,
            max_distance_m=maximum,
        )
