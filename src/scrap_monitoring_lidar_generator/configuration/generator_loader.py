"""Strict loader for generator-only execution configuration version 1."""

import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scrap_monitoring_lidar_generator.configuration._json import (
    parse_document,
    read_document,
    require_array,
    require_boolean,
    require_exact_fields,
    require_integer,
    require_literal,
    require_non_empty_string,
    require_number,
    require_object,
)
from scrap_monitoring_lidar_generator.configuration.errors import ConfigurationError
from scrap_monitoring_lidar_generator.configuration.generator_models import (
    CollectionOcclusionConfig,
    DiagnosticsConfig,
    DistanceNoiseConfig,
    DistortionConfig,
    DropoutConfig,
    FallingMaterialConfig,
    FloatRange,
    GeneratorConfig,
    GeneratorInputs,
    MeasurementConfig,
    ReflectionErrorConfig,
    ScenarioConfig,
    SurfaceConfig,
    TransportConfig,
    VoidsConfig,
)
from scrap_monitoring_lidar_generator.configuration.loader import load_environment
from scrap_monitoring_lidar_generator.configuration.models import Coordinate2
from scrap_monitoring_lidar_generator.configuration.quality_loader import load_quality_profile
from scrap_monitoring_lidar_generator.geometry import Polygon2, Vec2

_MAX_SEED = 18_446_744_073_709_551_615
_MAX_UNSIGNED_32_BIT = 4_294_967_295
_MAX_SIGNED_64_BIT = 9_223_372_036_854_775_807
_MIN_CONTRACT_DISTANCE_M = 0.05
_MAX_CONTRACT_DISTANCE_M = 30.0

_GENERATOR_FIELDS = frozenset(
    {
        "config_version",
        "seed",
        "environment_path",
        "quality_profile_path",
        "scenario",
        "measurement",
        "transport",
        "diagnostics",
    }
)
_SCENARIO_FIELDS = frozenset(
    {
        "mean_fill_duration_s",
        "fill_duration_factor_range",
        "fill_rate_factor_range",
        "fill_rate_change_duration_s_range",
        "collection_threshold_range",
        "collection_duration_factor_range",
        "collection_rate_factor_range",
        "collection_rate_change_duration_s_range",
        "inlet_positions_xy_m",
        "inlet_switch_activation_ratio",
        "inlet_switch_height_difference_m",
        "inlet_comparison_radius_m",
        "surface",
    }
)
_SURFACE_FIELDS = frozenset(
    {
        "cell_size_m",
        "update_interval_s",
        "pile_spread_radius_m",
        "roughness_height_range_m",
        "roughness_radius_range_m",
    }
)
_MEASUREMENT_FIELDS = frozenset(
    {
        "sample_rate_hz",
        "rotation_rate_hz",
        "min_distance_m",
        "max_distance_m",
        "distance_noise",
        "distortions",
    }
)
_DISTANCE_NOISE_FIELDS = frozenset({"enabled", "standard_deviation_m", "limit_m"})
_DISTORTION_FIELDS = frozenset(
    {
        "falling_material",
        "voids",
        "collection_occlusion",
        "reflection_error",
        "dropout",
    }
)
_FALLING_MATERIAL_FIELDS = frozenset(
    {
        "enabled",
        "event_rate_per_s",
        "radius_m_range",
        "duration_s_range",
        "distance_reduction_m_range",
    }
)
_VOIDS_FIELDS = frozenset(
    {
        "enabled",
        "surface_area_ratio",
        "radius_m_range",
        "duration_s_range",
        "cover_height_increase_m",
        "distance_increase_m_range",
    }
)
_COLLECTION_OCCLUSION_FIELDS = frozenset(
    {
        "enabled",
        "event_interval_s_range",
        "radius_m_range",
        "duration_s_range",
        "distance_reduction_m_range",
    }
)
_REFLECTION_ERROR_FIELDS = frozenset({"enabled", "probability", "distance_reduction_m_range"})
_DROPOUT_FIELDS = frozenset({"enabled", "event_interval_s_range", "duration_s_range"})
_DIAGNOSTICS_FIELDS = frozenset({"enabled", "output_path", "sample_scan_limit_per_sensor"})
_TRANSPORT_FIELDS = frozenset(
    {
        "host",
        "port",
        "max_message_body_bytes",
        "buffer_max_age_s",
        "buffer_max_bytes",
        "connect_timeout_s",
        "send_timeout_s",
        "ack_timeout_s",
        "reconnect_initial_delay_s",
        "reconnect_max_delay_s",
    }
)

