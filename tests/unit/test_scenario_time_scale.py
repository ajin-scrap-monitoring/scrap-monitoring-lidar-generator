"""Tests for the 24-hour scenario time reference."""

import pytest

from scrap_monitoring_lidar_generator.scenario import (
    REFERENCE_MEAN_FILL_DURATION_S,
    scale_duration_range,
    scale_event_rate_per_s,
    scenario_time_scale,
)


def test_uses_24_hours_as_the_unit_time_scale() -> None:
    assert REFERENCE_MEAN_FILL_DURATION_S == 86_400.0
    assert scenario_time_scale(86_400.0) == 1.0
    assert scenario_time_scale(3_600.0) == pytest.approx(1.0 / 24.0)


def test_scales_durations_and_rates_in_opposite_directions() -> None:
    time_scale = scenario_time_scale(43_200.0)

    assert scale_duration_range((10.0, 20.0), time_scale) == (5.0, 10.0)
    assert scale_event_rate_per_s(2.0, time_scale) == 4.0
    assert scale_event_rate_per_s(0.0, time_scale) == 0.0


@pytest.mark.parametrize("value", [0.0, -1.0, float("inf"), float("nan")])
def test_rejects_invalid_mean_fill_duration(value: float) -> None:
    with pytest.raises(ValueError, match="mean fill duration"):
        scenario_time_scale(value)


def test_rejects_unrepresentable_scaled_values() -> None:
    with pytest.raises(ValueError, match="scaled duration"):
        scale_duration_range((1e-300, 1e-300), 1e-300)
    with pytest.raises(ValueError, match="scaled event rate"):
        scale_event_rate_per_s(1e300, 1e-300)
