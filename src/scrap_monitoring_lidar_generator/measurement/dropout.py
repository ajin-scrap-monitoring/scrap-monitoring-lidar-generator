"""Deterministic sensor-local invalid measurement intervals."""

import math

import numpy as np
from numpy.typing import NDArray

from scrap_monitoring_lidar_generator.measurement._randomness import (
    derive_sensor_seed,
    require_seed,
)

type FloatArray = NDArray[np.float64]
type BoolArray = NDArray[np.bool_]
type FloatRange = tuple[float, float]


class SensorDropoutScheduler:
    """Mark non-overlapping dropout intervals separated by random inactive gaps."""

    __slots__ = (
        "_current_end_s",
        "_current_start_s",
        "_duration_s_range",
        "_event_interval_s_range",
        "_last_point_elapsed_s",
        "_rng",
        "_sensor_id",
    )

    def __init__(
        self,
        *,
        sensor_id: str,
        event_interval_s_range: FloatRange,
        duration_s_range: FloatRange,
        seed: int,
    ) -> None:
        if not sensor_id:
            raise ValueError("dropout scheduler sensor_id must be non-empty")
        require_seed(seed, "dropout seed")
        self._event_interval_s_range = _require_positive_range(
            event_interval_s_range,
            "dropout event interval",
        )
        self._duration_s_range = _require_positive_range(
            duration_s_range,
            "dropout duration",
        )
        self._sensor_id = sensor_id
        self._rng = np.random.Generator(
            np.random.PCG64(derive_sensor_seed(seed, sensor_id, "dropout"))
        )
        self._current_start_s = self._draw(self._event_interval_s_range)
        self._current_end_s: float | None = None
        self._last_point_elapsed_s = -math.inf

    @property
    def sensor_id(self) -> str:
        """Return the sensor handled by this scheduler."""
        return self._sensor_id

    def active_mask(self, point_elapsed_times_s: FloatArray) -> BoolArray:
        """Return dropout membership for a strictly increasing point-time batch."""
        point_times_s = np.asarray(point_elapsed_times_s, dtype=np.float64)
        if point_times_s.ndim != 1 or point_times_s.size == 0:
            raise ValueError("dropout point times must be a non-empty 1D array")
        if not bool(np.all(np.isfinite(point_times_s))) or bool(np.any(point_times_s < 0.0)):
            raise ValueError("dropout point times must be finite and non-negative")
        if bool(np.any(np.diff(point_times_s) <= 0.0)):
            raise ValueError("dropout point times must be strictly increasing")
        if point_times_s[0] <= self._last_point_elapsed_s:
            raise ValueError("dropout point-time batches must advance monotonically")

        active = np.zeros(point_times_s.size, dtype=np.bool_)
        through_s = float(point_times_s[-1])
        while self._current_start_s <= through_s:
            if self._current_end_s is None:
                self._current_end_s = self._current_start_s + self._draw(self._duration_s_range)
            active |= (point_times_s >= self._current_start_s) & (
                point_times_s < self._current_end_s
            )
            if self._current_end_s > through_s:
                break
            self._current_start_s = self._current_end_s + self._draw(self._event_interval_s_range)
            self._current_end_s = None

        self._last_point_elapsed_s = through_s
        return active

    def _draw(self, value_range: FloatRange) -> float:
        return float(self._rng.uniform(*value_range))


def _require_positive_range(value: FloatRange, name: str) -> FloatRange:
    if not isinstance(value, tuple) or len(value) != 2:
        raise ValueError(f"{name} range must contain exactly two values")
    lower, upper = value
    if not math.isfinite(lower) or not math.isfinite(upper) or lower <= 0.0 or upper < lower:
        raise ValueError(f"{name} range must contain finite positive ordered values")
    return lower, upper