type _NumberParser = Callable[[Any, str], float]


def load_generator_config(path: str | Path) -> GeneratorConfig:
    """Load generator configuration and resolve its relative paths."""
    source = Path(path)
    document = read_document(source, "generator configuration")
    return parse_generator_config(document, base_directory=source.parent)


def parse_generator_config(
    document: str,
    *,
    base_directory: str | Path = ".",
) -> GeneratorConfig:
    """Parse and validate a generator configuration JSON document."""
    root = require_object(parse_document(document), "$")
    require_exact_fields(root, _GENERATOR_FIELDS, "$")
    require_literal(root["config_version"], 1, "$.config_version")
    base = Path(base_directory)

    scenario = _parse_scenario(root["scenario"], "$.scenario")
    measurement = _parse_measurement(root["measurement"], "$.measurement")
    transport = _parse_transport(root["transport"], "$.transport")
    diagnostics = _parse_diagnostics(root["diagnostics"], "$.diagnostics", base)
    return GeneratorConfig(
        seed=require_integer(root["seed"], "$.seed", minimum=0, maximum=_MAX_SEED),
        environment_path=_parse_path(root["environment_path"], "$.environment_path", base),
        quality_profile_path=_parse_path(
            root["quality_profile_path"],
            "$.quality_profile_path",
            base,
        ),
        scenario=scenario,
        measurement=measurement,
        transport=transport,
        diagnostics=diagnostics,
    )


def load_generator_inputs(path: str | Path) -> GeneratorInputs:
    """Load generator configuration and validate all referenced inputs together."""
    generator = load_generator_config(path)
    environment = load_environment(generator.environment_path)
    quality_profile = load_quality_profile(generator.quality_profile_path)

    environment_sensor_ids = {sensor.sensor_id for sensor in environment.sensors}
    quality_sensor_ids = {sensor.sensor_id for sensor in quality_profile.sensors}
    if quality_sensor_ids != environment_sensor_ids:
        raise ConfigurationError(
            "quality profile sensor_id values must exactly match environment sensors"
        )

    boundary = Polygon2(tuple(Vec2(x, y) for x, y in environment.boundary_xy_m))
    if any(not boundary.contains(Vec2(x, y)) for x, y in generator.scenario.inlet_positions_xy_m):
        raise ConfigurationError(
            "scenario inlet positions must lie inside the environment boundary"
        )

    return GeneratorInputs(
        generator=generator,
        environment=environment,
        quality_profile=quality_profile,
    )


