"""Derive the ajin edge processing fixture from the synthetic environment."""

import json
import math
import re
from collections.abc import Mapping, Sequence
from itertools import pairwise
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np
from numpy.typing import NDArray

from scrap_monitoring_lidar_simulator.configuration import GeneratorInputs, SensorConfig
from scrap_monitoring_lidar_simulator.geometry import Polygon2, Vec2

type JsonObject = dict[str, Any]
type FloatArray = NDArray[np.float64]

_BIN_WIDTH_MM = 50
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_DRIVER_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_PLANE_TOLERANCE = 1e-6
_SEGMENT_PARAMETER_TOLERANCE = 1e-12
_I64_MIN = -(1 << 63)
_I64_EXCLUSIVE_MAX = 1 << 63


class ProcessingConfigError(ValueError):
    """An environment cannot be represented by the processing contract."""


def build_synthetic_processing_config(
    inputs: GeneratorInputs,
    *,
    socket_directory: PurePosixPath,
    site_id: str,
    edge_id: str,
    config_revision: str,
) -> JsonObject:
    """Build a complete processing configuration for the synthetic environment."""
    _require_absolute_posix_directory(socket_directory)
    identities = {
        "site_id": _require_identifier(site_id, "site_id"),
        "edge_id": _require_driver_identifier(edge_id, "edge_id"),
        "config_revision": _require_driver_identifier(config_revision, "config_revision"),
    }
    result: JsonObject = dict(identities)

    environment = inputs.environment
    if len(environment.environment_id) > 128:
        raise ProcessingConfigError("calibration version must contain at most 128 characters")
    quality_by_sensor = {quality.sensor_id: quality for quality in inputs.quality_profile.sensors}
    sensor_count = len(environment.sensors)
    if sensor_count != 2:
        raise ProcessingConfigError("processing configuration requires exactly two sensors")
    if any(_DRIVER_IDENTITY.fullmatch(sensor.sensor_id) is None for sensor in environment.sensors):
        raise ProcessingConfigError("sensor identifiers must be driver-compatible")

    result.update(
        {
            "schema_version": "1.0",
            "allow_demo_calibration": True,
            "calibration": {
                "version": environment.environment_id,
                "demo": True,
                "fusion_map": [[0.0, 0.0], [1.0, 1.0]],
                "single_sensor_maps": {},
            },
            "processing": {
                "target_scan_hz": inputs.generator.measurement.rotation_rate_hz,
            },
            "sensors": [
                _sensor_processing_config(
                    sensor,
                    inputs=inputs,
                    socket_directory=socket_directory,
                    base_weight=1.0 / sensor_count,
                    minimum_valid_quality=_minimum_valid_quality(
                        quality_by_sensor[sensor.sensor_id].valid_distance_frequencies
                    ),
                )
                for sensor in environment.sensors
            ],
        }
    )
    return result


def write_synthetic_processing_config(path: Path, config: Mapping[str, Any]) -> None:
    """Write one stable UTF-8 JSON integration fixture."""
    payload = json.dumps(config, ensure_ascii=True, allow_nan=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def _sensor_processing_config(
    sensor: SensorConfig,
    *,
    inputs: GeneratorInputs,
    socket_directory: PurePosixPath,
    base_weight: float,
    minimum_valid_quality: int,
) -> JsonObject:
    rotation, translation_mm, axis_x = _processing_transform(sensor)
    roi_x_mm, angle_interval_mdeg = _select_section(
        sensor,
        axis_x=axis_x,
        boundary=Polygon2(tuple(Vec2(*point) for point in inputs.environment.boundary_xy_m)),
        floor_z_m=inputs.environment.floor_z_m,
        top_z_m=inputs.environment.top_z_m,
    )
    floor_mm = _millimetres(inputs.environment.floor_z_m)
    top_mm = _millimetres(inputs.environment.top_z_m)
    bin_count = (roi_x_mm[1] - roi_x_mm[0]) // _BIN_WIDTH_MM
    measurement = inputs.generator.measurement
    return {
        "sensor_id": sensor.sensor_id,
        "endpoint": f"unix:{socket_directory / f'{sensor.sensor_id}.sock'}",
        "sample_filter": {
            "distance_min_mm": _millimetres(measurement.min_distance_m),
            "distance_max_mm": _millimetres(measurement.max_distance_m),
            "quality_min": minimum_valid_quality,
            "angle_interval_mdeg": list(angle_interval_mdeg),
        },
        "base_weight": base_weight,
        "calibration": {
            "rotation": _matrix_values(rotation),
            "translation_mm": _vector_values(translation_mm),
            "roi_x_mm": list(roi_x_mm),
            "roi_z_mm": [floor_mm, top_mm],
            "roi_polygon_xz_mm": [
                [roi_x_mm[0], floor_mm],
                [roi_x_mm[1], floor_mm],
                [roi_x_mm[1], top_mm],
                [roi_x_mm[0], top_mm],
            ],
            "masks_x_mm": [],
            "mask_polygons_xz_mm": [],
            "bottom_mm": [floor_mm] * bin_count,
            "max_height_mm": [top_mm] * bin_count,
        },
    }


def _processing_transform(sensor: SensorConfig) -> tuple[FloatArray, FloatArray, FloatArray]:
    u0 = np.asarray(sensor.u0, dtype=np.float64)
    u90 = np.asarray(sensor.u90, dtype=np.float64)
    if abs(float(u90[2])) > _PLANE_TOLERANCE:
        raise ProcessingConfigError(f"{sensor.sensor_id} u90 must be horizontal")
    if float(u0[2]) >= -_PLANE_TOLERANCE:
        raise ProcessingConfigError(f"{sensor.sensor_id} u0 must point downward")

    axis_x = -u90
    axis_z = np.asarray((0.0, 0.0, 1.0), dtype=np.float64)
    axis_y = np.cross(axis_z, axis_x)
    world_to_section = np.vstack((axis_x, axis_y, axis_z))
    sdk_to_world = np.column_stack((u0, -u90, np.cross(u0, -u90)))
    rotation = world_to_section @ sdk_to_world
    translation_mm = world_to_section @ np.asarray(sensor.p0_m, dtype=np.float64) * 1_000.0
    if not np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-6) or not np.isclose(
        np.linalg.det(rotation), 1.0, atol=1e-6
    ):
        raise ProcessingConfigError(f"{sensor.sensor_id} cannot produce a rigid transform")
    return rotation, translation_mm, axis_x


