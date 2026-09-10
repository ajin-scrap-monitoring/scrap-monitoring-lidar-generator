"""Tests for independent sensor rotation schedules."""

import numpy as np
import pytest

from scrap_monitoring_lidar_generator.measurement import (
    ScheduledScan,
    SensorRotationScheduler,
    create_seeded_rotation_scheduler,
)


def test_integer_rate_ratio_produces_one_ordered_full_rotation() -> None:
    scheduler = SensorRotationScheduler(
        sensor_id="sensor-a",
        sample_rate_hz=8.0,
        rotation_rate_hz=2.0,
        initial_angle_deg=350.0,
    )

    first = scheduler.next_scan()
    second = scheduler.next_scan()

    assert first.sensor_id == "sensor-a"
    assert first.scan_id == 1
    assert first.rotation_started_at_s == 0.0
    assert first.completed_at_s == 0.5
    assert first.captured_elapsed_s == 0.0
    np.testing.assert_allclose(first.point_elapsed_times_s, (0.0, 0.125, 0.25, 0.375))
    np.testing.assert_allclose(first.angles_deg, (350.0, 80.0, 170.0, 260.0))
    assert second.scan_id == 2
    assert second.rotation_started_at_s == 0.5
    assert second.completed_at_s == 1.0
    np.testing.assert_allclose(second.angles_deg, first.angles_deg)


def test_fractional_rate_ratio_varies_count_without_losing_samples() -> None:
    scheduler = SensorRotationScheduler(
        sensor_id="sensor-a",
        sample_rate_hz=5.0,
        rotation_rate_hz=2.0,
        initial_angle_deg=10.0,
    )

    scans = tuple(scheduler.next_scan() for _ in range(10))

    assert [scan.point_count for scan in scans] == [3, 2] * 5
    assert sum(scan.point_count for scan in scans) == 25
    assert scans[1].rotation_started_at_s == 0.5
    assert scans[1].captured_elapsed_s == pytest.approx(0.6)
    assert scans[-1].completed_at_s == 5.0
    all_times_s = np.concatenate([scan.point_elapsed_times_s for scan in scans])
    np.testing.assert_allclose(all_times_s, np.arange(25) / 5.0)


def test_sensor_schedulers_advance_independently() -> None:
    first = SensorRotationScheduler(
        sensor_id="sensor-a",
        sample_rate_hz=12.0,
        rotation_rate_hz=3.0,
        initial_angle_deg=20.0,
    )
    second = SensorRotationScheduler(
        sensor_id="sensor-b",
        sample_rate_hz=12.0,
        rotation_rate_hz=3.0,
        initial_angle_deg=40.0,
    )

    assert first.next_scan().scan_id == 1
    assert first.next_scan().scan_id == 2
    assert second.next_scan().scan_id == 1
    assert first.next_scan_id == 3
    assert second.next_scan_id == 2


def test_seeded_initial_angle_is_stable_and_sensor_specific() -> None:
    first = create_seeded_rotation_scheduler(
        sensor_id="sensor-a",
        sample_rate_hz=8.0,
        rotation_rate_hz=2.0,
        seed=12345,
    )
    repeated = create_seeded_rotation_scheduler(
        sensor_id="sensor-a",
        sample_rate_hz=8.0,
        rotation_rate_hz=2.0,
        seed=12345,
    )
    other_sensor = create_seeded_rotation_scheduler(
        sensor_id="sensor-b",
        sample_rate_hz=8.0,
        rotation_rate_hz=2.0,
        seed=12345,
    )

    assert first.initial_angle_deg == repeated.initial_angle_deg
    assert first.initial_angle_deg != other_sensor.initial_angle_deg
    np.testing.assert_array_equal(first.next_scan().angles_deg, repeated.next_scan().angles_deg)


def test_scheduled_scan_copies_and_protects_arrays() -> None:
    angles_deg = np.array((10.0, 20.0), dtype=np.float64)
    point_times_s = np.array((1.0, 1.1), dtype=np.float64)
    scan = ScheduledScan(
        sensor_id="sensor-a",
        scan_id=1,
        rotation_started_at_s=1.0,
        completed_at_s=1.2,
        angles_deg=angles_deg,
        point_elapsed_times_s=point_times_s,
    )
    angles_deg[:] = 30.0
    point_times_s[:] = 2.0

    np.testing.assert_array_equal(scan.angles_deg, (10.0, 20.0))
    np.testing.assert_array_equal(scan.point_elapsed_times_s, (1.0, 1.1))
    assert scan.angles_deg.flags.writeable is False
    assert scan.point_elapsed_times_s.flags.writeable is False


@pytest.mark.parametrize(
    ("sample_rate_hz", "rotation_rate_hz", "initial_angle_deg"),
    [
        (0.0, 2.0, 0.0),
        (1.0, 2.0, 0.0),
        (8.0, float("inf"), 0.0),
        (8.0, 2.0, -1.0),
        (8.0, 2.0, 360.0),
    ],
)
def test_rejects_invalid_scheduler_values(
    sample_rate_hz: float,
    rotation_rate_hz: float,
    initial_angle_deg: float,
) -> None:
    with pytest.raises(ValueError):
        SensorRotationScheduler(
            sensor_id="sensor-a",
            sample_rate_hz=sample_rate_hz,
            rotation_rate_hz=rotation_rate_hz,
            initial_angle_deg=initial_angle_deg,
        )


def test_rejects_invalid_scheduled_scan_arrays() -> None:
    with pytest.raises(ValueError, match="same 1D shape"):
        ScheduledScan(
            sensor_id="sensor-a",
            scan_id=1,
            rotation_started_at_s=0.0,
            completed_at_s=1.0,
            angles_deg=np.array((10.0, 20.0)),
            point_elapsed_times_s=np.array((0.0,)),
        )

    with pytest.raises(ValueError, match="strictly increasing"):
        ScheduledScan(
            sensor_id="sensor-a",
            scan_id=1,
            rotation_started_at_s=0.0,
            completed_at_s=1.0,
            angles_deg=np.array((10.0, 20.0)),
            point_elapsed_times_s=np.array((0.5, 0.5)),
        )

    with pytest.raises(ValueError, match="precede rotation completion"):
        ScheduledScan(
            sensor_id="sensor-a",
            scan_id=1,
            rotation_started_at_s=0.0,
            completed_at_s=1.0,
            angles_deg=np.array((10.0, 20.0)),
            point_elapsed_times_s=np.array((0.5, 1.0)),
        )
