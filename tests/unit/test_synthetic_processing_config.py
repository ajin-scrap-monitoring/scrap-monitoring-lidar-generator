"""Tests for the ajin edge synthetic processing configuration bridge."""

import json
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np
import pytest

from scrap_monitoring_lidar_simulator.configuration import load_generator_inputs
from scrap_monitoring_lidar_simulator.edge_integration import (
    ProcessingConfigError,
    build_synthetic_processing_config,
)
from scrap_monitoring_lidar_simulator.edge_integration.cli import main
from scrap_monitoring_lidar_simulator.edge_integration.synthetic_processing_config import (
    _millimetres,
    _segment_is_inside_boundary,
)
from scrap_monitoring_lidar_simulator.geometry import Polygon2, Vec2

_ROOT = Path(__file__).parents[2]
_GENERATOR_CONFIG = _ROOT / "examples" / "generator.v2.json"


def _config() -> dict[str, Any]:
    return build_synthetic_processing_config(
        load_generator_inputs(_GENERATOR_CONFIG),
        socket_directory=PurePosixPath("/sockets"),
        site_id="synthetic-site",
        edge_id="synthetic-edge",
        config_revision="synthetic-r1",
    )


def test_synthetic_processing_config_derives_two_usable_sensor_sections() -> None:
    config = _config()

    assert set(config) == {
        "schema_version",
        "site_id",
        "edge_id",
        "config_revision",
        "allow_demo_calibration",
        "calibration",
        "processing",
        "sensors",
    }
    assert config["schema_version"] == "1.0"
    assert config["allow_demo_calibration"] is True
    assert config["calibration"] == {
        "version": "synthetic-scrap-pit-v1",
        "demo": True,
        "fusion_map": [[0.0, 0.0], [1.0, 1.0]],
        "single_sensor_maps": {},
    }
    sensors = config["sensors"]
    assert isinstance(sensors, list)
    assert [sensor["sensor_id"] for sensor in sensors] == ["lidar_1", "lidar_2"]
    assert [sensor["endpoint"] for sensor in sensors] == [
        "unix:/sockets/lidar_1.sock",
        "unix:/sockets/lidar_2.sock",
    ]
    assert [sensor["sample_filter"]["angle_interval_mdeg"] for sensor in sensors] == [
        [270_000, 360_000],
        [0, 90_000],
    ]
    assert [sensor["calibration"]["roi_x_mm"] for sensor in sensors] == [
        [500, 4_000],
        [0, 3_900],
    ]
    assert [len(sensor["calibration"]["bottom_mm"]) for sensor in sensors] == [70, 78]
    for sensor in sensors:
        rotation = np.asarray(sensor["calibration"]["rotation"], dtype=np.float64)
        assert np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-6)
        assert np.linalg.det(rotation) == pytest.approx(1.0)


def test_synthetic_processing_config_is_deterministic() -> None:
    assert _config() == _config()


def test_synthetic_processing_transform_preserves_rays() -> None:
    inputs = load_generator_inputs(_GENERATOR_CONFIG)
    processing_sensors = {sensor["sensor_id"]: sensor for sensor in _config()["sensors"]}

    for sensor in inputs.environment.sensors:
        calibration = processing_sensors[sensor.sensor_id]["calibration"]
        rotation = np.asarray(calibration["rotation"], dtype=np.float64)
        translation = np.asarray(calibration["translation_mm"], dtype=np.float64)
        u0 = np.asarray(sensor.u0, dtype=np.float64)
        u90 = np.asarray(sensor.u90, dtype=np.float64)
        origin = np.asarray(sensor.p0_m, dtype=np.float64) * 1_000.0
        section_axes = np.vstack(
            (
                -u90,
                np.cross(np.asarray((0.0, 0.0, 1.0)), -u90),
                np.asarray((0.0, 0.0, 1.0)),
            )
        )
        for angle_deg in (0.0, 30.0, 90.0, 180.0, 270.0, 330.0):
            angle_rad = np.deg2rad(angle_deg)
            distance_mm = 1_234.5
            world_point = origin + distance_mm * (np.cos(angle_rad) * u0 + np.sin(angle_rad) * u90)
            sdk_point = np.asarray(
                (
                    distance_mm * np.cos(-angle_rad),
                    distance_mm * np.sin(-angle_rad),
                    0.0,
                )
            )

            assert rotation @ sdk_point + translation == pytest.approx(
                section_axes @ world_point,
                abs=1e-9,
            )


def test_synthetic_processing_config_rejects_driver_identity_over_64_bytes() -> None:
    with pytest.raises(ProcessingConfigError, match="driver-compatible"):
        build_synthetic_processing_config(
            load_generator_inputs(_GENERATOR_CONFIG),
            socket_directory=PurePosixPath("/sockets"),
            site_id="synthetic-site",
            edge_id="x" * 65,
            config_revision="synthetic-r1",
        )


def test_synthetic_processing_config_rejects_an_overlong_calibration_version() -> None:
    inputs = load_generator_inputs(_GENERATOR_CONFIG)
    inputs = replace(inputs, environment=replace(inputs.environment, environment_id="x" * 129))
    with pytest.raises(ProcessingConfigError, match="calibration version"):
        build_synthetic_processing_config(
            inputs,
            socket_directory=PurePosixPath("/sockets"),
            site_id="synthetic-site",
            edge_id="synthetic-edge",
            config_revision="synthetic-r1",
        )


def test_section_membership_detects_a_narrow_concave_gap() -> None:
    boundary = Polygon2(
        tuple(
            Vec2(*point)
            for point in (
                (0.0, 0.0),
                (4.0, 0.0),
                (4.0, 2.011),
                (3.0, 2.011),
                (3.0, 2.019),
                (4.0, 2.019),
                (4.0, 4.0),
                (0.0, 4.0),
            )
        )
    )
    assert not _segment_is_inside_boundary(Vec2(3.5, 0.0), Vec2(3.5, 4.0), boundary)
    assert _segment_is_inside_boundary(Vec2(2.5, 0.0), Vec2(2.5, 4.0), boundary)


def test_millimetres_rejects_the_exclusive_i64_upper_bound() -> None:
    with pytest.raises(ProcessingConfigError, match="integer millimetres"):
        _millimetres(9_223_372_036_854_776.0)
    assert _millimetres(-9_223_372_036_854_776.0) == -(1 << 63)


def test_export_cli_uses_environment_identity_values(tmp_path: Path) -> None:
    output = tmp_path / "processing.json"

    assert (
        main(
            ["--output", str(output)],
            environment={
                "SCRAP_LIDAR_GENERATOR_CONFIG": str(_GENERATOR_CONFIG),
                "SITE_ID": "synthetic-site",
                "EDGE_ID": "synthetic-edge",
                "CONFIG_REVISION": "synthetic-r1",
            },
        )
        == 0
    )
    value = json.loads(output.read_text(encoding="utf-8"))
    assert value == _config()


def test_export_cli_reports_missing_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(
            ["--generator-config", str(_GENERATOR_CONFIG), "--output", str(tmp_path / "x")],
            environment={},
        )
        == 2
    )
    assert "SITE_ID" in capsys.readouterr().err
