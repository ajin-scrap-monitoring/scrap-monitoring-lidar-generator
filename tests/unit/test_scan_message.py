"""Tests for scan message assembly and UTC timestamp conversion."""

from collections.abc import Iterable

import numpy as np
import pytest

from scrap_monitoring_lidar_generator.geometry import HitKind
from scrap_monitoring_lidar_generator.measurement import (
    MeasuredScan,
    MeasurementResult,
    ReferencePoint,
    ReferenceScan,
    ScheduledScan,
    TimedMeasuredScan,
    TimedReferenceScan,
)
from scrap_monitoring_lidar_generator.transport import (
    MAX_SIGNED_64_BIT,
    ScanMessage,
    ScanMessageFactory,
)


def _result(
    *,
    scan_id: int = 1,
    point_times_s: Iterable[float] = (0.0, 0.25),
    distances_m: Iterable[float] = (2.0, 0.0),
) -> MeasurementResult:
    point_times = np.asarray(tuple(point_times_s), dtype=np.float64)
    distances = np.asarray(tuple(distances_m), dtype=np.float64)
    point_count = len(point_times)
    angles_deg = np.arange(point_count, dtype=np.float64) * (360.0 / point_count)
    schedule = ScheduledScan(
        sensor_id="sensor-a",
        scan_id=scan_id,
        rotation_started_at_s=float(point_times[0]),
        completed_at_s=float(point_times[-1] + 0.25),
        angles_deg=angles_deg,
        point_elapsed_times_s=point_times,
    )
    reference = TimedReferenceScan(
        schedule=schedule,
        scan=ReferenceScan(
            sensor_id="sensor-a",
            points=tuple(
                ReferencePoint(
                    angle_deg=float(angle_deg),
                    distance_m=float(distance_m),
                    hit_kind=HitKind.WALL if distance_m > 0.0 else None,
                )
                for angle_deg, distance_m in zip(angles_deg, distances, strict=True)
            ),
        ),
    )
    measured = TimedMeasuredScan(
        schedule=schedule,
        scan=MeasuredScan(
            sensor_id="sensor-a",
            angles_deg=angles_deg,
            distances_m=distances,
            qualities=np.asarray([64] * point_count, dtype=np.uint8),
        ),
    )
    return MeasurementResult(reference=reference, measured=measured)


def test_factory_builds_message_from_final_measurement_without_copying_arrays() -> None:
    result = _result(scan_id=7, point_times_s=(0.1234567, 0.2))
    factory = ScanMessageFactory(
        environment_id="environment-a",
        run_id="run-a",
        run_started_at_utc_us=1_800_000_000_000_000,
    )

    message = factory.build(result)

    assert message.protocol_version == 1
    assert message.message_type == "scan"
    assert message.environment_id == "environment-a"
    assert message.run_id == "run-a"
    assert message.sensor_id == "sensor-a"
    assert message.scan_id == 7
    assert message.captured_at == 1_800_000_000_123_457
    assert message.point_count == 2
    assert message.measured_scan is result.measured.scan


@pytest.mark.parametrize(
    ("elapsed_s", "expected_offset_us"),
    [
        (0.00000049, 0),
        (0.0000005, 1),
        (0.00000149, 1),
        (0.0000015, 2),
    ],
)
def test_factory_rounds_first_point_to_nearest_microsecond_toward_future_on_tie(
    elapsed_s: float,
    expected_offset_us: int,
) -> None:
    message = ScanMessageFactory(
        environment_id="environment-a",
        run_id="run-a",
        run_started_at_utc_us=100,
    ).build(_result(point_times_s=(elapsed_s, elapsed_s + 0.1)))

    assert message.captured_at == 100 + expected_offset_us


def test_factory_rejects_captured_at_overflow() -> None:
    factory = ScanMessageFactory(
        environment_id="environment-a",
        run_id="run-a",
        run_started_at_utc_us=MAX_SIGNED_64_BIT,
    )

    with pytest.raises(OverflowError, match="captured_at"):
        factory.build(_result(point_times_s=(0.000001, 0.1)))


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"environment_id": ""}, "environment_id"),
        ({"run_id": ""}, "run_id"),
        ({"run_started_at_utc_us": -1}, "non-negative"),
        ({"run_started_at_utc_us": True}, "non-negative"),
        ({"run_started_at_utc_us": MAX_SIGNED_64_BIT + 1}, "non-negative"),
    ],
)
def test_factory_rejects_invalid_run_metadata(
    arguments: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "environment_id": "environment-a",
        "run_id": "run-a",
        "run_started_at_utc_us": 0,
    }
    values.update(arguments)

    with pytest.raises(ValueError, match=message):
        ScanMessageFactory(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("distance_m", [0.01, 30.01])
def test_message_rejects_positive_distance_outside_public_contract(distance_m: float) -> None:
    result = _result(distances_m=(distance_m, 0.0))

    with pytest.raises(ValueError, match="valid distances"):
        ScanMessage(
            environment_id="environment-a",
            run_id="run-a",
            scan_id=1,
            captured_at=0,
            measured_scan=result.measured.scan,
        )
