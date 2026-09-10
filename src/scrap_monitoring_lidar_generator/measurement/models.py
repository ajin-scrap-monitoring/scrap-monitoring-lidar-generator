"""Reference measurement result models."""

import math
from dataclasses import dataclass

from scrap_monitoring_lidar_generator.geometry.scene import HitKind
from scrap_monitoring_lidar_generator.measurement.rotation import ScheduledScan


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
