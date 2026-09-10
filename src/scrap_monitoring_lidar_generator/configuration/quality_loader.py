"""Strict loader for synthetic quality profile version 1."""

from pathlib import Path
from typing import Any

from scrap_monitoring_lidar_generator.configuration._json import (
    parse_document,
    read_document,
    require_array,
    require_exact_fields,
    require_integer,
    require_literal,
    require_non_empty_string,
    require_object,
)
from scrap_monitoring_lidar_generator.configuration.errors import ConfigurationError
from scrap_monitoring_lidar_generator.configuration.generator_models import (
    QualityFrequencies,
    QualityProfileConfig,
    SensorQualityConfig,
)

_PROFILE_FIELDS = frozenset({"config_version", "sensors"})
_SENSOR_FIELDS = frozenset(
    {
        "sensor_id",
        "valid_distance_frequencies",
        "invalid_distance_frequencies",
    }
)
_MAX_FREQUENCY = 9_223_372_036_854_775_807
_QUALITY_VALUE_COUNT = 256


def load_quality_profile(path: str | Path) -> QualityProfileConfig:
    """Load a quality profile from a UTF-8 JSON file."""
    return parse_quality_profile(read_document(path, "quality profile"))


def parse_quality_profile(document: str) -> QualityProfileConfig:
    """Parse and validate a quality profile JSON document."""
    root = require_object(parse_document(document), "$")
    require_exact_fields(root, _PROFILE_FIELDS, "$")
    require_literal(root["config_version"], 1, "$.config_version")

    sensor_values = require_array(root["sensors"], "$.sensors")
    if not sensor_values:
        raise ConfigurationError("$.sensors must contain at least 1 sensor")
    sensors = tuple(
        _parse_sensor(value, f"$.sensors[{index}]") for index, value in enumerate(sensor_values)
    )
    if len({sensor.sensor_id for sensor in sensors}) != len(sensors):
        raise ConfigurationError("$.sensors must contain unique sensor_id values")
    return QualityProfileConfig(sensors=sensors)


def _parse_sensor(value: Any, path: str) -> SensorQualityConfig:
    sensor = require_object(value, path)
    require_exact_fields(sensor, _SENSOR_FIELDS, path)
    return SensorQualityConfig(
        sensor_id=require_non_empty_string(sensor["sensor_id"], f"{path}.sensor_id"),
        valid_distance_frequencies=_parse_frequencies(
            sensor["valid_distance_frequencies"],
            f"{path}.valid_distance_frequencies",
        ),
        invalid_distance_frequencies=_parse_frequencies(
            sensor["invalid_distance_frequencies"],
            f"{path}.invalid_distance_frequencies",
        ),
    )


def _parse_frequencies(value: Any, path: str) -> QualityFrequencies:
    source = require_object(value, path)
    if not source:
        raise ConfigurationError(f"{path} must contain at least 1 quality value")

    frequencies = [0] * _QUALITY_VALUE_COUNT
    for raw_quality, raw_frequency in source.items():
        quality = _parse_quality_key(raw_quality, path)
        frequencies[quality] = require_integer(
            raw_frequency,
            f"{path}.{raw_quality}",
            minimum=1,
            maximum=_MAX_FREQUENCY,
        )
    return tuple(frequencies)


def _parse_quality_key(value: str, path: str) -> int:
    if not value.isascii() or not value.isdecimal():
        raise ConfigurationError(f"{path} keys must be decimal integers from 0 through 255")
    quality = int(value)
    if str(quality) != value or quality >= _QUALITY_VALUE_COUNT:
        raise ConfigurationError(f"{path} keys must be decimal integers from 0 through 255")
    return quality
