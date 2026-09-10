"""Configuration loading and validation."""

from scrap_monitoring_lidar_generator.configuration.geometry import (
    build_environment_scene,
    build_sensor_frame,
)
from scrap_monitoring_lidar_generator.configuration.loader import (
    ConfigurationError,
    load_environment,
    parse_environment,
)
from scrap_monitoring_lidar_generator.configuration.models import (
    Coordinate2,
    Coordinate3,
    EnvironmentConfig,
    SensorConfig,
)

__all__ = [
    "ConfigurationError",
    "Coordinate2",
    "Coordinate3",
    "EnvironmentConfig",
    "SensorConfig",
    "build_environment_scene",
    "build_sensor_frame",
    "load_environment",
    "parse_environment",
]
