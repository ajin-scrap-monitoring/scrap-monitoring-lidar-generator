"""Independent sensor rotation and point timing schedules."""

import hashlib
import math
from fractions import Fraction

import numpy as np
from numpy.typing import NDArray

type FloatArray = NDArray[np.float64]

_MAX_SCAN_ID = 9_223_372_036_854_775_807
_MAX_SEED = 18_446_744_073_709_551_615
_TIME_TOLERANCE = 1e-12


class ScheduledScan:
    """Read-only angle and simulation-time arrays for one completed rotation."""

    __slots__ = (
        "_angles_deg",
        "_point_elapsed_times_s",
        "completed_at_s",
        "rotation_started_at_s",
        "scan_id",
        "sensor_id",
    )

    def __init__(
        self,
        *,
        sensor_id: str,
        scan_id: int,
        rotation_started_at_s: float,
        completed_at_s: float,
        angles_deg: FloatArray,
        point_elapsed_times_s: FloatArray,
    ) -> None:
        if not sensor_id:
            raise ValueError("scheduled scan sensor_id must be non-empty")
        if isinstance(scan_id, bool) or not isinstance(scan_id, int):
            raise ValueError("scheduled scan scan_id must be an integer")
        if not 1 <= scan_id <= _MAX_SCAN_ID:
            raise ValueError("scheduled scan scan_id must be a positive 64-bit integer")
        if not math.isfinite(rotation_started_at_s) or rotation_started_at_s < 0.0:
            raise ValueError("rotation start must be a finite non-negative time")
        if not math.isfinite(completed_at_s) or completed_at_s <= rotation_started_at_s:
            raise ValueError("rotation completion must be finite and after its start")

        angles = np.array(angles_deg, dtype=np.float64, copy=True)
        point_times_s = np.array(point_elapsed_times_s, dtype=np.float64, copy=True)
        if angles.ndim != 1 or point_times_s.ndim != 1 or angles.shape != point_times_s.shape:
            raise ValueError("scheduled scan angle and time arrays must have the same 1D shape")
        if angles.size == 0:
            raise ValueError("scheduled scan must contain at least one point")
        if not bool(np.all(np.isfinite(angles))) or not bool(np.all(np.isfinite(point_times_s))):
            raise ValueError("scheduled scan angles and times must be finite")
        if bool(np.any(angles < 0.0)) or bool(np.any(angles >= 360.0)):
            raise ValueError("scheduled scan angles must be in [0, 360)")
        if bool(np.any(np.diff(point_times_s) <= 0.0)):
            raise ValueError("scheduled scan point times must be strictly increasing")

        tolerance_s = max(1.0, completed_at_s) * _TIME_TOLERANCE
        if point_times_s[0] < rotation_started_at_s - tolerance_s:
            raise ValueError("scheduled scan points must not precede the rotation start")
        if point_times_s[-1] >= completed_at_s:
            raise ValueError("scheduled scan points must precede rotation completion")

        angles.flags.writeable = False
        point_times_s.flags.writeable = False
        self.sensor_id = sensor_id
        self.scan_id = scan_id
        self.rotation_started_at_s = rotation_started_at_s
        self.completed_at_s = completed_at_s
        self._angles_deg = angles
        self._point_elapsed_times_s = point_times_s

    @property
    def point_count(self) -> int:
        """Return the actual number of samples in this rotation."""
        return int(self._angles_deg.size)

    @property
    def captured_elapsed_s(self) -> float:
        """Return the simulation time of the first measurement point."""
        return float(self._point_elapsed_times_s[0])

    @property
    def angles_deg(self) -> FloatArray:
        """Return the read-only sensor angle array in measurement order."""
        return self._angles_deg

    @property
    def point_elapsed_times_s(self) -> FloatArray:
        """Return the read-only simulation time array in measurement order."""
        return self._point_elapsed_times_s


