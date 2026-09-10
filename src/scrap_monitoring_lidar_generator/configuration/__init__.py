"""Configuration loading and validation."""

from scrap_monitoring_lidar_generator.configuration.errors import ConfigurationError
from scrap_monitoring_lidar_generator.configuration.generator_loader import (
    load_generator_config,
    load_generator_inputs,
    parse_generator_config,
)
from scrap_monitoring_lidar_generator.configuration.generator_models import (
    GeneratorConfig,
    GeneratorInputs,
    QualityProfileConfig,
)
from scrap_monitoring_lidar_generator.configuration.geometry import (
    build_environment_scene,
    build_sensor_frame,
)
from scrap_monitoring_lidar_generator.configuration.loader import (
    load_environment,
    parse_environment,
)
from scrap_monitoring_lidar_generator.configuration.models import (
    Coordinate2,
    Coordinate3,
    EnvironmentConfig,
    SensorConfig,
)
from scrap_monitoring_lidar_generator.configuration.quality_loader import (
    load_quality_profile,
    parse_quality_profile,
)

__all__ = [
    "ConfigurationError",
    "Coordinate2",
    "Coordinate3",
    "EnvironmentConfig",
    "GeneratorConfig",
    "GeneratorInputs",
    "QualityProfileConfig",
    "SensorConfig",
    "build_environment_scene",
    "build_sensor_frame",
    "load_environment",
    "load_generator_config",
    "load_generator_inputs",
    "load_quality_profile",
    "parse_environment",
    "parse_generator_config",
    "parse_quality_profile",
]
