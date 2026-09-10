"""Validated batches of environment-space rays."""

from collections.abc import Iterable

import numpy as np
from numpy.typing import NDArray

from scrap_monitoring_lidar_generator.geometry.primitives import Ray

type FloatArray = NDArray[np.float64]

_UNIT_VECTOR_TOLERANCE = 1e-6


class RayBatch:
    """Immutable-shape arrays of finite origins and unit directions."""

    __slots__ = ("_directions", "_origins_m")

    def __init__(self, origins_m: FloatArray, directions: FloatArray) -> None:
        origins = np.array(origins_m, dtype=np.float64, copy=True)
        direction_values = np.array(directions, dtype=np.float64, copy=True)
        if origins.ndim != 2 or origins.shape[1:] != (3,):
            raise ValueError("ray batch origins must have shape (count, 3)")
        if direction_values.shape != origins.shape:
            raise ValueError("ray batch directions must match origin shape")
        if not bool(np.all(np.isfinite(origins))) or not bool(
            np.all(np.isfinite(direction_values))
        ):
            raise ValueError("ray batch coordinates must be finite")
        lengths = np.sqrt(np.sum(np.square(direction_values), axis=1))
        if not bool(
            np.all(
                np.isclose(
                    lengths,
                    1.0,
                    rtol=0.0,
                    atol=_UNIT_VECTOR_TOLERANCE,
                )
            )
        ):
            raise ValueError("ray batch directions must be unit vectors")

        origins.flags.writeable = False
        direction_values.flags.writeable = False
        self._origins_m = origins
        self._directions = direction_values

    @classmethod
    def from_rays(cls, rays: Iterable[Ray]) -> RayBatch:
        """Build a batch from validated scalar rays in iteration order."""
        ray_values = tuple(rays)
        origins_m = np.empty((len(ray_values), 3), dtype=np.float64)
        directions = np.empty((len(ray_values), 3), dtype=np.float64)
        for index, ray in enumerate(ray_values):
            origins_m[index] = (ray.origin.x, ray.origin.y, ray.origin.z)
            directions[index] = (ray.direction.x, ray.direction.y, ray.direction.z)
        return cls(origins_m, directions)

    @property
    def count(self) -> int:
        """Return the number of rays."""
        return int(self._origins_m.shape[0])

    @property
    def origins_m(self) -> FloatArray:
        """Return the read-only origin array."""
        return self._origins_m

    @property
    def directions(self) -> FloatArray:
        """Return the read-only unit direction array."""
        return self._directions
