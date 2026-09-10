"""Integration tests from environment configuration to reference scan."""

from pathlib import Path

import pytest

from scrap_monitoring_lidar_generator.configuration import (
    build_environment_scene,
    build_sensor_frame,
    load_environment,
)
from scrap_monitoring_lidar_generator.geometry import HitKind, Polygon2, Vec2
from scrap_monitoring_lidar_generator.measurement import ReferenceScanner
from scrap_monitoring_lidar_generator.scenario import HeightField

_ROOT = Path(__file__).parents[2]


def test_synthetic_environment_produces_reference_scan() -> None:
    environment = load_environment(_ROOT / "examples" / "environment.v1.json")
    sensor = environment.sensors[0]
    scene = build_environment_scene(environment)
    scanner = ReferenceScanner(
        sensor_id=sensor.sensor_id,
        frame=build_sensor_frame(sensor),
    )

    scan = scanner.generate(scene, [270.0, 0.0, 90.0, 180.0])

    assert [point.distance_m for point in scan.points] == pytest.approx([4.0, 3.0, 4.0, 0.0])
    assert [point.hit_kind for point in scan.points] == [
        HitKind.WALL,
        HitKind.FLOOR,
        HitKind.WALL,
        None,
    ]


def test_reference_scan_observes_dynamic_height_field() -> None:
    environment = load_environment(_ROOT / "examples" / "environment.v1.json")
    sensor = environment.sensors[0]
    boundary = Polygon2(tuple(Vec2(x, y) for x, y in environment.boundary_xy_m))
    surface = HeightField(
        boundary,
        floor_z_m=environment.floor_z_m,
        top_z_m=environment.top_z_m,
        cell_size_m=0.25,
    )
    scene = build_environment_scene(environment, dynamic_surface=surface)
    scanner = ReferenceScanner(sensor_id=sensor.sensor_id, frame=build_sensor_frame(sensor))

    empty_distance_m = scanner.measure(scene, 0.0).distance_m
    surface.add_volume(8.0, center=Vec2(4.0, 3.0), spread_radius_m=1.0)
    filled_point = scanner.measure(scene, 0.0)

    assert filled_point.hit_kind is HitKind.SURFACE
    assert 0.0 < filled_point.distance_m < empty_distance_m
