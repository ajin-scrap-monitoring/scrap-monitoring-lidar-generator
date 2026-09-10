"""Vectorized ray intersection for a regular bilinear height field."""

import math

import numpy as np
from numpy.typing import NDArray

from scrap_monitoring_lidar_generator.geometry.batch_intersections import contains_xy
from scrap_monitoring_lidar_generator.geometry.batches import FloatArray, RayBatch
from scrap_monitoring_lidar_generator.geometry.intersections import validate_distance_bounds
from scrap_monitoring_lidar_generator.geometry.polygon import Polygon2

type BoolArray = NDArray[np.bool]
type IntegerArray = NDArray[np.int64]
type ByteArray = NDArray[np.uint8]

_DISTANCE_TOLERANCE = 1e-10
_POLYNOMIAL_TOLERANCE = 1e-12
_OUTSIDE_CELL = 0
_PARTIAL_CELL = 1


def intersect_height_field_batch(
    rays: RayBatch,
    *,
    boundary: Polygon2,
    x_coordinates_m: FloatArray,
    y_coordinates_m: FloatArray,
    heights_m: FloatArray,
    cell_coverage: ByteArray,
    cell_size_m: float,
    min_distance_m: float,
    max_distance_m: float,
) -> FloatArray:
    """Return nearest height-field distances with infinity for misses."""
    validate_distance_bounds(min_distance_m, max_distance_m)
    results_m = np.full(rays.count, math.inf, dtype=np.float64)
    if rays.count == 0:
        return results_m

    entry_m = np.full(rays.count, min_distance_m, dtype=np.float64)
    exit_m = np.full(rays.count, max_distance_m, dtype=np.float64)
    active = np.ones(rays.count, dtype=np.bool)
    for axis, lower_m, upper_m in (
        (0, float(x_coordinates_m[0]), float(x_coordinates_m[-1])),
        (1, float(y_coordinates_m[0]), float(y_coordinates_m[-1])),
        (2, float(np.min(heights_m)), float(np.max(heights_m))),
    ):
        _clip_intervals(
            origins_m=rays.origins_m[:, axis],
            directions=rays.directions[:, axis],
            lower_m=lower_m,
            upper_m=upper_m,
            entry_m=entry_m,
            exit_m=exit_m,
            active=active,
        )
    active &= exit_m >= entry_m
    current_m = entry_m.copy()

    x_cell_count = heights_m.shape[1] - 1
    y_cell_count = heights_m.shape[0] - 1
    entry_x_m = rays.origins_m[:, 0] + rays.directions[:, 0] * entry_m
    entry_y_m = rays.origins_m[:, 1] + rays.directions[:, 1] * entry_m
    x_indices = _cell_indices(
        entry_x_m,
        rays.directions[:, 0],
        minimum_m=float(x_coordinates_m[0]),
        cell_size_m=cell_size_m,
        cell_count=x_cell_count,
    )
    y_indices = _cell_indices(
        entry_y_m,
        rays.directions[:, 1],
        minimum_m=float(y_coordinates_m[0]),
        cell_size_m=cell_size_m,
        cell_count=y_cell_count,
    )
    next_x_m, x_delta_m, x_steps = _grid_axis_steps(
        origins_m=rays.origins_m[:, 0],
        directions=rays.directions[:, 0],
        cell_indices=x_indices,
        entry_m=entry_m,
        minimum_m=float(x_coordinates_m[0]),
        cell_size_m=cell_size_m,
    )
    next_y_m, y_delta_m, y_steps = _grid_axis_steps(
        origins_m=rays.origins_m[:, 1],
        directions=rays.directions[:, 1],
        cell_indices=y_indices,
        entry_m=entry_m,
        minimum_m=float(y_coordinates_m[0]),
        cell_size_m=cell_size_m,
    )
    maximum_step_count = x_cell_count + y_cell_count + 2
    for _ in range(maximum_step_count):
        ray_indices = np.flatnonzero(active)
        if ray_indices.size == 0:
            break

        segment_start_m = current_m[ray_indices]
        segment_limit_m = exit_m[ray_indices]
        origins_m = rays.origins_m[ray_indices]
        directions = rays.directions[ray_indices]
        local_next_x_m = next_x_m[ray_indices]
        local_next_y_m = next_y_m[ray_indices]
        segment_end_m = np.minimum(
            segment_limit_m,
            np.minimum(local_next_x_m, local_next_y_m),
        )

        candidates_m = _intersect_cells(
            origins_m=origins_m,
            directions=directions,
            segment_start_m=segment_start_m,
            segment_end_m=segment_end_m,
            x_indices=x_indices[ray_indices],
            y_indices=y_indices[ray_indices],
            x_coordinates_m=x_coordinates_m,
            y_coordinates_m=y_coordinates_m,
            heights_m=heights_m,
            cell_coverage=cell_coverage,
            cell_size_m=cell_size_m,
            boundary=boundary,
        )
        hit = np.isfinite(candidates_m)
        if bool(np.any(hit)):
            hit_indices = ray_indices[hit]
            results_m[hit_indices] = candidates_m[hit]
            active[hit_indices] = False

        unfinished = ~hit & (segment_end_m < segment_limit_m - _DISTANCE_TOLERANCE)
        finished_indices = ray_indices[~hit & ~unfinished]
        active[finished_indices] = False
        continuing_indices = ray_indices[unfinished]
        current_m[continuing_indices] = segment_end_m[unfinished]
        crosses_x = unfinished & (local_next_x_m <= segment_end_m + _DISTANCE_TOLERANCE)
        crosses_y = unfinished & (local_next_y_m <= segment_end_m + _DISTANCE_TOLERANCE)
        x_crossing_indices = ray_indices[crosses_x]
        y_crossing_indices = ray_indices[crosses_y]
        x_indices[x_crossing_indices] += x_steps[x_crossing_indices]
        y_indices[y_crossing_indices] += y_steps[y_crossing_indices]
        next_x_m[x_crossing_indices] += x_delta_m[x_crossing_indices]
        next_y_m[y_crossing_indices] += y_delta_m[y_crossing_indices]
        outside_grid = (
            (x_indices < 0)
            | (x_indices >= x_cell_count)
            | (y_indices < 0)
            | (y_indices >= y_cell_count)
        )
        active[outside_grid] = False
    if bool(np.any(active)):
        raise RuntimeError("height field batch traversal exceeded the grid step bound")
    return results_m


