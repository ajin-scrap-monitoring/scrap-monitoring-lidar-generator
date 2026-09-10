"""Deterministic distance noise and quality generation."""

import hashlib
import math
from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

from scrap_monitoring_lidar_generator.geometry.intersections import validate_distance_bounds
from scrap_monitoring_lidar_generator.measurement.models import (
    MeasuredScan,
    MeasurementResult,
    TimedMeasuredScan,
    TimedReferenceScan,
)

type FloatArray = NDArray[np.float64]
type QualityArray = NDArray[np.uint8]

_MAX_SEED = 18_446_744_073_709_551_615
_QUALITY_VALUE_COUNT = 256


class MeasurementGenerator:
    """Apply bounded normal noise and sensor-specific quality distributions."""

    __slots__ = (
        "_invalid_quality_cdf",
        "_max_distance_m",
        "_min_distance_m",
        "_noise_enabled",
        "_noise_limit_m",
        "_noise_rng",
        "_noise_standard_deviation_m",
        "_quality_rng",
        "_sensor_id",
        "_valid_quality_cdf",
    )

    def __init__(
        self,
        *,
        sensor_id: str,
        min_distance_m: float,
        max_distance_m: float,
        noise_enabled: bool,
        noise_standard_deviation_m: float,
        noise_limit_m: float,
        valid_quality_frequencies: Sequence[int],
        invalid_quality_frequencies: Sequence[int],
        seed: int,
    ) -> None:
        if not sensor_id:
            raise ValueError("measurement generator sensor_id must be non-empty")
        validate_distance_bounds(min_distance_m, max_distance_m)
        if min_distance_m == 0.0 or not math.isfinite(max_distance_m):
            raise ValueError("measurement range must have finite positive bounds")
        if not isinstance(noise_enabled, bool):
            raise ValueError("noise enabled must be a boolean")
        standard_deviation_m = _require_non_negative_finite(
            noise_standard_deviation_m,
            "noise standard deviation",
        )
        limit_m = _require_non_negative_finite(noise_limit_m, "noise limit")
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= _MAX_SEED:
            raise ValueError("measurement seed must be an unsigned 64-bit integer")

        self._sensor_id = sensor_id
        self._min_distance_m = float(min_distance_m)
        self._max_distance_m = float(max_distance_m)
        self._noise_enabled = noise_enabled
        self._noise_standard_deviation_m = standard_deviation_m
        self._noise_limit_m = limit_m
        self._valid_quality_cdf = _quality_cdf(
            valid_quality_frequencies,
            "valid quality frequencies",
        )
        self._invalid_quality_cdf = _quality_cdf(
            invalid_quality_frequencies,
            "invalid quality frequencies",
        )
        self._noise_rng = np.random.Generator(
            np.random.PCG64(_derive_seed(seed, sensor_id, "distance-noise"))
        )
        self._quality_rng = np.random.Generator(
            np.random.PCG64(_derive_seed(seed, sensor_id, "quality"))
        )

    @property
    def sensor_id(self) -> str:
        """Return the sensor handled by this generator."""
        return self._sensor_id

    def generate(self, reference: TimedReferenceScan) -> MeasurementResult:
        """Generate final distances and quality while retaining the reference result."""
        if reference.sensor_id != self._sensor_id:
            raise ValueError("measurement generator and reference sensor identifiers must match")

        reference_distances = np.fromiter(
            (point.distance_m for point in reference.scan.points),
            dtype=np.float64,
            count=len(reference.scan.points),
        )
        distances_m = reference_distances.copy()
        if self._noise_enabled:
            noise_m = _sample_truncated_normal(
                self._noise_rng,
                size=distances_m.size,
                standard_deviation=self._noise_standard_deviation_m,
                limit=self._noise_limit_m,
            )
            reference_valid = reference_distances > 0.0
            distances_m[reference_valid] += noise_m[reference_valid]

        final_valid = (
            (reference_distances > 0.0)
            & (distances_m >= self._min_distance_m)
            & (distances_m <= self._max_distance_m)
        )
        distances_m[~final_valid] = 0.0
        qualities = self._sample_qualities(final_valid)
        measured = TimedMeasuredScan(
            schedule=reference.schedule,
            scan=MeasuredScan(
                sensor_id=self._sensor_id,
                angles_deg=reference.schedule.angles_deg,
                distances_m=distances_m,
                qualities=qualities,
            ),
        )
        return MeasurementResult(reference=reference, measured=measured)

    def _sample_qualities(self, valid_distances: NDArray[np.bool_]) -> QualityArray:
        draws = self._quality_rng.random(valid_distances.size)
        qualities = np.empty(valid_distances.size, dtype=np.uint8)
        qualities[valid_distances] = np.searchsorted(
            self._valid_quality_cdf,
            draws[valid_distances],
            side="right",
        ).astype(np.uint8)
        qualities[~valid_distances] = np.searchsorted(
            self._invalid_quality_cdf,
            draws[~valid_distances],
            side="right",
        ).astype(np.uint8)
        return qualities


def _sample_truncated_normal(
    rng: np.random.Generator,
    *,
    size: int,
    standard_deviation: float,
    limit: float,
) -> FloatArray:
    if size == 0 or standard_deviation == 0.0 or limit == 0.0:
        return np.zeros(size, dtype=np.float64)

    result = np.empty(size, dtype=np.float64)
    remaining = np.arange(size)
    if limit >= standard_deviation * 0.5:
        while remaining.size:
            candidates = rng.normal(0.0, standard_deviation, size=remaining.size)
            accepted = np.abs(candidates) <= limit
            result[remaining[accepted]] = candidates[accepted]
            remaining = remaining[~accepted]
        return result

    while remaining.size:
        candidates = rng.uniform(-limit, limit, size=remaining.size)
        acceptance = np.exp(-0.5 * np.square(candidates / standard_deviation))
        accepted = rng.random(remaining.size) < acceptance
        result[remaining[accepted]] = candidates[accepted]
        remaining = remaining[~accepted]
    return result


def _quality_cdf(frequencies: Sequence[int], name: str) -> FloatArray:
    values = tuple(frequencies)
    if len(values) != _QUALITY_VALUE_COUNT:
        raise ValueError(f"{name} must contain exactly {_QUALITY_VALUE_COUNT} values")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
        raise ValueError(f"{name} must contain non-negative integers")
    total = sum(values)
    if total == 0:
        raise ValueError(f"{name} must contain at least one positive value")

    weights = np.asarray(values, dtype=np.float64)
    cdf = np.cumsum(weights / float(total))
    cdf[-1] = 1.0
    cdf.flags.writeable = False
    return cdf


def _derive_seed(seed: int, sensor_id: str, stream_name: str) -> int:
    sensor_bytes = sensor_id.encode("utf-8")
    payload = (
        b"measurement\0"
        + seed.to_bytes(8, byteorder="big")
        + len(sensor_bytes).to_bytes(8, byteorder="big")
        + sensor_bytes
        + stream_name.encode("ascii")
    )
    return int.from_bytes(hashlib.sha256(payload).digest()[:16], byteorder="big")


def _require_non_negative_finite(value: float, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite and non-negative")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{name} must be finite and non-negative") from error
    if not math.isfinite(result) or result < 0.0:
        raise ValueError(f"{name} must be finite and non-negative")
    return result
