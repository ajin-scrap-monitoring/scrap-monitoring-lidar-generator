"""Reference measurement result models."""

import math
from dataclasses import dataclass

from scrap_monitoring_lidar_generator.geometry.scene import HitKind


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
