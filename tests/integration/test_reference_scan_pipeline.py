"""Integration tests from environment configuration to reference scan."""

from pathlib import Path

import pytest

from scrap_monitoring_lidar_generator.configuration import (
    build_environment_scene,
    build_sensor_frame,
    load_environment,
)
from scrap_monitoring_lidar_generator.geometry import HitKind
from scrap_monitoring_lidar_generator.measurement import ReferenceScanner

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