def _select_section(
    sensor: SensorConfig,
    *,
    axis_x: FloatArray,
    boundary: Polygon2,
    floor_z_m: float,
    top_z_m: float,
) -> tuple[tuple[int, int], tuple[int, int]]:
    projections_mm = [
        _millimetres(vertex.x * axis_x[0] + vertex.y * axis_x[1]) for vertex in boundary.vertices
    ]
    first_edge = math.floor(min(projections_mm) / _BIN_WIDTH_MM) * _BIN_WIDTH_MM
    last_edge = math.ceil(max(projections_mm) / _BIN_WIDTH_MM) * _BIN_WIDTH_MM
    if not _I64_MIN <= first_edge < _I64_EXCLUSIVE_MAX:
        raise ProcessingConfigError("section edge exceeds i64")
    if not _I64_MIN <= last_edge < _I64_EXCLUSIVE_MAX:
        raise ProcessingConfigError("section edge exceeds i64")
    origin_mm = _millimetres(sensor.p0_m[0] * axis_x[0] + sensor.p0_m[1] * axis_x[1])
    valid_bins = {
        edge
        for edge in range(first_edge, last_edge, _BIN_WIDTH_MM)
        if _section_column_is_inside(
            sensor,
            section_x_m=(edge + _BIN_WIDTH_MM / 2) / 1_000.0,
            axis_x=axis_x,
            boundary=boundary,
            floor_z_m=floor_z_m,
            top_z_m=top_z_m,
        )
    }
    left = _contiguous_run_toward_origin(
        valid_bins,
        candidate_edges=range(first_edge, origin_mm, _BIN_WIDTH_MM),
        reverse=True,
    )
    right = _contiguous_run_toward_origin(
        valid_bins,
        candidate_edges=range(origin_mm, last_edge, _BIN_WIDTH_MM),
        reverse=False,
    )
    if not left and not right:
        raise ProcessingConfigError(f"{sensor.sensor_id} has no valid 50 mm section bins")
    if len(left) >= len(right):
        return (min(left), max(left) + _BIN_WIDTH_MM), (0, 90_000)
    return (min(right), max(right) + _BIN_WIDTH_MM), (270_000, 360_000)


def _contiguous_run_toward_origin(
    valid_bins: set[int],
    *,
    candidate_edges: range,
    reverse: bool,
) -> tuple[int, ...]:
    ordered = list(candidate_edges)
    if reverse:
        ordered.reverse()
    selected: list[int] = []
    for edge in ordered:
        if edge in valid_bins:
            selected.append(edge)
        elif selected:
            break
    return tuple(selected)


def _section_column_is_inside(
    sensor: SensorConfig,
    *,
    section_x_m: float,
    axis_x: FloatArray,
    boundary: Polygon2,
    floor_z_m: float,
    top_z_m: float,
) -> bool:
    u0 = np.asarray(sensor.u0, dtype=np.float64)
    u90 = np.asarray(sensor.u90, dtype=np.float64)
    origin = np.asarray(sensor.p0_m, dtype=np.float64)
    origin_section_x_m = float(origin @ axis_x)
    b = origin_section_x_m - section_x_m

    def point_at_height(height_m: float) -> Vec2:
        a = (height_m - origin[2]) / u0[2]
        point = origin + a * u0 + b * u90
        return Vec2(float(point[0]), float(point[1]))

    return _segment_is_inside_boundary(
        point_at_height(floor_z_m),
        point_at_height(top_z_m),
        boundary,
    )


