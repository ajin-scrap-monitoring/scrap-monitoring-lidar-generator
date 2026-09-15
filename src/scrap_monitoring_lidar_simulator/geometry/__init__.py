"""Geometry models and intersection calculations."""

from scrap_monitoring_lidar_simulator.geometry.batches import RayBatch
from scrap_monitoring_lidar_simulator.geometry.intersections import intersect_triangle
from scrap_monitoring_lidar_simulator.geometry.polygon import Polygon2
from scrap_monitoring_lidar_simulator.geometry.primitives import Ray, Triangle, Vec2, Vec3
from scrap_monitoring_lidar_simulator.geometry.scene import (
    EnvironmentScene,
    HitKind,
    RayHit,
    RayHitBatch,
)
from scrap_monitoring_lidar_simulator.geometry.sensor import SensorFrame
from scrap_monitoring_lidar_simulator.geometry.surfaces import RaySurface

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
