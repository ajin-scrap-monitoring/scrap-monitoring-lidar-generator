"""Read-only copies of the simulated load-model surface."""

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from scrap_monitoring_lidar_generator.scenario.height_field import HeightField

type FloatArray = NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class SurfaceModelSnapshot:
    """Immutable regular-grid values needed to observe the load surface."""

    x_coordinates_m: FloatArray
    y_coordinates_m: FloatArray
    heights_m: FloatArray
    cell_size_m: float

    def __post_init__(self) -> None:
        x_coordinates_m = _readonly_vector(self.x_coordinates_m, "x coordinates")
        y_coordinates_m = _readonly_vector(self.y_coordinates_m, "y coordinates")
        heights_m = _readonly_matrix(self.heights_m, "heights")
        if x_coordinates_m.size < 2 or y_coordinates_m.size < 2:
            raise ValueError("surface snapshot coordinates must contain at least 2 nodes")
        if heights_m.shape != (y_coordinates_m.size, x_coordinates_m.size):
            raise ValueError("surface snapshot heights must match coordinate dimensions")
        if not math.isfinite(self.cell_size_m) or self.cell_size_m <= 0.0:
            raise ValueError("surface snapshot cell size must be finite and positive")
        if not bool(np.all(np.diff(x_coordinates_m) > 0.0)):
            raise ValueError("surface snapshot x coordinates must be strictly increasing")
        if not bool(np.all(np.diff(y_coordinates_m) > 0.0)):
            raise ValueError("surface snapshot y coordinates must be strictly increasing")
        object.__setattr__(self, "x_coordinates_m", x_coordinates_m)
        object.__setattr__(self, "y_coordinates_m", y_coordinates_m)
        object.__setattr__(self, "heights_m", heights_m)

    @classmethod
    def from_height_field(cls, surface: HeightField) -> SurfaceModelSnapshot:
        """Copy the public grid state without exposing mutable simulation storage."""
        return cls(
            x_coordinates_m=surface.x_coordinates_m,
            y_coordinates_m=surface.y_coordinates_m,
            heights_m=surface.heights_m,
            cell_size_m=surface.cell_size_m,
        )

    @property
    def shape(self) -> tuple[int, int]:
        """Return the y-major height matrix shape."""
        return self.heights_m.shape


def _readonly_vector(value: FloatArray, name: str) -> FloatArray:
    result = np.array(value, dtype=np.float64, copy=True)
    if result.ndim != 1 or not bool(np.all(np.isfinite(result))):
        raise ValueError(f"surface snapshot {name} must be a finite vector")
    result.flags.writeable = False
    return result


def _readonly_matrix(value: FloatArray, name: str) -> FloatArray:
    result = np.array(value, dtype=np.float64, copy=True)
    if result.ndim != 2 or not bool(np.all(np.isfinite(result))):
        raise ValueError(f"surface snapshot {name} must be a finite matrix")
    result.flags.writeable = False
    return result
