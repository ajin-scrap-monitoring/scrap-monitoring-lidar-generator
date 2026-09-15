"""Verify generated scans with the pinned ajin edge processing implementation."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from scrap_monitoring_lidar_simulator.configuration import load_generator_inputs
from scrap_monitoring_lidar_simulator.edge_integration import build_synthetic_processing_config
from scrap_monitoring_lidar_simulator.runtime import (
    build_measurement_generation_runtime,
)
from scrap_monitoring_lidar_simulator.scan_stream import ScanFrameFactory

_ROOT = Path(__file__).parents[1]
_SOURCE = _ROOT / "contracts" / "lidar" / "v1" / "upstream.json"
_PROTO = _ROOT / "contracts" / "lidar" / "v1" / "lidar.proto"
_GENERATOR_CONFIG = _ROOT / "examples" / "generator.v2.json"
_PROCESSING_FIXTURE = _ROOT / "edge-platform-integration" / "v1" / "processing.synthetic.json"
_RUST_STATUS_FIXTURE = _ROOT / "tests" / "fixtures" / "rust-status" / "lidar-driver-a.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edge-platform-root", required=True, type=Path)
    parser.add_argument(
        "--processing-config",
        type=Path,
        help="configuration produced by the Rust exporter; defaults to the Python builder",
    )
    return parser


def _verify_source(edge_root: Path, source: dict[str, str]) -> None:
    actual_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=edge_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if actual_commit != source["commit"]:
        raise RuntimeError(
            f"edge platform checkout is {actual_commit}, expected pinned {source['commit']}"
        )
    upstream_proto = edge_root / source["path"]
    if upstream_proto.read_bytes() != _PROTO.read_bytes():
        raise RuntimeError("local LiDAR Proto differs from the pinned edge platform contract")
    digest = hashlib.sha256(_PROTO.read_bytes()).hexdigest()
    if digest != source["sha256"]:
        raise RuntimeError("local LiDAR Proto digest differs from its source metadata")


def _load_upstream_modules(edge_root: Path) -> tuple[Any, Any, Any]:
    sys.path.insert(0, str(edge_root / "packages" / "edge-common" / "src"))
    sys.path.insert(0, str(edge_root / "services" / "lidar-processing" / "src"))
    from ajin_edge.config import load_config  # type: ignore[import-not-found]
    from ajin_edge.status import read_status  # type: ignore[import-not-found]
    from ajin_lidar_processing.engine import ProcessingEngine  # type: ignore[import-not-found]

    return load_config, ProcessingEngine, read_status


def _processing_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        inputs = load_generator_inputs(_GENERATOR_CONFIG)
        return build_synthetic_processing_config(
            inputs,
            socket_directory=PurePosixPath("/sockets"),
            site_id="synthetic-site",
            edge_id="synthetic-edge",
            config_revision="synthetic-r1",
        )
    generated = json.loads(path.read_text(encoding="utf-8"))
    fixture = json.loads(_PROCESSING_FIXTURE.read_text(encoding="utf-8"))
    if generated != fixture:
        raise RuntimeError("Rust processing configuration differs from the integration fixture")
    if not isinstance(generated, dict):
        raise RuntimeError("Rust processing configuration must be a JSON object")
    return generated


def _validate_with_upstream(
    edge_root: Path,
    processing_config_path: Path | None,
) -> dict[str, object]:
    load_config, processing_engine, read_status = _load_upstream_modules(edge_root)
    status = read_status(
        _RUST_STATUS_FIXTURE,
        now=datetime(2027, 1, 15, 8, 0, 5, tzinfo=UTC),
    )
    if status["state"] != "HEALTHY":
        raise RuntimeError(f"upstream status reader rejected Rust status: {status}")
    inputs = load_generator_inputs(_GENERATOR_CONFIG)
    processing_config = _processing_config(processing_config_path)
    with tempfile.TemporaryDirectory(prefix="edge-contract-") as directory:
        path = Path(directory) / "processing.json"
        path.write_text(
            json.dumps(processing_config, ensure_ascii=True, allow_nan=False),
            encoding="utf-8",
        )
        hidden_environment = {
            name: os.environ.pop(name, None)
            for name in (
                "SITE_ID",
                "EDGE_ID",
                "CONFIG_REVISION",
                "DEPLOYMENT_REVISION",
                "CAMERA_ID",
                "CONFIG_SHA256",
            )
        }
        try:
            validated_config = load_config(path)
        finally:
            os.environ.update(
                {name: value for name, value in hidden_environment.items() if value is not None}
            )

    engine = processing_engine(validated_config)
    runtime = build_measurement_generation_runtime(inputs)
    base_monotonic_ns = time.monotonic_ns()
    base_unix_ms = time.time_ns() // 1_000_000
    timestamps = iter(
        base_monotonic_ns + cycle * 100_000_000 + sensor * 1_000
        for cycle in range(8)
        for sensor in range(2)
    )
    current_monotonic_ns = base_monotonic_ns

    def monotonic_ns() -> int:
        nonlocal current_monotonic_ns
        current_monotonic_ns = next(timestamps)
        return current_monotonic_ns

    frame_factory = ScanFrameFactory(
        sensor_ids=runtime.sensor_ids,
        edge_id="synthetic-edge",
        config_revision="synthetic-r1",
        instance_ids={sensor_id: f"instance-{sensor_id}" for sensor_id in runtime.sensor_ids},
        monotonic_ns=monotonic_ns,
        unix_ms=lambda: base_unix_ms + (current_monotonic_ns - base_monotonic_ns) // 1_000_000,
    )
    frames = []
    for _ in range(8):
        for result in runtime.next_completed_scans():
            frame = frame_factory.build(result)
            if frame is None:
                continue
            if not engine.ingest(
                frame,
                receive_monotonic_ns=frame.acquired_monotonic_ns + 1_000,
                receive_unix_ms=frame.acquired_at_unix_ms,
            ):
                raise RuntimeError(f"upstream processing rejected {frame.sensor_id} frame")
            frames.append(frame)

    latest_monotonic_ns = max(frame.acquired_monotonic_ns for frame in frames)
    latest_unix_ms = max(frame.acquired_at_unix_ms for frame in frames)
    measurement = engine.measure(
        latest_monotonic_ns + 1_000_000,
        latest_unix_ms + 1,
        {"state": "SYNCED", "offset_ms": 0},
        measurement_id="contract-measurement",
        cycle_id="contract-cycle",
    )
    if measurement is None or measurement["quality"]["state"] != "GOOD":
        raise RuntimeError(f"upstream processing did not produce a GOOD measurement: {measurement}")
    if any(sensor.get("coverage_ratio") != 1.0 for sensor in measurement["sensors"]):
        raise RuntimeError(f"upstream processing profile coverage is incomplete: {measurement}")
    return {
        "frames": len(frames),
        "measurement_state": measurement["quality"]["state"],
        "sensor_ids": [sensor["sensor_id"] for sensor in measurement["sensors"]],
        "status_state": status["state"],
    }


def main() -> int:
    args = _parser().parse_args()
    source = json.loads(_SOURCE.read_text(encoding="utf-8"))
    _verify_source(args.edge_platform_root, source)
    result = _validate_with_upstream(args.edge_platform_root, args.processing_config)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
