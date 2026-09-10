"""Strict JSON loader for environment configuration version 1."""

import json
import math
from pathlib import Path
from typing import Any, Never

from scrap_monitoring_lidar_generator.configuration.models import (
    Coordinate2,
    Coordinate3,
    EnvironmentConfig,
    SensorConfig,
)

_ENVIRONMENT_FIELDS = frozenset(
    {
        "environment_id",
        "length_unit",
        "angle_unit",
        "boundary_xy_m",
        "floor_z_m",
        "top_z_m",
        "sensors",
    }
)
_SENSOR_FIELDS = frozenset({"sensor_id", "p0_m", "u0", "u90"})
_VECTOR_TOLERANCE = 1e-6
_GEOMETRY_TOLERANCE = 1e-12


class ConfigurationError(ValueError):
    """Raised when configuration cannot be parsed or validated."""


def load_environment(path: str | Path) -> EnvironmentConfig:
    """Load and validate an environment configuration from a UTF-8 JSON file."""
    source = Path(path)
    try:
        document = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ConfigurationError(f"cannot read environment configuration: {source}") from error
    return parse_environment(document)


def parse_environment(document: str) -> EnvironmentConfig:
    """Parse and validate an environment configuration JSON document."""
    try:
        value = json.loads(
            document,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_non_finite_constant,
        )
    except ConfigurationError:
        raise
    except json.JSONDecodeError as error:
        raise ConfigurationError(
            f"invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}"
        ) from error

    root = _require_object(value, "$")
    _require_exact_fields(root, _ENVIRONMENT_FIELDS, "$")

    environment_id = _require_non_empty_string(root["environment_id"], "$.environment_id")
    _require_literal(root["length_unit"], "m", "$.length_unit")
    _require_literal(root["angle_unit"], "deg", "$.angle_unit")

    boundary_value = _require_array(root["boundary_xy_m"], "$.boundary_xy_m")
    if len(boundary_value) < 3:
        raise ConfigurationError("$.boundary_xy_m must contain at least 3 coordinates")
    boundary = tuple(
        _require_coordinate2(item, f"$.boundary_xy_m[{index}]")
        for index, item in enumerate(boundary_value)
    )
    _validate_boundary(boundary)

    floor_z_m = _require_number(root["floor_z_m"], "$.floor_z_m")
    top_z_m = _require_number(root["top_z_m"], "$.top_z_m")
    if top_z_m <= floor_z_m:
        raise ConfigurationError("$.top_z_m must be greater than $.floor_z_m")

    sensors_value = _require_array(root["sensors"], "$.sensors")
    if not sensors_value:
        raise ConfigurationError("$.sensors must contain at least 1 sensor")
    sensors = tuple(
        _parse_sensor(item, f"$.sensors[{index}]") for index, item in enumerate(sensors_value)
    )
    sensor_ids = [sensor.sensor_id for sensor in sensors]
    if len(sensor_ids) != len(set(sensor_ids)):
        raise ConfigurationError("$.sensors must contain unique sensor_id values")

    return EnvironmentConfig(
        environment_id=environment_id,
        boundary_xy_m=boundary,
        floor_z_m=floor_z_m,
        top_z_m=top_z_m,
        sensors=sensors,
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_non_finite_constant(value: str) -> Never:
    raise ConfigurationError(f"non-finite JSON number is not allowed: {value}")


def _parse_sensor(value: Any, path: str) -> SensorConfig:
    sensor = _require_object(value, path)
    _require_exact_fields(sensor, _SENSOR_FIELDS, path)

    sensor_id = _require_non_empty_string(sensor["sensor_id"], f"{path}.sensor_id")
    p0_m = _require_coordinate3(sensor["p0_m"], f"{path}.p0_m")
    u0 = _require_coordinate3(sensor["u0"], f"{path}.u0")
    u90 = _require_coordinate3(sensor["u90"], f"{path}.u90")
    _require_unit_vector(u0, f"{path}.u0")
    _require_unit_vector(u90, f"{path}.u90")

    dot_product = sum(left * right for left, right in zip(u0, u90, strict=True))
    if not math.isclose(dot_product, 0.0, rel_tol=0.0, abs_tol=_VECTOR_TOLERANCE):
        raise ConfigurationError(f"{path}.u0 and {path}.u90 must be orthogonal")

    return SensorConfig(sensor_id=sensor_id, p0_m=p0_m, u0=u0, u90=u90)


def _require_exact_fields(value: dict[str, Any], expected: frozenset[str], path: str) -> None:
    actual = frozenset(value)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing:
        raise ConfigurationError(f"{path} is missing fields: {', '.join(missing)}")
    if unexpected:
        raise ConfigurationError(f"{path} contains unexpected fields: {', '.join(unexpected)}")


def _require_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{path} must be an object")
    return value


def _require_array(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConfigurationError(f"{path} must be an array")
    return value


def _require_non_empty_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"{path} must be a non-empty string")
    return value


def _require_literal(value: Any, expected: str, path: str) -> None:
    if value != expected:
        raise ConfigurationError(f"{path} must be {expected!r}")


def _require_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigurationError(f"{path} must be a number")
    try:
        result = float(value)
    except OverflowError as error:
        raise ConfigurationError(f"{path} must be finite") from error
    if not math.isfinite(result):
        raise ConfigurationError(f"{path} must be finite")
    return result


def _require_coordinate_values(value: Any, size: int, path: str) -> tuple[float, ...]:
    items = _require_array(value, path)
    if len(items) != size:
        raise ConfigurationError(f"{path} must contain exactly {size} numbers")
    return tuple(_require_number(item, f"{path}[{index}]") for index, item in enumerate(items))


def _require_coordinate2(value: Any, path: str) -> Coordinate2:
    coordinates = _require_coordinate_values(value, 2, path)
    return coordinates[0], coordinates[1]


def _require_coordinate3(value: Any, path: str) -> Coordinate3:
    coordinates = _require_coordinate_values(value, 3, path)
    return coordinates[0], coordinates[1], coordinates[2]


def _require_unit_vector(value: Coordinate3, path: str) -> None:
    length = math.sqrt(sum(component * component for component in value))
    if not math.isclose(length, 1.0, rel_tol=0.0, abs_tol=_VECTOR_TOLERANCE):
        raise ConfigurationError(f"{path} must be a unit vector")


def _validate_boundary(boundary: tuple[Coordinate2, ...]) -> None:
    if len(boundary) != len(set(boundary)):
        raise ConfigurationError("$.boundary_xy_m must contain unique coordinates")

    doubled_area = sum(
        left[0] * right[1] - right[0] * left[1] for left, right in _boundary_edges(boundary)
    )
    if math.isclose(doubled_area, 0.0, rel_tol=0.0, abs_tol=_GEOMETRY_TOLERANCE):
        raise ConfigurationError("$.boundary_xy_m must enclose a non-zero area")

    edges = tuple(_boundary_edges(boundary))
    for first_index, first in enumerate(edges):
        for second_index in range(first_index + 1, len(edges)):
            if _edges_are_adjacent(first_index, second_index, len(edges)):
                continue
            if _segments_intersect(*first, *edges[second_index]):
                raise ConfigurationError("$.boundary_xy_m must not self-intersect")


def _boundary_edges(
    boundary: tuple[Coordinate2, ...],
) -> tuple[tuple[Coordinate2, Coordinate2], ...]:
    return tuple(
        (point, boundary[(index + 1) % len(boundary)]) for index, point in enumerate(boundary)
    )


def _edges_are_adjacent(first: int, second: int, edge_count: int) -> bool:
    return second == first + 1 or (first == 0 and second == edge_count - 1)


def _segments_intersect(
    a: Coordinate2,
    b: Coordinate2,
    c: Coordinate2,
    d: Coordinate2,
) -> bool:
    orientations = (
        _orientation(a, b, c),
        _orientation(a, b, d),
        _orientation(c, d, a),
        _orientation(c, d, b),
    )
    first, second, third, fourth = orientations

    if first * second < 0 and third * fourth < 0:
        return True
    return (
        (first == 0 and _point_on_segment(c, a, b))
        or (second == 0 and _point_on_segment(d, a, b))
        or (third == 0 and _point_on_segment(a, c, d))
        or (fourth == 0 and _point_on_segment(b, c, d))
    )


def _orientation(a: Coordinate2, b: Coordinate2, c: Coordinate2) -> int:
    cross_product = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    if math.isclose(cross_product, 0.0, rel_tol=0.0, abs_tol=_GEOMETRY_TOLERANCE):
        return 0
    return 1 if cross_product > 0 else -1


def _point_on_segment(point: Coordinate2, start: Coordinate2, end: Coordinate2) -> bool:
    return (
        min(start[0], end[0]) - _GEOMETRY_TOLERANCE
        <= point[0]
        <= max(start[0], end[0]) + _GEOMETRY_TOLERANCE
        and min(start[1], end[1]) - _GEOMETRY_TOLERANCE
        <= point[1]
        <= max(start[1], end[1]) + _GEOMETRY_TOLERANCE
    )
