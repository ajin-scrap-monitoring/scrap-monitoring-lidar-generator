"""Integration tests from generated measurements through the scan body codec."""

from pathlib import Path

import numpy as np

from scrap_monitoring_lidar_generator.configuration import load_generator_inputs
from scrap_monitoring_lidar_generator.runtime import build_measurement_generation_runtime
from scrap_monitoring_lidar_generator.transport import (
    ScanMessageFactory,
    decode_scan_message,
    encode_scan_message,
)

_EXAMPLES = Path(__file__).parents[2] / "examples"


def test_generated_scan_round_trips_with_run_identity_and_utc_timestamp() -> None:
    inputs = load_generator_inputs(_EXAMPLES / "generator.v1.json")
    runtime = build_measurement_generation_runtime(inputs)
    factory = ScanMessageFactory(
        environment_id=inputs.environment.environment_id,
        run_id="synthetic-run-a",
        run_started_at_utc_us=1_800_000_000_000_000,
    )

    first_result = runtime.next_completed_scans()[0]
    second_result = runtime.next_completed_scans()[0]
    first_message = factory.build(first_result)
    second_message = factory.build(second_result)
    decoded = decode_scan_message(encode_scan_message(second_message))

    assert first_message.captured_at == 1_800_000_000_000_000
    assert second_message.captured_at == 1_800_000_000_100_000
    assert decoded.environment_id == inputs.environment.environment_id
    assert decoded.run_id == "synthetic-run-a"
    assert decoded.sensor_id == second_result.sensor_id
    assert decoded.scan_id == 2
    assert decoded.captured_at == second_message.captured_at
    np.testing.assert_array_equal(
        decoded.measured_scan.angles_deg,
        second_result.measured.scan.angles_deg,
    )
    np.testing.assert_array_equal(
        decoded.measured_scan.distances_m,
        second_result.measured.scan.distances_m,
    )
    np.testing.assert_array_equal(
        decoded.measured_scan.qualities,
        second_result.measured.scan.qualities,
    )
