"""Tests for the ajin edge processing configuration bridge."""

import json
from pathlib import Path, PurePosixPath

import numpy as np
import pytest

from scrap_monitoring_lidar_generator.configuration import load_generator_inputs
from scrap_monitoring_lidar_generator.edge_integration import (
    ProcessingConfigError,
    build_processing_config,
)
from scrap_monitoring_lidar_generator.edge_integration.cli import main

_ROOT = Path(__file__).parents[2]
_GENERATOR_CONFIG = _ROOT / "examples" / "generator.v2.json"


def _config() -> dict[str, object]:
    return build_processing_config(
        load_generator_inputs(_GENERATOR_CONFIG),
        socket_directory=PurePosixPath("/sockets"),
        site_id="synthetic-site",
        edge_id="synthetic-edge",
        config_revision="synthetic-r1",
    )


def test_processing_config_derives_two_usable_sensor_sections() -> None:
    config = _config()

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


def test_processing_config_is_deterministic_and_preserves_base_deployment_fields() -> None:
    base = {
        "schema_version": "1.0",
        "site_id": "synthetic-site",
        "edge_id": "synthetic-edge",
        "config_revision": "synthetic-r1",
        "deployment_revision": "deployment-r1",
        "camera_id": "camera-a",
        "service_versions": {
            "lidar-driver-a": "1.0.0",
            "lidar-driver-b": "1.0.0",
            "lidar-processing": "0.1.0",
            "measurement-uplink": "0.1.0",
            "camera-edge": "0.1.0",
            "edge-orchestrator": "0.1.0",
        },
        "unrelated": {"retained": True},
    }

    first = build_processing_config(
        load_generator_inputs(_GENERATOR_CONFIG),
        socket_directory=PurePosixPath("/sockets"),
        site_id="synthetic-site",
        edge_id="synthetic-edge",
        config_revision="synthetic-r1",
        base_config=base,
        driver_service_version="0.8.0",
    )
    second = build_processing_config(
        load_generator_inputs(_GENERATOR_CONFIG),
        socket_directory=PurePosixPath("/sockets"),
        site_id="synthetic-site",
        edge_id="synthetic-edge",
        config_revision="synthetic-r1",
        base_config=base,
        driver_service_version="0.8.0",
    )

    assert first == second
    assert first["deployment_revision"] == "deployment-r1"
    assert first["camera_id"] == "camera-a"
    assert first["unrelated"] == {"retained": True}
    assert first["service_versions"] == {
        "lidar-processing": "0.1.0",
        "lidar-driver-a": "0.8.0",
        "lidar-driver-b": "0.8.0",
        "measurement-uplink": "0.1.0",
        "camera-edge": "0.1.0",
        "edge-orchestrator": "0.1.0",
    }
    assert base.get("sensors") is None


def test_processing_config_rejects_identity_mismatch() -> None:
    with pytest.raises(ProcessingConfigError, match="does not match"):
        build_processing_config(
            load_generator_inputs(_GENERATOR_CONFIG),
            socket_directory=PurePosixPath("/sockets"),
            site_id="synthetic-site",
            edge_id="synthetic-edge",
            config_revision="synthetic-r1",
            base_config={
                "schema_version": "1.0",
                "site_id": "another-site",
            },
        )


def test_processing_config_rejects_driver_identity_over_64_bytes() -> None:
    with pytest.raises(ProcessingConfigError, match="driver-compatible"):
        build_processing_config(
            load_generator_inputs(_GENERATOR_CONFIG),
            socket_directory=PurePosixPath("/sockets"),
            site_id="synthetic-site",
            edge_id="x" * 65,
            config_revision="synthetic-r1",
        )


def test_export_cli_uses_deployment_environment(tmp_path: Path) -> None:
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
