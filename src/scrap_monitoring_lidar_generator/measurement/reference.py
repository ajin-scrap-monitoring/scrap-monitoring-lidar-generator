"""Undistorted reference scan generation."""

import math
from collections.abc import Iterable
from dataclasses import dataclass

from scrap_monitoring_lidar_generator.geometry.intersections import validate_distance_bounds
from scrap_monitoring_lidar_generator.geometry.scene import EnvironmentScene
from scrap_monitoring_lidar_generator.geometry.sensor import SensorFrame
from scrap_monitoring_lidar_generator.measurement.models import ReferencePoint, ReferenceScan

DEFAULT_MIN_DISTANCE_M = 0.05
DEFAULT_MAX_DISTANCE_M = 30.0


@dataclass(frozen=True, slots=True)
class ReferenceScanner:
    """Calculate undistorted measurements for one sensor frame."""

    sensor_id: str
    frame: SensorFrame
    min_distance_m: float = DEFAULT_MIN_DISTANCE_M
    max_distance_m: float = DEFAULT_MAX_DISTANCE_M

    def __post_init__(self) -> None:
        if not self.sensor_id:
            raise ValueError("reference scanner sensor_id must be non-empty")
        validate_distance_bounds(self.min_distance_m, self.max_distance_m)
        if self.min_distance_m == 0.0:
            raise ValueError("minimum measurable distance must be greater than zero")
        if not math.isfinite(self.max_distance_m):
            raise ValueError("maximum measurable distance must be finite")

    def generate(
        self,
        scene: EnvironmentScene,
        angles_deg: Iterable[float],
    ) -> ReferenceScan:
        """Generate reference points in the supplied measurement order."""
        angle_values = tuple(angles_deg)
        rays = self.frame.ray_batch_at(angle_values)
        angles = tuple(float(angle_deg) for angle_deg in angle_values)
        hits = scene.first_hit_batch(
            rays,
            min_distance_m=self.min_distance_m,
            max_distance_m=self.max_distance_m,
        )
        points = tuple(
            ReferencePoint(
                angle_deg=angle,
                distance_m=0.0 if kind is None else float(distance_m),
                hit_kind=kind,
            )
            for angle, distance_m, kind in zip(
                angles,
                hits.distances_m,
                hits.hit_kinds,
                strict=True,
            )
        )
        return ReferenceScan(sensor_id=self.sensor_id, points=points)

    def measure(self, scene: EnvironmentScene, angle_deg: float) -> ReferencePoint:
        """Calculate one reference point against the supplied scene state."""
        ray = self.frame.ray_at(angle_deg)
        angle = float(angle_deg)
        hit = scene.first_hit(
            ray,
            min_distance_m=self.min_distance_m,
            max_distance_m=self.max_distance_m,
        )
        if hit is None:
            return ReferencePoint(angle_deg=angle, distance_m=0.0, hit_kind=None)
        return ReferencePoint(
            angle_deg=angle,
            distance_m=hit.distance_m,
            hit_kind=hit.kind,
        )
