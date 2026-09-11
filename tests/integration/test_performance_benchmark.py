"""Integration test for the executable performance measurement."""

import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parents[2]


def test_benchmark_reports_all_non_empty_stages() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.performance.generation",
            "--config",
            "examples/generator.v1.json",
            "--scans-per-sensor",
            "1",
        ],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10.0,
    )

    document = json.loads(completed.stdout)
    assert document["workload"]["generated_scans"] == 1
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
