"""Tests for final measurement result models."""

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


def _schedule(*, sensor_id: str = "sensor-a", angle_deg: float = 10.0) -> ScheduledScan:
    return ScheduledScan(
        sensor_id=sensor_id,
        scan_id=1,
        rotation_started_at_s=0.0,
        completed_at_s=1.0,
        angles_deg=np.array([angle_deg]),
        point_elapsed_times_s=np.array([0.0]),
    )


def _measured_scan(
    *,
    sensor_id: str = "sensor-a",
    angle_deg: float = 10.0,
) -> MeasuredScan:
    return MeasuredScan(
        sensor_id=sensor_id,
        angles_deg=np.array([angle_deg]),
        distances_m=np.array([2.0]),
        qualities=np.array([64], dtype=np.uint8),
    )


def test_measured_scan_copies_input_arrays() -> None:
    angles = np.array([10.0])
    distances = np.array([2.0])
    qualities = np.array([64], dtype=np.uint8)

    scan = MeasuredScan(
        sensor_id="sensor-a",
        angles_deg=angles,
        distances_m=distances,
        qualities=qualities,
    )
    angles[0] = 20.0
    distances[0] = 3.0
    qualities[0] = 1

    assert scan.angles_deg.tolist() == [10.0]
    assert scan.distances_m.tolist() == [2.0]
    assert scan.qualities.tolist() == [64]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            {
                "sensor_id": "",
                "angles_deg": np.array([10.0]),
                "distances_m": np.array([2.0]),
                "qualities": np.array([64], dtype=np.uint8),
            },
            "sensor_id",
        ),
        (
            {
                "sensor_id": "sensor-a",
                "angles_deg": np.array([10.0, 20.0]),
                "distances_m": np.array([2.0]),
                "qualities": np.array([64], dtype=np.uint8),
            },
            "same 1D shape",
        ),
        (
            {
                "sensor_id": "sensor-a",
                "angles_deg": np.array([360.0]),
                "distances_m": np.array([2.0]),
                "qualities": np.array([64], dtype=np.uint8),
            },
            "angles",
        ),
        (
            {
                "sensor_id": "sensor-a",
                "angles_deg": np.array([10.0]),
                "distances_m": np.array([-1.0]),
                "qualities": np.array([64], dtype=np.uint8),
            },
            "distances",
        ),
        (
            {
                "sensor_id": "sensor-a",
                "angles_deg": np.array([10.0]),
                "distances_m": np.array([2.0]),
                "qualities": np.array([1.5]),
            },
            "integers",
        ),
        (
            {
                "sensor_id": "sensor-a",
                "angles_deg": np.array([10.0]),
                "distances_m": np.array([2.0]),
                "qualities": np.array([256]),
            },
            r"\[0, 255\]",
        ),
    ],
)
def test_rejects_invalid_measured_scan_arrays(
    arguments: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        MeasuredScan(**arguments)  # type: ignore[arg-type]


def test_timed_measured_scan_requires_matching_schedule() -> None:
    with pytest.raises(ValueError, match="point angles"):
        TimedMeasuredScan(
            schedule=_schedule(angle_deg=10.0),
            scan=_measured_scan(angle_deg=20.0),
        )


def test_measurement_result_requires_the_shared_schedule() -> None:
    first_schedule = _schedule()
    second_schedule = _schedule()
    reference = TimedReferenceScan(
        schedule=first_schedule,
        scan=ReferenceScan(
            sensor_id="sensor-a",
            points=(ReferencePoint(10.0, 2.0, HitKind.WALL),),
        ),
    )
    measured = TimedMeasuredScan(schedule=second_schedule, scan=_measured_scan())

    with pytest.raises(ValueError, match="share one rotation schedule"):
        MeasurementResult(reference=reference, measured=measured)