def _clip_intervals(
    *,
    origins_m: FloatArray,
    directions: FloatArray,
    lower_m: float,
    upper_m: float,
    entry_m: FloatArray,
    exit_m: FloatArray,
    active: BoolArray,
) -> None:
    moving = directions != 0.0
    stationary = ~moving
    active[stationary & ((origins_m < lower_m) | (origins_m > upper_m))] = False

    first_m = np.zeros(origins_m.shape, dtype=np.float64)
    second_m = np.zeros(origins_m.shape, dtype=np.float64)
    np.divide(lower_m - origins_m, directions, out=first_m, where=moving)
    np.divide(upper_m - origins_m, directions, out=second_m, where=moving)
    near_m = np.minimum(first_m, second_m)
    far_m = np.maximum(first_m, second_m)
    entry_m[moving] = np.maximum(entry_m[moving], near_m[moving])
    exit_m[moving] = np.minimum(exit_m[moving], far_m[moving])
    active &= exit_m >= entry_m


def _cell_indices(
    values_m: FloatArray,
    directions: FloatArray,
    *,
    minimum_m: float,
    cell_size_m: float,
    cell_count: int,
) -> IntegerArray:
    scaled = (values_m - minimum_m) / cell_size_m
    rounded = np.rint(scaled)
    on_grid_line = np.isclose(scaled, rounded, rtol=0.0, atol=_DISTANCE_TOLERANCE)
    indices = np.floor(scaled).astype(np.int64)
    indices -= ((directions < 0.0) & on_grid_line).astype(np.int64)
    return np.clip(indices, 0, cell_count - 1)


def _grid_axis_steps(
    *,
    origins_m: FloatArray,
    directions: FloatArray,
    cell_indices: IntegerArray,
    entry_m: FloatArray,
    minimum_m: float,
    cell_size_m: float,
) -> tuple[FloatArray, FloatArray, IntegerArray]:
    positive = directions > 0.0
    moving = directions != 0.0
    steps = np.sign(directions).astype(np.int64)
    boundary_indices = cell_indices + positive.astype(np.int64)
    boundary_coordinates_m = minimum_m + boundary_indices * cell_size_m
    crossings_m = np.full(origins_m.shape, math.inf, dtype=np.float64)
    np.divide(
        boundary_coordinates_m - origins_m,
        directions,
        out=crossings_m,
        where=moving,
    )
    deltas_m = np.full(origins_m.shape, math.inf, dtype=np.float64)
    np.divide(cell_size_m, np.abs(directions), out=deltas_m, where=moving)
    stale = moving & (crossings_m <= entry_m)
    crossings_m[stale] += deltas_m[stale]
    return crossings_m, deltas_m, steps


