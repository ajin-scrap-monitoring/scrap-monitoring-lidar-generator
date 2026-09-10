"""Sensor measurement frame in environment coordinates."""

import math
from dataclasses import dataclass

from scrap_monitoring_lidar_generator.geometry.primitives import Ray, Vec3

_FRAME_TOLERANCE = 1e-6


@dataclass(frozen=True, slots=True)
class SensorFrame:
    """Sensor origin and orthonormal directions for 0 and 90 degrees."""

    origin_m: Vec3
    u0: Vec3
    u90: Vec3

    def __post_init__(self) -> None:
        if not math.isclose(
            self.u0.length(),
            1.0,
            rel_tol=0.0,
            abs_tol=_FRAME_TOLERANCE,
        ):
            raise ValueError("sensor u0 must be a unit vector")
        if not math.isclose(
            self.u90.length(),
            1.0,
            rel_tol=0.0,
            abs_tol=_FRAME_TOLERANCE,
        ):
            raise ValueError("sensor u90 must be a unit vector")
        if not math.isclose(
            self.u0.dot(self.u90),
            0.0,
            rel_tol=0.0,
            abs_tol=_FRAME_TOLERANCE,
        ):
            raise ValueError("sensor u0 and u90 must be orthogonal")

    def ray_at(self, angle_deg: float) -> Ray:
        """Return the environment-space ray for a sensor angle."""
        angle = _coerce_angle_deg(angle_deg)
        angle_rad = math.radians(angle)
        direction = math.cos(angle_rad) * self.u0 + math.sin(angle_rad) * self.u90
        return Ray(self.origin_m, direction.normalized())


def _coerce_angle_deg(angle_deg: float) -> float:
    if isinstance(angle_deg, bool):
        raise ValueError("sensor angle must be a finite number")
    try:
        angle = float(angle_deg)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("sensor angle must be a finite number") from error
    if not math.isfinite(angle):
        raise ValueError("sensor angle must be a finite number")
    if angle < 0.0 or angle >= 360.0:
        raise ValueError("sensor angle must be at least 0 and less than 360 degrees")
    return angle
