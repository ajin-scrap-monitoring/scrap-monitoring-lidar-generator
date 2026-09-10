"""Strict JSON loader for environment configuration version 1."""

import math
from pathlib import Path
from typing import Any

import scrap_monitoring_lidar_generator.configuration._json as strict_json
from scrap_monitoring_lidar_generator.configuration.errors import ConfigurationError
from scrap_monitoring_lidar_generator.configuration.models import (
    Coordinate2,
    Coordinate3,
    EnvironmentConfig,
    SensorConfig,
)
from scrap_monitoring_lidar_generator.geometry.polygon import Polygon2
from scrap_monitoring_lidar_generator.geometry.primitives import Vec2

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


def load_environment(path: str | Path) -> EnvironmentConfig:
    """Load and validate an environment configuration from a UTF-8 JSON file."""
    document = strict_json.read_document(path, "environment configuration")
    return parse_environment(document)


def parse_environment(document: str) -> EnvironmentConfig:
    """Parse and validate an environment configuration JSON document."""
    value = strict_json.parse_document(document)
    root = strict_json.require_object(value, "$")
    strict_json.require_exact_fields(root, _ENVIRONMENT_FIELDS, "$")

    environment_id = strict_json.require_non_empty_string(
        root["environment_id"], "$.environment_id"
    )
    strict_json.require_literal(root["length_unit"], "m", "$.length_unit")
    strict_json.require_literal(root["angle_unit"], "deg", "$.angle_unit")

    boundary_value = strict_json.require_array(root["boundary_xy_m"], "$.boundary_xy_m")
    if len(boundary_value) < 3:
        raise ConfigurationError("$.boundary_xy_m must contain at least 3 coordinates")
    boundary = tuple(
        _require_coordinate2(item, f"$.boundary_xy_m[{index}]")
        for index, item in enumerate(boundary_value)
    )
    _validate_boundary(boundary)

    floor_z_m = strict_json.require_number(root["floor_z_m"], "$.floor_z_m")
    top_z_m = strict_json.require_number(root["top_z_m"], "$.top_z_m")
    if top_z_m <= floor_z_m:
        raise ConfigurationError("$.top_z_m must be greater than $.floor_z_m")

    sensors_value = strict_json.require_array(root["sensors"], "$.sensors")
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


def _parse_sensor(value: Any, path: str) -> SensorConfig:
    sensor = strict_json.require_object(value, path)
    strict_json.require_exact_fields(sensor, _SENSOR_FIELDS, path)

    sensor_id = strict_json.require_non_empty_string(sensor["sensor_id"], f"{path}.sensor_id")
    p0_m = _require_coordinate3(sensor["p0_m"], f"{path}.p0_m")
    u0 = _require_coordinate3(sensor["u0"], f"{path}.u0")
    u90 = _require_coordinate3(sensor["u90"], f"{path}.u90")
    _require_unit_vector(u0, f"{path}.u0")
    _require_unit_vector(u90, f"{path}.u90")

    dot_product = sum(left * right for left, right in zip(u0, u90, strict=True))
    if not math.isclose(dot_product, 0.0, rel_tol=0.0, abs_tol=_VECTOR_TOLERANCE):
        raise ConfigurationError(f"{path}.u0 and {path}.u90 must be orthogonal")

    return SensorConfig(sensor_id=sensor_id, p0_m=p0_m, u0=u0, u90=u90)


def _require_coordinate_values(value: Any, size: int, path: str) -> tuple[float, ...]:
    items = strict_json.require_array(value, path)
    if len(items) != size:
        raise ConfigurationError(f"{path} must contain exactly {size} numbers")
    return tuple(
        strict_json.require_number(item, f"{path}[{index}]") for index, item in enumerate(items)
    )


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
    try:
        Polygon2(tuple(Vec2(x, y) for x, y in boundary))
    except ValueError as error:
        raise ConfigurationError(f"$.boundary_xy_m is invalid: {error}") from error
