"""Tests for deterministic sensor dropout intervals."""

import numpy as np
import pytest

from scrap_monitoring_lidar_generator.measurement import SensorDropoutScheduler


def test_marks_non_overlapping_intervals_across_point_batches() -> None:
    scheduler = SensorDropoutScheduler(
        sensor_id="sensor-a",
        event_interval_s_range=(2.0, 2.0),
        duration_s_range=(1.0, 1.0),
        seed=123,
    )

    first = scheduler.active_mask(np.array([0.0, 1.0, 2.0, 2.5]))
    second = scheduler.active_mask(np.array([3.0, 4.9, 5.0, 5.5, 6.0]))

    assert first.tolist() == [False, False, True, True]
    assert second.tolist() == [False, False, True, True, False]


def test_same_seed_reproduces_variable_intervals() -> None:
    schedulers = [
        SensorDropoutScheduler(
            sensor_id="sensor-a",
            event_interval_s_range=(0.2, 0.5),
            duration_s_range=(0.1, 0.3),
            seed=456,
        )
        for _ in range(2)
    ]
    point_times_s = np.arange(0.0, 10.0, 0.01)

    masks = [scheduler.active_mask(point_times_s) for scheduler in schedulers]

    assert np.array_equal(masks[0], masks[1])
    assert bool(np.any(masks[0]))
    assert bool(np.any(~masks[0]))


def test_sensor_identifier_derives_independent_intervals() -> None:
    first = SensorDropoutScheduler(
        sensor_id="sensor-a",
        event_interval_s_range=(0.2, 0.5),
        duration_s_range=(0.1, 0.3),
        seed=789,
    )
    second = SensorDropoutScheduler(
        sensor_id="sensor-b",
        event_interval_s_range=(0.2, 0.5),
        duration_s_range=(0.1, 0.3),
        seed=789,
    )
    point_times_s = np.arange(0.0, 10.0, 0.01)

    assert not np.array_equal(
        first.active_mask(point_times_s),
        second.active_mask(point_times_s),
    )


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"sensor_id": ""}, "sensor_id"),
        ({"event_interval_s_range": (0.0, 1.0)}, "event interval"),
        ({"event_interval_s_range": (2.0, 1.0)}, "event interval"),
        ({"duration_s_range": (1.0, float("inf"))}, "duration"),
        ({"seed": True}, "seed"),
    ],
)
def test_rejects_invalid_settings(arguments: dict[str, object], message: str) -> None:
    values: dict[str, object] = {
        "sensor_id": "sensor-a",
        "event_interval_s_range": (1.0, 2.0),
        "duration_s_range": (0.1, 0.2),
        "seed": 123,
    }
    values.update(arguments)

    with pytest.raises(ValueError, match=message):
        SensorDropoutScheduler(**values)  # type: ignore[arg-type]


def test_rejects_invalid_or_repeated_point_times() -> None:
    scheduler = SensorDropoutScheduler(
        sensor_id="sensor-a",
        event_interval_s_range=(1.0, 1.0),
        duration_s_range=(1.0, 1.0),
        seed=123,
    )

    with pytest.raises(ValueError, match="strictly increasing"):
        scheduler.active_mask(np.array([0.5, 0.4]))
    scheduler.active_mask(np.array([0.5, 0.75]))
    with pytest.raises(ValueError, match="monotonically"):
        scheduler.active_mask(np.array([0.75, 1.0]))
