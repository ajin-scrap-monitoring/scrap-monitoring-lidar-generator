"""Geometry models and intersection calculations."""

from scrap_monitoring_lidar_generator.geometry.intersections import intersect_triangle
from scrap_monitoring_lidar_generator.geometry.polygon import Polygon2
from scrap_monitoring_lidar_generator.geometry.primitives import Ray, Triangle, Vec2, Vec3
from scrap_monitoring_lidar_generator.geometry.scene import EnvironmentScene, HitKind, RayHit

__all__ = [
    "EnvironmentScene",
    "HitKind",
    "Polygon2",
    "Ray",
    "RayHit",
    "Triangle",
    "Vec2",
    "Vec3",
    "intersect_triangle",
]
