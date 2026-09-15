"""Tests for edge platform ScanFrame conversion."""

from pathlib import Path

import pytest

from scrap_monitoring_lidar_simulator.configuration import load_generator_inputs
from scrap_monitoring_lidar_simulator.runtime import build_measurement_generation_runtime
from scrap_monitoring_lidar_simulator.scan_stream import ScanFrameFactory

_ROOT = Path(__file__).parents[2]


def test_frame_matches_driver_units_order_and_identity() -> None:
    inputs = load_generator_inputs(_ROOT / "examples" / "generator.v2.json")
    runtime = build_measurement_generation_runtime(inputs)
    first_results = runtime.next_completed_scans()
    second_results = runtime.next_completed_scans()
    monotonic_values = iter((1_000_000_000, 1_000_000_001, 1_100_000_000, 1_100_000_001))
    factory = ScanFrameFactory(
        sensor_ids=runtime.sensor_ids,
        edge_id="edge-a",
        config_revision="config-a",
        instance_ids={"lidar_1": "instance-1", "lidar_2": "instance-2"},
        monotonic_ns=lambda: next(monotonic_values),
        unix_ms=lambda: 1_800_000_000_000,
    )

    assert [factory.build(result) for result in first_results] == [None, None]
    frames = [factory.build(result) for result in second_results]
    frame = frames[0]
    assert frame is not None
    assert frame.schema_version == "1.0"
    assert frame.edge_id == "edge-a"
    assert frame.sensor_id == "lidar_1"
    assert frame.sequence == 1
    assert frame.acquired_at_unix_ms == 1_800_000_000_000
    assert frame.acquired_monotonic_ns == 1_100_000_000
    assert frame.sdk_status == "OK"
    assert frame.scan_hz == pytest.approx(10.0)
    assert frame.instance_id == "instance-1"
    assert frame.config_revision == "config-a"
    assert len(frame.samples) == second_results[0].measured.scan.point_count
    assert all(
        left.angle_mdeg <= right.angle_mdeg
        for left, right in zip(frame.samples, frame.samples[1:], strict=False)
    )
    assert all(0 <= sample.angle_mdeg < 360_000 for sample in frame.samples)
    assert all(0 <= sample.quality <= 63 for sample in frame.samples)


def test_frame_factory_rejects_non_increasing_completion_time() -> None:
    inputs = load_generator_inputs(_ROOT / "examples" / "generator.v2.json")
    runtime = build_measurement_generation_runtime(inputs)
    results = [runtime.next_completed_scans()[0] for _ in range(2)]
    factory = ScanFrameFactory(
        sensor_ids=("lidar_1",),
        edge_id="edge-a",
        config_revision="config-a",
        instance_ids={"lidar_1": "instance-1"},
        monotonic_ns=lambda: 1,
        unix_ms=lambda: 1,
    )

    assert factory.build(results[0]) is None
    with pytest.raises(ValueError, match="increase"):
        factory.build(results[1])


def test_frame_factory_rejects_driver_identity_over_64_bytes() -> None:
    with pytest.raises(ValueError, match="edge_id"):
        ScanFrameFactory(
            sensor_ids=("lidar_1",),
            edge_id="x" * 65,
            config_revision="config-a",
            instance_ids={"lidar_1": "instance-1"},
        )
