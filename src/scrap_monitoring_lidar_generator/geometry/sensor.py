"""Sensor measurement frame in environment coordinates."""

import math
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from scrap_monitoring_lidar_generator.geometry.batches import RayBatch
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

    def ray_batch_at(self, angles_deg: Iterable[float]) -> RayBatch:
        """Return environment-space rays for angles in iteration order."""
        angles = np.fromiter(
            (_coerce_angle_deg(angle_deg) for angle_deg in angles_deg),
            dtype=np.float64,
        )
        angle_radians = np.deg2rad(angles)
        u0 = np.array((self.u0.x, self.u0.y, self.u0.z), dtype=np.float64)
        u90 = np.array((self.u90.x, self.u90.y, self.u90.z), dtype=np.float64)
        directions = (
            np.cos(angle_radians[:, np.newaxis]) * u0 + np.sin(angle_radians[:, np.newaxis]) * u90
        )
        lengths = np.sqrt(np.sum(np.square(directions), axis=1))
        np.divide(directions, lengths[:, np.newaxis], out=directions)
        origin = np.array(
            (self.origin_m.x, self.origin_m.y, self.origin_m.z),
            dtype=np.float64,
        )
        origins_m = np.broadcast_to(origin, directions.shape)
        return RayBatch(origins_m, directions)


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
