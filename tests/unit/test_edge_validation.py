"""Tests for the repeatable edge release validation support."""

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from tests.edge.check_result import validate_result
from tests.edge.receiver import ReceiverState

from scrap_monitoring_lidar_generator.configuration import load_environment
from scrap_monitoring_lidar_generator.measurement import MeasuredScan
from scrap_monitoring_lidar_generator.transport import ScanMessage

_ROOT = Path(__file__).parents[2]
_ENVIRONMENT = _ROOT / "examples" / "environment.v1.json"
_RUNNER = _ROOT / "tests" / "edge" / "run.sh"
_FAKE_DIGEST = "example.invalid/lidar-generator@sha256:" + "0" * 64


def _write_result_logs(
    directory: Path,
    *,
    expired: int = 0,
    scan_duplicates: int = 0,
) -> tuple[Path, Path]:
    generator_log = directory / "generator.log"
    generator_log.write_text(
        "\n".join(
            (
                "run_id=run-a generated=20 acknowledged=19 pending=1",
                "transport enqueued=20 sent=20 acknowledged=19 rejected=0 "
                f"expired={expired} capacity_discarded=0 oversized=0 "
                "connection_failures=0 pending_frames=1 pending_bytes=64000",
                "observation=receiver:9100 sent=3 dropped=0 connection_failures=0",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    receiver_log = directory / "receiver.log"
    receiver_log.write_text(
        "validation_receiver="
        + json.dumps(
            {
                "environment_id": "synthetic-scrap-pit-v1",
                "expected_sensor_ids": ["lidar_1", "lidar_2"],
                "run_id": "run-a",
                "scan_connections": {"lidar_1": 1, "lidar_2": 1},
                "scan_received": 20,
                "scan_unique": {"lidar_1": 10, "lidar_2": 10},
                "scan_duplicates": scan_duplicates,
                "scan_gaps": 0,
                "scan_points": 64000,
                "last_scan_ids": {"lidar_1": 10, "lidar_2": 10},
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
    return generator_log, receiver_log


def test_result_checker_accepts_two_sensor_delivery_with_one_in_flight_frame(
    tmp_path: Path,
) -> None:
    generator_log, receiver_log = _write_result_logs(tmp_path)

    report = validate_result(
        environment_path=_ENVIRONMENT,
        generator_log_path=generator_log,
        receiver_log_path=receiver_log,
        max_pending_frames=2,
    )

    assert report == (
        "edge_validation=passed sensors=2 generated=20 acknowledged=19 pending=1 observations=3"
    )


def test_result_checker_rejects_transport_loss(tmp_path: Path) -> None:
    generator_log, receiver_log = _write_result_logs(tmp_path, expired=1)

    with pytest.raises(ValueError, match="rejection, discard or connection failure"):
        validate_result(
            environment_path=_ENVIRONMENT,
            generator_log_path=generator_log,
            receiver_log_path=receiver_log,
            max_pending_frames=2,
        )


def test_result_checker_rejects_duplicate_scan(tmp_path: Path) -> None:
    generator_log, receiver_log = _write_result_logs(tmp_path, scan_duplicates=1)

    with pytest.raises(ValueError, match="duplicate scan"):
        validate_result(
            environment_path=_ENVIRONMENT,
            generator_log_path=generator_log,
            receiver_log_path=receiver_log,
            max_pending_frames=2,
        )


def test_receiver_state_tracks_each_sensor_and_rejects_mixed_lane() -> None:
    environment = load_environment(_ENVIRONMENT)
    state = ReceiverState(
        environment.environment_id,
        tuple(sensor.sensor_id for sensor in environment.sensors),
    )
    lidar_1 = ScanMessage(
        environment_id=environment.environment_id,
        run_id="run-a",
        scan_id=1,
        captured_at=123,
        measured_scan=MeasuredScan(
            sensor_id="lidar_1",
            angles_deg=np.array([0.0]),
            distances_m=np.array([1.0]),
            qualities=np.array([48], dtype=np.uint8),
        ),
    )
    lidar_2 = ScanMessage(
        environment_id=environment.environment_id,
        run_id="run-a",
        scan_id=1,
        captured_at=123,
        measured_scan=MeasuredScan(
            sensor_id="lidar_2",
            angles_deg=np.array([0.0]),
            distances_m=np.array([1.0]),
            qualities=np.array([48], dtype=np.uint8),
        ),
    )

    lane = state.record_scan(lidar_1, None)
    assert lane == "lidar_1"
    assert state.scan_unique == {"lidar_1": 1, "lidar_2": 0}
    with pytest.raises(ValueError, match="multiple sensor_id"):
        state.record_scan(lidar_2, lane)


def test_receiver_state_requires_exactly_two_sensors() -> None:
    with pytest.raises(ValueError, match="exactly 2 configured sensors"):
        ReceiverState("environment-a", ("lidar_1",))


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--image"],
        ["--image", "lidar-generator:latest", "--config-dir", "examples"],
        [
            "--image",
            _FAKE_DIGEST,
            "--config-dir",
            "examples",
            "--cpus",
            "0.0",
        ],
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


def test_edge_runner_does_not_overwrite_result_files(tmp_path: Path) -> None:
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
    assert "result file already exists" in completed.stderr
    assert (tmp_path / "generator.log").read_text(encoding="utf-8") == "existing\n"