class SensorRotationScheduler:
    """Generate completed rotation schedules for one sensor independently."""

    __slots__ = (
        "_initial_angle_deg",
        "_next_rotation_index",
        "_next_sample_index",
        "_rotation_rate_fraction",
        "_rotation_rate_hz",
        "_sample_rate_fraction",
        "_sample_rate_hz",
        "_sensor_id",
    )

    def __init__(
        self,
        *,
        sensor_id: str,
        sample_rate_hz: float,
        rotation_rate_hz: float,
        initial_angle_deg: float,
    ) -> None:
        if not sensor_id:
            raise ValueError("rotation scheduler sensor_id must be non-empty")
        sample_rate = _require_positive_rate(sample_rate_hz, "sample rate")
        rotation_rate = _require_positive_rate(rotation_rate_hz, "rotation rate")
        if sample_rate < rotation_rate:
            raise ValueError("sample rate must be at least the rotation rate")
        initial_angle = _require_angle(initial_angle_deg)

        self._sensor_id = sensor_id
        self._sample_rate_hz = sample_rate
        self._rotation_rate_hz = rotation_rate
        self._sample_rate_fraction = Fraction(str(sample_rate))
        self._rotation_rate_fraction = Fraction(str(rotation_rate))
        self._initial_angle_deg = initial_angle
        self._next_rotation_index = 0
        self._next_sample_index = 0

    @property
    def sensor_id(self) -> str:
        """Return the sensor handled by this scheduler."""
        return self._sensor_id

    @property
    def initial_angle_deg(self) -> float:
        """Return the stable angle at each logical rotation boundary."""
        return self._initial_angle_deg

    @property
    def next_scan_id(self) -> int:
        """Return the identifier that the next completed rotation will receive."""
        return self._next_rotation_index + 1

    @property
    def next_completion_elapsed_s(self) -> float:
        """Return the simulation time when the next rotation completes."""
        boundary = Fraction(self._next_rotation_index + 1, 1) / self._rotation_rate_fraction
        return float(boundary)

    def next_scan(self) -> ScheduledScan:
        """Build the next full rotation schedule and advance only this sensor."""
        scan_id = self.next_scan_id
        if scan_id > _MAX_SCAN_ID:
            raise OverflowError("sensor scan_id range is exhausted")

        rotation_index = self._next_rotation_index
        rotation_start = Fraction(rotation_index, 1) / self._rotation_rate_fraction
        rotation_end = Fraction(rotation_index + 1, 1) / self._rotation_rate_fraction
        end_sample_index = _ceil_fraction(rotation_end * self._sample_rate_fraction)
        if end_sample_index > np.iinfo(np.int64).max:
            raise OverflowError("sensor sample index exceeds the supported integer range")
        sample_indices = np.arange(
            self._next_sample_index,
            end_sample_index,
            dtype=np.int64,
        )
        if sample_indices.size == 0:
            raise RuntimeError("rotation schedule produced no measurement points")

        sample_indices_float = sample_indices.astype(np.float64)
        point_elapsed_times_s = sample_indices_float / self._sample_rate_hz
        rotation_progress = point_elapsed_times_s * self._rotation_rate_hz - rotation_index
        angles_deg = np.remainder(
            self._initial_angle_deg + 360.0 * rotation_progress,
            360.0,
        )
        result = ScheduledScan(
            sensor_id=self._sensor_id,
            scan_id=scan_id,
            rotation_started_at_s=float(rotation_start),
            completed_at_s=float(rotation_end),
            angles_deg=angles_deg,
            point_elapsed_times_s=point_elapsed_times_s,
        )
        self._next_rotation_index += 1
        self._next_sample_index = end_sample_index
        return result


def create_seeded_rotation_scheduler(
    *,
    sensor_id: str,
    sample_rate_hz: float,
    rotation_rate_hz: float,
    seed: int,
) -> SensorRotationScheduler:
    """Create a sensor scheduler with a stable seed-derived initial angle."""
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= _MAX_SEED:
        raise ValueError("rotation seed must be an unsigned 64-bit integer")
    if not sensor_id:
        raise ValueError("rotation scheduler sensor_id must be non-empty")

    payload = b"sensor-rotation\0" + seed.to_bytes(8, byteorder="big") + sensor_id.encode("utf-8")
    digest_value = int.from_bytes(hashlib.sha256(payload).digest(), byteorder="big")
    initial_angle_deg = 360.0 * digest_value / (1 << 256)
    return SensorRotationScheduler(
        sensor_id=sensor_id,
        sample_rate_hz=sample_rate_hz,
        rotation_rate_hz=rotation_rate_hz,
        initial_angle_deg=initial_angle_deg,
    )


def _ceil_fraction(value: Fraction) -> int:
    return -(-value.numerator // value.denominator)


def _require_positive_rate(value: float, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite positive number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{name} must be a finite positive number") from error
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError(f"{name} must be a finite positive number")
    return result


def _require_angle(value: float) -> float:
    if isinstance(value, bool):
        raise ValueError("initial angle must be finite and in [0, 360)")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("initial angle must be finite and in [0, 360)") from error
    if not math.isfinite(result) or not 0.0 <= result < 360.0:
        raise ValueError("initial angle must be finite and in [0, 360)")
    return result
