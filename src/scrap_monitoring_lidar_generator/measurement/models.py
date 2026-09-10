"""Reference and measured scan result models."""

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from scrap_monitoring_lidar_generator.geometry.scene import HitKind
from scrap_monitoring_lidar_generator.measurement.rotation import ScheduledScan

type FloatArray = NDArray[np.float64]
type QualityArray = NDArray[np.uint8]


@dataclass(frozen=True, slots=True)
class ReferencePoint:
    """Undistorted distance and target for one sensor angle."""

    angle_deg: float
    distance_m: float
    hit_kind: HitKind | None

    def __post_init__(self) -> None:
        if not math.isfinite(self.angle_deg) or self.angle_deg < 0.0 or self.angle_deg >= 360.0:
            raise ValueError("reference point angle must be finite and in [0, 360)")
        if not math.isfinite(self.distance_m) or self.distance_m < 0.0:
            raise ValueError("reference point distance must be finite and non-negative")
        if (self.distance_m == 0.0) != (self.hit_kind is None):
            raise ValueError("only a reference point without a hit may have zero distance")


@dataclass(frozen=True, slots=True)
class ReferenceScan:
    """Ordered undistorted measurements from one completed rotation."""

    sensor_id: str
    points: tuple[ReferencePoint, ...]

    def __post_init__(self) -> None:
        if not self.sensor_id:
            raise ValueError("reference scan sensor_id must be non-empty")
        points = tuple(self.points)
        if not points:
            raise ValueError("reference scan must contain at least one point")
        object.__setattr__(self, "points", points)


@dataclass(frozen=True, slots=True)
class TimedReferenceScan:
    """Reference measurements paired with their completed rotation schedule."""

    schedule: ScheduledScan
    scan: ReferenceScan

    def __post_init__(self) -> None:
        if self.schedule.sensor_id != self.scan.sensor_id:
            raise ValueError("timed reference scan sensor identifiers must match")
        if self.schedule.point_count != len(self.scan.points):
            raise ValueError("timed reference scan point counts must match")
        if any(
            point.angle_deg != float(angle_deg)
            for point, angle_deg in zip(
                self.scan.points,
                self.schedule.angles_deg,
                strict=True,
            )
        ):
            raise ValueError("timed reference scan point angles must match its schedule")

    @property
    def sensor_id(self) -> str:
        """Return the source sensor identifier."""
        return self.schedule.sensor_id

    @property
    def scan_id(self) -> int:
        """Return the sensor-local completed scan sequence."""
        return self.schedule.scan_id

    @property
    def captured_elapsed_s(self) -> float:
        """Return the simulation time of the first measurement point."""
        return self.schedule.captured_elapsed_s

    @property
    def completed_at_s(self) -> float:
        """Return the logical rotation completion time."""
        return self.schedule.completed_at_s


class MeasuredScan:
    """Read-only final distances and quality values for one completed rotation."""

    __slots__ = ("_angles_deg", "_distances_m", "_qualities", "sensor_id")

    def __init__(
        self,
        *,
        sensor_id: str,
        angles_deg: FloatArray,
        distances_m: FloatArray,
        qualities: QualityArray,
    ) -> None:
        if not sensor_id:
            raise ValueError("measured scan sensor_id must be non-empty")

        angles = np.array(angles_deg, dtype=np.float64, copy=True)
        distances = np.array(distances_m, dtype=np.float64, copy=True)
        quality_values = np.array(qualities, copy=True)
        if (
            angles.ndim != 1
            or distances.ndim != 1
            or quality_values.ndim != 1
            or angles.shape != distances.shape
            or angles.shape != quality_values.shape
        ):
            raise ValueError("measured scan arrays must have the same 1D shape")
        if angles.size == 0:
            raise ValueError("measured scan must contain at least one point")
        if (
            not bool(np.all(np.isfinite(angles)))
            or bool(np.any(angles < 0.0))
            or bool(np.any(angles >= 360.0))
        ):
            raise ValueError("measured scan angles must be finite and in [0, 360)")
        if not bool(np.all(np.isfinite(distances))) or bool(np.any(distances < 0.0)):
            raise ValueError("measured scan distances must be finite and non-negative")
        if not np.issubdtype(quality_values.dtype, np.integer):
            raise ValueError("measured scan qualities must be integers")
        if bool(np.any(quality_values < 0)) or bool(np.any(quality_values > 255)):
            raise ValueError("measured scan qualities must be in [0, 255]")

        qualities_uint8 = quality_values.astype(np.uint8, copy=False)
        angles.flags.writeable = False
        distances.flags.writeable = False
        qualities_uint8.flags.writeable = False
        self.sensor_id = sensor_id
        self._angles_deg = angles
        self._distances_m = distances
        self._qualities = qualities_uint8

    @property
    def point_count(self) -> int:
        """Return the number of measured points."""
        return int(self._angles_deg.size)

    @property
    def angles_deg(self) -> FloatArray:
        """Return the read-only angle array in measurement order."""
        return self._angles_deg

    @property
    def distances_m(self) -> FloatArray:
        """Return the read-only final distance array in measurement order."""
        return self._distances_m

    @property
    def qualities(self) -> QualityArray:
        """Return the read-only 8-bit quality array in measurement order."""
        return self._qualities


@dataclass(frozen=True, slots=True)
class TimedMeasuredScan:
    """Final measured values paired with their completed rotation schedule."""

    schedule: ScheduledScan
    scan: MeasuredScan

    def __post_init__(self) -> None:
        if self.schedule.sensor_id != self.scan.sensor_id:
            raise ValueError("timed measured scan sensor identifiers must match")
        if self.schedule.point_count != self.scan.point_count:
            raise ValueError("timed measured scan point counts must match")
        if not np.array_equal(self.schedule.angles_deg, self.scan.angles_deg):
            raise ValueError("timed measured scan point angles must match its schedule")

    @property
    def sensor_id(self) -> str:
        """Return the source sensor identifier."""
        return self.schedule.sensor_id

    @property
    def scan_id(self) -> int:
        """Return the sensor-local completed scan sequence."""
        return self.schedule.scan_id

    @property
    def captured_elapsed_s(self) -> float:
        """Return the simulation time of the first measurement point."""
        return self.schedule.captured_elapsed_s

    @property
    def completed_at_s(self) -> float:
        """Return the logical rotation completion time."""
        return self.schedule.completed_at_s


@dataclass(frozen=True, slots=True)
class MeasurementResult:
    """Separate reference and final values for one completed sensor rotation."""

    reference: TimedReferenceScan
    measured: TimedMeasuredScan

    def __post_init__(self) -> None:
        if self.reference.schedule is not self.measured.schedule:
            raise ValueError("measurement result scans must share one rotation schedule")

    @property
    def sensor_id(self) -> str:
        """Return the source sensor identifier."""
        return self.measured.sensor_id

    @property
    def scan_id(self) -> int:
        """Return the sensor-local completed scan sequence."""
        return self.measured.scan_id
