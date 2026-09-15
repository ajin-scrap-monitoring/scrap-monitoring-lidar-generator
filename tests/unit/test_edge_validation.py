"""Tests for repeatable edge release validation support."""

import json
import subprocess
from pathlib import Path

import pytest
from tests.edge.check_result import validate_result
from tests.edge.receiver import ReceiverState

from scrap_monitoring_lidar_simulator.wire import lidar_pb2

_ROOT = Path(__file__).parents[2]
_ENVIRONMENT = _ROOT / "examples" / "environment.v1.json"
_RUNNER = _ROOT / "tests" / "edge" / "run.sh"
_FAKE_DIGEST = "example.invalid/lidar-simulator@sha256:" + "0" * 64


def _frame(sensor_id: str, sequence: int = 1) -> lidar_pb2.ScanFrame:
    return lidar_pb2.ScanFrame(
        schema_version="1.0",
        edge_id="validation-edge",
        sensor_id=sensor_id,
        sequence=sequence,
        acquired_at_unix_ms=1_800_000_000_000,
        acquired_monotonic_ns=1_000_000_000,
        sdk_status="OK",
        scan_hz=10.0,
        samples=[lidar_pb2.ScanSample(angle_mdeg=0, distance_mm=1_000, quality=12)],
        instance_id=f"instance-{sensor_id}",
        config_revision="validation-r1",
    )


def _write_result_files(
    directory: Path,
    *,
    frame_loss: int = 0,
    scan_duplicates: int = 0,
) -> tuple[Path, Path, Path]:
    generator = directory / "generator.log"
    generator.write_text(
        "run_id=run-a generated=22 published=20\n"
        f"scan_stream published=20 frame_loss={frame_loss} subscribers=0\n"
        "observation=active sent=3 dropped=0 connection_failures=0\n",
        encoding="utf-8",
    )
    receiver = directory / "receiver.log"
    receiver.write_text(
        "validation_receiver="
        + json.dumps(
            {
                "environment_id": "synthetic-scrap-pit-v1",
                "expected_sensor_ids": ["lidar_1", "lidar_2"],
                "edge_id": "validation-edge",
                "config_revision": "validation-r1",
                "run_id": "run-a",
                "scan_subscriptions": {"lidar_1": 1, "lidar_2": 1},
                "scan_received": 20,
                "scan_unique": {"lidar_1": 10, "lidar_2": 10},
                "scan_duplicates": scan_duplicates,
                "scan_gaps": 0,
                "scan_points": 64_000,
                "last_sequences": {"lidar_1": 10, "lidar_2": 10},
                "instance_ids": {
                    "lidar_1": "instance-lidar_1",
                    "lidar_2": "instance-lidar_2",
                },
                "observation_headers": 1,
                "observations": 3,
                "observation_gaps": 0,
                "last_observation_sequence": 3,
                "errors": [],
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    status = directory / "status"
    status.mkdir()
    for service, sensor_id in (("lidar-driver-a", "lidar_1"), ("lidar-driver-b", "lidar_2")):
        service_directory = status / service
        service_directory.mkdir()
        (service_directory / f"{service}.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "service": service,
                    "sensor_id": sensor_id,
                    "edge_id": "validation-edge",
                    "config_revision": "validation-r1",
                    "state": "HEALTHY",
                    "reason_codes": [],
                    "instance_id": f"instance-{sensor_id}",
                }
            ),
            encoding="utf-8",
        )
    return generator, receiver, status


def test_result_checker_accepts_two_sensor_subscription(tmp_path: Path) -> None:
    generator, receiver, status = _write_result_files(tmp_path)

    report = validate_result(
        environment_path=_ENVIRONMENT,
        generator_log_path=generator,
        receiver_log_path=receiver,
        status_directory=status,
    )

    assert report == (
        "edge_validation=passed sensors=2 generated=22 published=20 received=20 observations=3"
    )


def test_result_checker_rejects_stream_loss(tmp_path: Path) -> None:
    generator, receiver, status = _write_result_files(tmp_path, frame_loss=1)

    with pytest.raises(ValueError, match="frame loss"):
        validate_result(
            environment_path=_ENVIRONMENT,
            generator_log_path=generator,
            receiver_log_path=receiver,
            status_directory=status,
        )


def test_result_checker_rejects_duplicate_scan(tmp_path: Path) -> None:
    generator, receiver, status = _write_result_files(tmp_path, scan_duplicates=1)

    with pytest.raises(ValueError, match="duplicate"):
        validate_result(
            environment_path=_ENVIRONMENT,
            generator_log_path=generator,
            receiver_log_path=receiver,
            status_directory=status,
        )


def test_receiver_state_tracks_each_subscription_lane() -> None:
    state = ReceiverState(
        "synthetic-scrap-pit-v1",
        ("lidar_1", "lidar_2"),
        "validation-edge",
        "validation-r1",
    )
    state.record_subscription("lidar_1")
    state.record_scan(_frame("lidar_1"), "lidar_1")

    assert state.scan_unique == {"lidar_1": 1, "lidar_2": 0}
    with pytest.raises(ValueError, match="subscription lane"):
        state.record_scan(_frame("lidar_2"), "lidar_1")


def test_receiver_state_requires_exactly_two_sensors() -> None:
    with pytest.raises(ValueError, match="exactly 2 unique"):
        ReceiverState("environment-a", ("lidar_1",), "edge-a", "config-a")


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--image"],
        ["--image", "lidar-simulator:latest", "--config-dir", "examples"],
        ["--image", _FAKE_DIGEST, "--config-dir", "examples", "--cpus", "0.0"],
    ],
)
def test_edge_runner_rejects_invalid_arguments(arguments: list[str]) -> None:
    completed = subprocess.run(
        [str(_RUNNER), *arguments],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2


def test_edge_runner_does_not_overwrite_result_paths(tmp_path: Path) -> None:
    (tmp_path / "generator.log").write_text("existing\n", encoding="utf-8")

    completed = subprocess.run(
        [
            str(_RUNNER),
            "--image",
            _FAKE_DIGEST,
            "--config-dir",
            "examples",
            "--output-dir",
            str(tmp_path),
        ],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "result path already exists" in completed.stderr
    assert (tmp_path / "generator.log").read_text(encoding="utf-8") == "existing\n"
