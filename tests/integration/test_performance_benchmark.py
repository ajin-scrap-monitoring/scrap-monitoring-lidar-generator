"""Integration test for the executable performance measurement."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

_ROOT = Path(__file__).parents[2]


def _run_benchmark(config_path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.performance.generation",
            "--config",
            str(config_path),
            "--scans-per-sensor",
            "1",
        ],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10.0,
    )
    return cast(dict[str, Any], json.loads(completed.stdout))


def test_benchmark_reports_all_non_empty_stages() -> None:
    document = _run_benchmark(_ROOT / "examples" / "generator.v1.json")

    assert document["workload"]["sensor_count"] == 2
    assert document["workload"]["generated_scans"] == 2
    assert document["workload"]["generated_points"] > 0
    assert document["workload"]["generated_points_per_simulated_second"] > 0
    assert document["workload"]["scan_wire_bytes_per_simulated_second"] > 0
    assert set(document["stages"]) == {
        "scene_update",
        "scan_generation",
        "serialization",
        "transport_wait",
    }
    assert all(stage["samples"] > 0 for stage in document["stages"].values())


def test_benchmark_uses_one_transport_connection_per_sensor() -> None:
    document = _run_benchmark(_ROOT / "examples" / "generator.v1.json")

    assert document["workload"]["sensor_count"] == 2
    assert document["workload"]["generated_scans"] == 2
    assert document["stages"]["transport_wait"]["samples"] == 4
