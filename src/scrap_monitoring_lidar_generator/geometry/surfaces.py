"""Structural interface for ray-intersectable environment surfaces."""

import math
from typing import Protocol

from scrap_monitoring_lidar_generator.geometry.intersections import DEFAULT_MIN_DISTANCE_M
from scrap_monitoring_lidar_generator.geometry.polygon import Polygon2
from scrap_monitoring_lidar_generator.geometry.primitives import Ray


class RaySurface(Protocol):
    """Surface model that can return its nearest ray intersection."""

    @property
    def boundary(self) -> Polygon2:
        """Return the horizontal domain of the surface."""
        ...

    @property
    def floor_z_m(self) -> float:
        """Return the lower surface height bound."""
        ...

    @property
    def top_z_m(self) -> float:
        """Return the upper surface height bound."""
        ...

    def intersect_ray(
        self,
        ray: Ray,
        *,
        min_distance_m: float = DEFAULT_MIN_DISTANCE_M,
        max_distance_m: float = math.inf,
    ) -> float | None:
        """Return the nearest allowed distance to the surface."""
        ...
