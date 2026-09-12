"""Small deterministic meshes used by the engineering renderer."""

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from scrap_monitoring_lidar_generator.configuration import EnvironmentConfig
from scrap_monitoring_lidar_generator.observation import ObservationRecord

type FloatArray = NDArray[np.float64]
type IndexArray = NDArray[np.int64]


@dataclass(frozen=True, slots=True)
class Mesh:
    """Triangle mesh with immutable vertex and face arrays."""

    vertices: FloatArray
    faces: IndexArray

    def __post_init__(self) -> None:
        vertices = np.array(self.vertices, dtype=np.float64, copy=True)
        faces = np.array(self.faces, dtype=np.int64, copy=True)
        if vertices.ndim != 2 or vertices.shape[1] != 3:
            raise ValueError("mesh vertices must have shape (n, 3)")
        if faces.ndim != 2 or faces.shape[1] != 3:
            raise ValueError("mesh faces must have shape (n, 3)")
        if faces.size and (np.min(faces) < 0 or np.max(faces) >= len(vertices)):
            raise ValueError("mesh face index is outside vertex range")
        vertices.flags.writeable = False
        faces.flags.writeable = False
        object.__setattr__(self, "vertices", vertices)
        object.__setattr__(self, "faces", faces)


def build_surface_mesh(record: ObservationRecord) -> Mesh:
    """Build two triangles for every regular-grid surface cell."""
    surface = record.snapshot.surface
    x_values = surface.x_coordinates_m
    y_values = surface.y_coordinates_m
    heights_m = surface.heights_m
    x_grid, y_grid = np.meshgrid(x_values, y_values)
    vertices = np.column_stack((x_grid.ravel(), y_grid.ravel(), heights_m.ravel()))
    x_count = len(x_values)
    y_count = len(y_values)
    faces: list[tuple[int, int, int]] = []
    for row in range(y_count - 1):
        for column in range(x_count - 1):
            lower_left = row * x_count + column
            lower_right = lower_left + 1
            upper_left = lower_left + x_count
            upper_right = upper_left + 1
            faces.extend(
                (
                    (lower_left, lower_right, upper_right),
                    (lower_left, upper_right, upper_left),
                )
            )
    return Mesh(vertices, np.asarray(faces, dtype=np.int64))


def build_environment_mesh(environment: EnvironmentConfig) -> Mesh:
    """Build floor and outer-wall triangles from the public environment polygon."""
    boundary = environment.boundary_xy_m
    vertex_count = len(boundary)
    floor_vertices = [(x, y, environment.floor_z_m) for x, y in boundary]
    top_vertices = [(x, y, environment.top_z_m) for x, y in boundary]
    faces: list[tuple[int, int, int]] = []
    for index in range(1, vertex_count - 1):
        faces.append((0, index + 1, index))
    for index in range(vertex_count):
        next_index = (index + 1) % vertex_count
        top_index = vertex_count + index
        next_top_index = vertex_count + next_index
        faces.extend(
            (
                (index, next_index, next_top_index),
                (index, next_top_index, top_index),
            )
        )
    return Mesh(
        np.asarray(floor_vertices + top_vertices, dtype=np.float64),
        np.asarray(faces, dtype=np.int64),
    )