def _intersect_cells(
    *,
    origins_m: FloatArray,
    directions: FloatArray,
    segment_start_m: FloatArray,
    segment_end_m: FloatArray,
    x_indices: IntegerArray,
    y_indices: IntegerArray,
    x_coordinates_m: FloatArray,
    y_coordinates_m: FloatArray,
    heights_m: FloatArray,
    cell_coverage: ByteArray,
    cell_size_m: float,
    boundary: Polygon2,
) -> FloatArray:
    segment_origins_m = origins_m + directions * segment_start_m[:, np.newaxis]
    u_start = (segment_origins_m[:, 0] - x_coordinates_m[x_indices]) / cell_size_m
    v_start = (segment_origins_m[:, 1] - y_coordinates_m[y_indices]) / cell_size_m
    u_rate = directions[:, 0] / cell_size_m
    v_rate = directions[:, 1] / cell_size_m

    lower_left_z_m = heights_m[y_indices, x_indices]
    lower_right_z_m = heights_m[y_indices, x_indices + 1]
    upper_left_z_m = heights_m[y_indices + 1, x_indices]
    upper_right_z_m = heights_m[y_indices + 1, x_indices + 1]
    x_slope = lower_right_z_m - lower_left_z_m
    y_slope = upper_left_z_m - lower_left_z_m
    mixed_slope = upper_right_z_m - lower_right_z_m - upper_left_z_m + lower_left_z_m
    surface_constant = (
        lower_left_z_m + x_slope * u_start + y_slope * v_start + mixed_slope * u_start * v_start
    )
    surface_linear = (
        x_slope * u_rate + y_slope * v_rate + mixed_slope * (u_start * v_rate + v_start * u_rate)
    )
    surface_quadratic = mixed_slope * u_rate * v_rate
    roots_m = _real_root_arrays(
        -surface_quadratic,
        directions[:, 2] - surface_linear,
        segment_origins_m[:, 2] - surface_constant,
    )

    segment_lengths_m = segment_end_m - segment_start_m
    valid = (roots_m >= -_DISTANCE_TOLERANCE) & (
        roots_m <= segment_lengths_m[:, np.newaxis] + _DISTANCE_TOLERANCE
    )
    coverage = cell_coverage[y_indices, x_indices]
    valid &= coverage[:, np.newaxis] != _OUTSIDE_CELL
    bounded_roots_m = np.maximum(roots_m, 0.0)
    bounded_roots_m = np.minimum(bounded_roots_m, segment_lengths_m[:, np.newaxis])
    distances_m = segment_start_m[:, np.newaxis] + bounded_roots_m
    partial_candidates = valid & (coverage[:, np.newaxis] == _PARTIAL_CELL)
    if bool(np.any(partial_candidates)):
        ray_indices, root_indices = np.nonzero(partial_candidates)
        partial_distances_m = distances_m[ray_indices, root_indices]
        x_values = origins_m[ray_indices, 0] + directions[ray_indices, 0] * partial_distances_m
        y_values = origins_m[ray_indices, 1] + directions[ray_indices, 1] * partial_distances_m
        inside = contains_xy(boundary, x_values, y_values)
        valid[ray_indices, root_indices] &= inside
    return np.min(np.where(valid, distances_m, math.inf), axis=1)


def _real_root_arrays(
    quadratic: FloatArray,
    linear: FloatArray,
    constant: FloatArray,
) -> FloatArray:
    roots = np.full((quadratic.size, 2), math.inf, dtype=np.float64)
    linear_equations = (quadratic == 0.0) & (linear != 0.0)
    roots[linear_equations, 0] = -constant[linear_equations] / linear[linear_equations]

    quadratic_equations = quadratic != 0.0
    discriminants = linear * linear - 4.0 * quadratic * constant
    discriminant_scale = np.maximum(
        1.0,
        np.maximum(linear * linear, np.abs(4.0 * quadratic * constant)),
    )
    has_real_roots = quadratic_equations & (
        discriminants >= -_POLYNOMIAL_TOLERANCE * discriminant_scale
    )
    square_roots = np.sqrt(np.maximum(discriminants[has_real_roots], 0.0))
    selected_linear = linear[has_real_roots]
    selected_quadratic = quadratic[has_real_roots]
    selected_constant = constant[has_real_roots]
    stable_terms = -0.5 * (selected_linear + np.copysign(square_roots, selected_linear))
    repeated = stable_terms == 0.0
    first_roots = np.empty(stable_terms.shape, dtype=np.float64)
    second_roots = np.empty(stable_terms.shape, dtype=np.float64)
    first_roots[repeated] = -selected_linear[repeated] / (2.0 * selected_quadratic[repeated])
    second_roots[repeated] = first_roots[repeated]
    first_roots[~repeated] = stable_terms[~repeated] / selected_quadratic[~repeated]
    second_roots[~repeated] = selected_constant[~repeated] / stable_terms[~repeated]
    roots[has_real_roots, 0] = np.minimum(first_roots, second_roots)
    roots[has_real_roots, 1] = np.maximum(first_roots, second_roots)
    return roots
