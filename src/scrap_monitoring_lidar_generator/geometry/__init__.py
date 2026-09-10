"""Geometry models and intersection calculations."""

from scrap_monitoring_lidar_generator.geometry.batches import RayBatch
from scrap_monitoring_lidar_generator.geometry.intersections import intersect_triangle
from scrap_monitoring_lidar_generator.geometry.polygon import Polygon2
from scrap_monitoring_lidar_generator.geometry.primitives import Ray, Triangle, Vec2, Vec3
from scrap_monitoring_lidar_generator.geometry.scene import (
    EnvironmentScene,
    HitKind,
    RayHit,
    RayHitBatch,
)
from scrap_monitoring_lidar_generator.geometry.sensor import SensorFrame
from scrap_monitoring_lidar_generator.geometry.surfaces import RaySurface

__all__ = [
    "EnvironmentScene",
    "HitKind",
    "Polygon2",
    "Ray",
    "RayBatch",
    "RayHit",
    "RayHitBatch",
    "RaySurface",
    "SensorFrame",
    "Triangle",
    "Vec2",
    "Vec3",
    "intersect_triangle",
]
