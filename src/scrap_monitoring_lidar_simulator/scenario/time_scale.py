"""Shared time scaling for accelerated or slowed scenario execution."""

import math

type FloatRange = tuple[float, float]

REFERENCE_MEAN_FILL_DURATION_S = 24.0 * 60.0 * 60.0


def scenario_time_scale(mean_fill_duration_s: float) -> float:
    """Return the configured mean fill duration relative to the 24-hour reference."""
    if not math.isfinite(mean_fill_duration_s) or mean_fill_duration_s <= 0.0:
        raise ValueError("mean fill duration must be a finite positive number")
    return mean_fill_duration_s / REFERENCE_MEAN_FILL_DURATION_S


def scale_duration_range(value: FloatRange, time_scale: float) -> FloatRange:
    """Scale a positive duration range while preserving its order."""
    _require_positive_time_scale(time_scale)
    lower, upper = value
    scaled = lower * time_scale, upper * time_scale
    if (
        not math.isfinite(scaled[0])
        or not math.isfinite(scaled[1])
        or scaled[0] <= 0.0
        or scaled[1] < scaled[0]
    ):
        raise ValueError("scaled duration range must contain finite positive ordered values")
    return scaled


def scale_event_rate_per_s(value: float, time_scale: float) -> float:
    """Scale a non-negative event rate inversely to scenario time."""
    _require_positive_time_scale(time_scale)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError("event rate must be finite and non-negative")
    scaled = value / time_scale
    if not math.isfinite(scaled):
        raise ValueError("scaled event rate must be finite")
    return scaled


def _require_positive_time_scale(value: float) -> None:
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError("scenario time scale must be a finite positive number")