def _segment_is_inside_boundary(start: Vec2, end: Vec2, boundary: Polygon2) -> bool:
    if not boundary.contains(start) or not boundary.contains(end):
        return False
    direction = (end.x - start.x, end.y - start.y)
    length_squared = _dot2(direction, direction)
    if length_squared == 0.0:
        return True

    cuts = [0.0, 1.0]
    vertices = boundary.vertices
    for index, edge_start in enumerate(vertices):
        edge_end = vertices[(index + 1) % len(vertices)]
        cuts.extend(_edge_cut_parameters(start, direction, length_squared, edge_start, edge_end))

    cuts.sort()
    distinct = [cuts[0]]
    for value in cuts[1:]:
        if abs(value - distinct[-1]) > _SEGMENT_PARAMETER_TOLERANCE:
            distinct.append(value)
    for lower, upper in pairwise(distinct):
        if upper - lower <= _SEGMENT_PARAMETER_TOLERANCE:
            continue
        parameter = (lower + upper) * 0.5
        point = Vec2(
            start.x + parameter * direction[0],
            start.y + parameter * direction[1],
        )
        if not boundary.contains(point):
            return False
    return True


def _edge_cut_parameters(
    start: Vec2,
    direction: tuple[float, float],
    length_squared: float,
    edge_start: Vec2,
    edge_end: Vec2,
) -> tuple[float, ...]:
    edge = (edge_end.x - edge_start.x, edge_end.y - edge_start.y)
    offset = (edge_start.x - start.x, edge_start.y - start.y)
    denominator = _cross2(direction, edge)
    scale = max(*(abs(value) for value in (*direction, *edge)), 1.0)
    parallel_tolerance = math.ulp(1.0) * 64.0 * scale * scale
    if abs(denominator) <= parallel_tolerance:
        if abs(_cross2(offset, direction)) > parallel_tolerance:
            return ()
        parameters = (
            _dot2((point.x - start.x, point.y - start.y), direction) / length_squared
            for point in (edge_start, edge_end)
        )
        return tuple(
            _bounded_segment_parameter(value) for value in parameters if _on_segment(value)
        )

    segment_parameter = _cross2(offset, edge) / denominator
    edge_parameter = _cross2(offset, direction) / denominator
    if _on_segment(segment_parameter) and _on_segment(edge_parameter):
        return (_bounded_segment_parameter(segment_parameter),)
    return ()


def _on_segment(parameter: float) -> bool:
    return -_SEGMENT_PARAMETER_TOLERANCE <= parameter <= 1.0 + _SEGMENT_PARAMETER_TOLERANCE


def _bounded_segment_parameter(parameter: float) -> float:
    return min(1.0, max(0.0, parameter))


def _dot2(left: tuple[float, float], right: tuple[float, float]) -> float:
    return left[0] * right[0] + left[1] * right[1]


def _cross2(left: tuple[float, float], right: tuple[float, float]) -> float:
    return left[0] * right[1] - left[1] * right[0]


def _minimum_valid_quality(frequencies: Sequence[int]) -> int:
    for sdk_quality, frequency in enumerate(frequencies):
        if frequency > 0:
            return sdk_quality >> 2
    raise ProcessingConfigError("quality profile has no valid measurement quality")


def _millimetres(value_m: float) -> int:
    value_mm = value_m * 1_000.0
    rounded = round(value_mm)
    if (
        not math.isclose(value_mm, rounded, rel_tol=0.0, abs_tol=1e-6)
        or not _I64_MIN <= rounded < _I64_EXCLUSIVE_MAX
    ):
        raise ProcessingConfigError(f"processing geometry requires integer millimetres: {value_m}")
    return rounded


def _matrix_values(values: FloatArray) -> list[list[float]]:
    return [_vector_values(row) for row in values]


def _vector_values(values: FloatArray) -> list[float]:
    return [_stable_float(float(value)) for value in values]


def _stable_float(value: float) -> float:
    if abs(value) < 1e-15:
        return 0.0
    return round(value, 15)


def _require_absolute_posix_directory(path: PurePosixPath) -> None:
    if not path.is_absolute() or str(path) == "/":
        raise ProcessingConfigError("processing socket directory must be an absolute non-root path")


def _require_identifier(value: str, name: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ProcessingConfigError(f"{name} must be a safe deployment identifier")
    return value


def _require_driver_identifier(value: str, name: str) -> str:
    if not _DRIVER_IDENTITY.fullmatch(value):
        raise ProcessingConfigError(f"{name} must be a driver-compatible identifier")
    return value