def _parse_scenario(value: Any, path: str) -> ScenarioConfig:
    scenario = require_object(value, path)
    require_exact_fields(scenario, _SCENARIO_FIELDS, path)
    inlet_values = require_array(scenario["inlet_positions_xy_m"], f"{path}.inlet_positions_xy_m")
    if not inlet_values:
        raise ConfigurationError(f"{path}.inlet_positions_xy_m must contain at least 1 coordinate")
    inlet_positions = tuple(
        _parse_coordinate2(item, f"{path}.inlet_positions_xy_m[{index}]")
        for index, item in enumerate(inlet_values)
    )
    if len(set(inlet_positions)) != len(inlet_positions):
        raise ConfigurationError(f"{path}.inlet_positions_xy_m must contain unique coordinates")

    fill_duration_factor_range = _require_range(
        scenario["fill_duration_factor_range"],
        f"{path}.fill_duration_factor_range",
        _require_positive,
    )
    if not math.isclose(
        sum(fill_duration_factor_range),
        2.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ConfigurationError(f"{path}.fill_duration_factor_range must be centered on 1")

    fill_rate_factor_range = _require_average_factor_range(
        scenario["fill_rate_factor_range"],
        f"{path}.fill_rate_factor_range",
    )
    collection_rate_factor_range = _require_average_factor_range(
        scenario["collection_rate_factor_range"],
        f"{path}.collection_rate_factor_range",
    )

    return ScenarioConfig(
        mean_fill_duration_s=_require_positive(
            scenario["mean_fill_duration_s"],
            f"{path}.mean_fill_duration_s",
        ),
        fill_duration_factor_range=fill_duration_factor_range,
        fill_rate_factor_range=fill_rate_factor_range,
        fill_rate_change_duration_s_range=_require_range(
            scenario["fill_rate_change_duration_s_range"],
            f"{path}.fill_rate_change_duration_s_range",
            _require_positive,
        ),
        collection_threshold_range=_require_range(
            scenario["collection_threshold_range"],
            f"{path}.collection_threshold_range",
            _require_positive_ratio,
        ),
        collection_duration_factor_range=_require_range(
            scenario["collection_duration_factor_range"],
            f"{path}.collection_duration_factor_range",
            _require_positive,
        ),
        collection_rate_factor_range=collection_rate_factor_range,
        collection_rate_change_duration_s_range=_require_range(
            scenario["collection_rate_change_duration_s_range"],
            f"{path}.collection_rate_change_duration_s_range",
            _require_positive,
        ),
        inlet_positions_xy_m=inlet_positions,
        inlet_switch_activation_ratio=_require_ratio(
            scenario["inlet_switch_activation_ratio"],
            f"{path}.inlet_switch_activation_ratio",
        ),
        inlet_switch_height_difference_m=_require_non_negative(
            scenario["inlet_switch_height_difference_m"],
            f"{path}.inlet_switch_height_difference_m",
        ),
        inlet_comparison_radius_m=_require_positive(
            scenario["inlet_comparison_radius_m"],
            f"{path}.inlet_comparison_radius_m",
        ),
        surface=_parse_surface(scenario["surface"], f"{path}.surface"),
    )


def _parse_surface(value: Any, path: str) -> SurfaceConfig:
    surface = require_object(value, path)
    require_exact_fields(surface, _SURFACE_FIELDS, path)
    return SurfaceConfig(
        cell_size_m=_require_positive(surface["cell_size_m"], f"{path}.cell_size_m"),
        update_interval_s=_require_positive(
            surface["update_interval_s"],
            f"{path}.update_interval_s",
        ),
        pile_spread_radius_m=_require_positive(
            surface["pile_spread_radius_m"],
            f"{path}.pile_spread_radius_m",
        ),
        roughness_height_range_m=_require_range(
            surface["roughness_height_range_m"],
            f"{path}.roughness_height_range_m",
            require_number,
        ),
        roughness_radius_range_m=_require_range(
            surface["roughness_radius_range_m"],
            f"{path}.roughness_radius_range_m",
            _require_positive,
        ),
    )


def _parse_measurement(value: Any, path: str) -> MeasurementConfig:
    measurement = require_object(value, path)
    require_exact_fields(measurement, _MEASUREMENT_FIELDS, path)
    sample_rate_hz = _require_positive(measurement["sample_rate_hz"], f"{path}.sample_rate_hz")
    rotation_rate_hz = _require_positive(
        measurement["rotation_rate_hz"],
        f"{path}.rotation_rate_hz",
    )
    if sample_rate_hz < rotation_rate_hz:
        raise ConfigurationError(f"{path}.sample_rate_hz must be at least {path}.rotation_rate_hz")
    min_distance_m = _require_contract_distance(
        measurement["min_distance_m"],
        f"{path}.min_distance_m",
    )
    max_distance_m = _require_contract_distance(
        measurement["max_distance_m"],
        f"{path}.max_distance_m",
    )
    if max_distance_m <= min_distance_m:
        raise ConfigurationError(f"{path}.max_distance_m must be greater than min_distance_m")

    return MeasurementConfig(
        sample_rate_hz=sample_rate_hz,
        rotation_rate_hz=rotation_rate_hz,
        min_distance_m=min_distance_m,
        max_distance_m=max_distance_m,
        distance_noise=_parse_distance_noise(
            measurement["distance_noise"],
            f"{path}.distance_noise",
        ),
        distortions=_parse_distortions(measurement["distortions"], f"{path}.distortions"),
    )


def _parse_distance_noise(value: Any, path: str) -> DistanceNoiseConfig:
    noise = require_object(value, path)
    require_exact_fields(noise, _DISTANCE_NOISE_FIELDS, path)
    return DistanceNoiseConfig(
        enabled=require_boolean(noise["enabled"], f"{path}.enabled"),
        standard_deviation_m=_require_non_negative(
            noise["standard_deviation_m"],
            f"{path}.standard_deviation_m",
        ),
        limit_m=_require_non_negative(noise["limit_m"], f"{path}.limit_m"),
    )


def _parse_distortions(value: Any, path: str) -> DistortionConfig:
    distortions = require_object(value, path)
    require_exact_fields(distortions, _DISTORTION_FIELDS, path)
    return DistortionConfig(
        falling_material=_parse_falling_material(
            distortions["falling_material"],
            f"{path}.falling_material",
        ),
        voids=_parse_voids(distortions["voids"], f"{path}.voids"),
        collection_occlusion=_parse_collection_occlusion(
            distortions["collection_occlusion"],
            f"{path}.collection_occlusion",
        ),
        reflection_error=_parse_reflection_error(
            distortions["reflection_error"],
            f"{path}.reflection_error",
        ),
        dropout=_parse_dropout(distortions["dropout"], f"{path}.dropout"),
    )


def _parse_falling_material(value: Any, path: str) -> FallingMaterialConfig:
    config = require_object(value, path)
    require_exact_fields(config, _FALLING_MATERIAL_FIELDS, path)
    return FallingMaterialConfig(
        enabled=require_boolean(config["enabled"], f"{path}.enabled"),
        event_rate_per_s=_require_non_negative(
            config["event_rate_per_s"],
            f"{path}.event_rate_per_s",
        ),
        radius_m_range=_require_positive_range(config, "radius_m_range", path),
        duration_s_range=_require_positive_range(config, "duration_s_range", path),
        distance_reduction_m_range=_require_positive_range(
            config,
            "distance_reduction_m_range",
            path,
        ),
    )


def _parse_voids(value: Any, path: str) -> VoidsConfig:
    config = require_object(value, path)
    require_exact_fields(config, _VOIDS_FIELDS, path)
    return VoidsConfig(
        enabled=require_boolean(config["enabled"], f"{path}.enabled"),
        surface_area_ratio=_require_ratio(
            config["surface_area_ratio"],
            f"{path}.surface_area_ratio",
        ),
        radius_m_range=_require_positive_range(config, "radius_m_range", path),
        duration_s_range=_require_positive_range(config, "duration_s_range", path),
        cover_height_increase_m=_require_non_negative(
            config["cover_height_increase_m"],
            f"{path}.cover_height_increase_m",
        ),
        distance_increase_m_range=_require_positive_range(
            config,
            "distance_increase_m_range",
            path,
        ),
    )


def _parse_collection_occlusion(value: Any, path: str) -> CollectionOcclusionConfig:
    config = require_object(value, path)
    require_exact_fields(config, _COLLECTION_OCCLUSION_FIELDS, path)
    return CollectionOcclusionConfig(
        enabled=require_boolean(config["enabled"], f"{path}.enabled"),
        event_interval_s_range=_require_positive_range(
            config,
            "event_interval_s_range",
            path,
        ),
        radius_m_range=_require_positive_range(config, "radius_m_range", path),
        duration_s_range=_require_positive_range(config, "duration_s_range", path),
        distance_reduction_m_range=_require_positive_range(
            config,
            "distance_reduction_m_range",
            path,
        ),
    )


def _parse_reflection_error(value: Any, path: str) -> ReflectionErrorConfig:
    config = require_object(value, path)
    require_exact_fields(config, _REFLECTION_ERROR_FIELDS, path)
    return ReflectionErrorConfig(
        enabled=require_boolean(config["enabled"], f"{path}.enabled"),
        probability=_require_ratio(config["probability"], f"{path}.probability"),
        distance_reduction_m_range=_require_positive_range(
            config,
            "distance_reduction_m_range",
            path,
        ),
    )


def _parse_dropout(value: Any, path: str) -> DropoutConfig:
    config = require_object(value, path)
    require_exact_fields(config, _DROPOUT_FIELDS, path)
    return DropoutConfig(
        enabled=require_boolean(config["enabled"], f"{path}.enabled"),
        event_interval_s_range=_require_positive_range(
            config,
            "event_interval_s_range",
            path,
        ),
        duration_s_range=_require_positive_range(config, "duration_s_range", path),
    )


def _parse_diagnostics(value: Any, path: str, base: Path) -> DiagnosticsConfig:
    diagnostics = require_object(value, path)
    require_exact_fields(diagnostics, _DIAGNOSTICS_FIELDS, path)
    return DiagnosticsConfig(
        enabled=require_boolean(diagnostics["enabled"], f"{path}.enabled"),
        output_path=_parse_path(diagnostics["output_path"], f"{path}.output_path", base),
        sample_scan_limit_per_sensor=require_integer(
            diagnostics["sample_scan_limit_per_sensor"],
            f"{path}.sample_scan_limit_per_sensor",
            minimum=0,
        ),
    )


def _parse_transport(value: Any, path: str) -> TransportConfig:
    transport = require_object(value, path)
    require_exact_fields(transport, _TRANSPORT_FIELDS, path)
    initial_delay_s = _require_positive(
        transport["reconnect_initial_delay_s"],
        f"{path}.reconnect_initial_delay_s",
    )
    maximum_delay_s = _require_positive(
        transport["reconnect_max_delay_s"],
        f"{path}.reconnect_max_delay_s",
    )
    if initial_delay_s > maximum_delay_s:
        raise ConfigurationError(
            f"{path}.reconnect_initial_delay_s must not exceed {path}.reconnect_max_delay_s"
        )
    return TransportConfig(
        host=require_non_empty_string(transport["host"], f"{path}.host"),
        port=require_integer(transport["port"], f"{path}.port", minimum=1, maximum=65_535),
        max_message_body_bytes=require_integer(
            transport["max_message_body_bytes"],
            f"{path}.max_message_body_bytes",
            minimum=1,
            maximum=_MAX_UNSIGNED_32_BIT,
        ),
        buffer_max_age_s=_require_positive(
            transport["buffer_max_age_s"], f"{path}.buffer_max_age_s"
        ),
        buffer_max_bytes=require_integer(
            transport["buffer_max_bytes"],
            f"{path}.buffer_max_bytes",
            minimum=1,
            maximum=_MAX_SIGNED_64_BIT,
        ),
        connect_timeout_s=_require_positive(
            transport["connect_timeout_s"], f"{path}.connect_timeout_s"
        ),
        send_timeout_s=_require_positive(transport["send_timeout_s"], f"{path}.send_timeout_s"),
        ack_timeout_s=_require_positive(transport["ack_timeout_s"], f"{path}.ack_timeout_s"),
        reconnect_initial_delay_s=initial_delay_s,
        reconnect_max_delay_s=maximum_delay_s,
    )


def _parse_path(value: Any, path: str, base: Path) -> Path:
    result = Path(require_non_empty_string(value, path))
    return result if result.is_absolute() else base / result


def _parse_coordinate2(value: Any, path: str) -> Coordinate2:
    items = require_array(value, path)
    if len(items) != 2:
        raise ConfigurationError(f"{path} must contain exactly 2 numbers")
    return require_number(items[0], f"{path}[0]"), require_number(items[1], f"{path}[1]")


def _require_range(value: Any, path: str, parser: _NumberParser) -> FloatRange:
    items = require_array(value, path)
    if len(items) != 2:
        raise ConfigurationError(f"{path} must contain exactly 2 numbers")
    lower = parser(items[0], f"{path}[0]")
    upper = parser(items[1], f"{path}[1]")
    if upper < lower:
        raise ConfigurationError(f"{path}[1] must be at least {path}[0]")
    return lower, upper


def _require_positive_range(config: dict[str, Any], field: str, path: str) -> FloatRange:
    return _require_range(config[field], f"{path}.{field}", _require_positive)


def _require_average_factor_range(value: Any, path: str) -> FloatRange:
    result = _require_range(value, path, _require_positive)
    if not result[0] <= 1.0 <= result[1]:
        raise ConfigurationError(f"{path} must include the cycle average factor 1")
    return result


def _require_positive(value: Any, path: str) -> float:
    result = require_number(value, path)
    if result <= 0.0:
        raise ConfigurationError(f"{path} must be greater than zero")
    return result


def _require_non_negative(value: Any, path: str) -> float:
    result = require_number(value, path)
    if result < 0.0:
        raise ConfigurationError(f"{path} must be non-negative")
    return result


def _require_ratio(value: Any, path: str) -> float:
    result = require_number(value, path)
    if result < 0.0 or result > 1.0:
        raise ConfigurationError(f"{path} must be between 0 and 1")
    return result


def _require_positive_ratio(value: Any, path: str) -> float:
    result = _require_ratio(value, path)
    if result == 0.0:
        raise ConfigurationError(f"{path} must be greater than zero")
    return result


def _require_contract_distance(value: Any, path: str) -> float:
    result = require_number(value, path)
    if result < _MIN_CONTRACT_DISTANCE_M or result > _MAX_CONTRACT_DISTANCE_M:
        raise ConfigurationError(
            f"{path} must be between {_MIN_CONTRACT_DISTANCE_M} and {_MAX_CONTRACT_DISTANCE_M}"
        )
    return result
