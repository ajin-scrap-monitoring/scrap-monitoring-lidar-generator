"""Versioned scan message models and run-time timestamp assembly."""

import math
from dataclasses import dataclass

import numpy as np

from scrap_monitoring_lidar_generator.measurement import MeasuredScan, MeasurementResult

PROTOCOL_VERSION = 1
SCAN_MESSAGE_TYPE = "scan"
MIN_VALID_DISTANCE_M = 0.05
MAX_VALID_DISTANCE_M = 30.0
MAX_SIGNED_64_BIT = 9_223_372_036_854_775_807
_MICROSECONDS_PER_SECOND = 1_000_000


@dataclass(frozen=True, slots=True)
class ScanMessage:
    """One validated v1 scan body without transport framing."""

    environment_id: str
    run_id: str
    scan_id: int
    captured_at: int
    measured_scan: MeasuredScan

    def __post_init__(self) -> None:
        _require_non_empty_string(self.environment_id, "scan message environment_id")
        _require_non_empty_string(self.run_id, "scan message run_id")
        _require_positive_64_bit_integer(self.scan_id, "scan message scan_id")
        _require_non_negative_64_bit_integer(self.captured_at, "scan message captured_at")

        distances_m = self.measured_scan.distances_m
        valid_distances_m = distances_m > 0.0
        if bool(
            np.any(
                valid_distances_m
                & ((distances_m < MIN_VALID_DISTANCE_M) | (distances_m > MAX_VALID_DISTANCE_M))
            )
        ):
            raise ValueError("scan message valid distances must be in [0.05, 30] meters")

    @property
    def protocol_version(self) -> int:
        """Return the fixed public scan contract version."""
        return PROTOCOL_VERSION

    @property
    def message_type(self) -> str:
        """Return the fixed transport message type."""
        return SCAN_MESSAGE_TYPE

    @property
    def sensor_id(self) -> str:
        """Return the source sensor identifier."""
        return self.measured_scan.sensor_id

    @property
    def point_count(self) -> int:
        """Return the number of points in measurement order."""
        return self.measured_scan.point_count


class ScanMessageFactory:
    """Combine deterministic measurements with one run's external identity and UTC epoch."""

    __slots__ = ("_environment_id", "_run_id", "_run_started_at_utc_us")

    def __init__(
        self,
        *,
        environment_id: str,
        run_id: str,
        run_started_at_utc_us: int,
    ) -> None:
        self._environment_id = _require_non_empty_string(
            environment_id,
            "scan message environment_id",
        )
        self._run_id = _require_non_empty_string(run_id, "scan message run_id")
        self._run_started_at_utc_us = _require_non_negative_64_bit_integer(
            run_started_at_utc_us,
            "run start UTC timestamp",
        )

    @property
    def environment_id(self) -> str:
        """Return the environment identifier applied to every message."""
        return self._environment_id

    @property
    def run_id(self) -> str:
        """Return the execution identifier applied to every message."""
        return self._run_id

    @property
    def run_started_at_utc_us(self) -> int:
        """Return the UTC Unix timestamp corresponding to simulation time zero."""
        return self._run_started_at_utc_us

    def build(self, result: MeasurementResult) -> ScanMessage:
        """Build a wire message from the final measurement without retaining its reference scan."""
        captured_offset_us = _elapsed_microseconds(result.measured.captured_elapsed_s)
        if captured_offset_us > MAX_SIGNED_64_BIT - self._run_started_at_utc_us:
            raise OverflowError("scan message captured_at exceeds the signed 64-bit range")
        return ScanMessage(
            environment_id=self._environment_id,
            run_id=self._run_id,
            scan_id=result.scan_id,
            captured_at=self._run_started_at_utc_us + captured_offset_us,
            measured_scan=result.measured.scan,
        )


def _elapsed_microseconds(elapsed_s: float) -> int:
    if not math.isfinite(elapsed_s) or elapsed_s < 0.0:
        raise ValueError("scan captured elapsed time must be finite and non-negative")
    if elapsed_s > MAX_SIGNED_64_BIT / _MICROSECONDS_PER_SECOND:
        raise OverflowError("scan captured elapsed time exceeds the signed 64-bit range")
    return math.floor(elapsed_s * _MICROSECONDS_PER_SECOND + 0.5)


def _require_non_empty_string(value: str, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_positive_64_bit_integer(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_SIGNED_64_BIT:
        raise ValueError(f"{name} must be a positive signed 64-bit integer")
    return value


def _require_non_negative_64_bit_integer(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_SIGNED_64_BIT:
        raise ValueError(f"{name} must be a non-negative signed 64-bit integer")
    return value
