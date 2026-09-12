"""Tests for strict generator configuration loading."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from scrap_monitoring_lidar_generator.configuration import (
    ConfigurationError,
    load_generator_config,
    parse_generator_config,
)

_ROOT = Path(__file__).parents[2]
_EXAMPLE_PATH = _ROOT / "examples" / "generator.v1.json"


@pytest.fixture
def valid_generator() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_EXAMPLE_PATH.read_text(encoding="utf-8")))


def _parse(value: dict[str, Any]) -> None:
    parse_generator_config(json.dumps(value))


def test_loads_generator_and_resolves_relative_paths() -> None:
    config = load_generator_config(_EXAMPLE_PATH)

    assert config.seed == 123456789
    assert config.environment_path == _ROOT / "examples" / "environment.v1.json"
    assert config.quality_profile_path == _ROOT / "examples" / "quality-profile.v1.json"
    assert config.scenario.inlet_positions_xy_m == ((1.5, 1.9), (2.588, 2.593))
    assert config.measurement.sample_rate_hz == 32_000.0
    assert config.measurement.rotation_rate_hz == 10.0
    assert config.measurement.min_distance_m == 0.05
    assert config.measurement.max_distance_m == 30.0
    assert config.scenario.mean_fill_duration_s == 86_400.0
    assert config.scenario.fill_duration_factor_range == (0.8, 1.2)
    assert config.scenario.fill_rate_factor_range == (0.5, 1.5)
    assert config.scenario.fill_rate_change_duration_s_range == (300.0, 900.0)
    assert config.scenario.collection_threshold_range == (0.85, 0.95)
    assert config.scenario.collection_duration_factor_range == (
        0.03333333333333333,
        0.05,
    )
    assert config.scenario.collection_rate_factor_range == (0.3, 1.7)
    assert config.scenario.collection_rate_change_duration_s_range == (60.0, 180.0)
    assert config.scenario.inlet_switch_activation_ratio == 0.5
    assert config.scenario.inlet_switch_height_difference_m == 0.5
    assert config.scenario.inlet_comparison_radius_m == 0.5
    assert config.scenario.surface.pile_spread_radius_m == 0.5
    assert config.scenario.surface.roughness_height_range_m == (-0.2, 0.2)
    assert config.scenario.surface.roughness_radius_range_m == (0.1, 0.3)
    assert config.measurement.distance_noise.standard_deviation_m == 0.01
    assert config.measurement.distance_noise.limit_m == 0.03
    assert config.measurement.distortions.falling_material.event_rate_per_s == 0.5
    assert config.measurement.distortions.falling_material.radius_m_range == (0.025, 0.1)
    assert config.measurement.distortions.voids.surface_area_ratio == 0.03
    assert config.measurement.distortions.voids.radius_m_range == (0.015, 0.06)
    assert config.measurement.distortions.voids.duration_s_range == (60.0, 300.0)
    assert config.measurement.distortions.collection_occlusion.event_interval_s_range == (
        20.0,
        40.0,
    )
    assert config.measurement.distortions.collection_occlusion.radius_m_range == (0.15, 0.5)
    assert config.measurement.distortions.collection_occlusion.duration_s_range == (2.0, 8.0)
    assert config.measurement.distortions.reflection_error.probability == 0.001
    assert config.measurement.distortions.reflection_error.distance_reduction_m_range == (
        0.5,
        2.0,
    )
    assert config.measurement.distortions.dropout.enabled is False
    assert config.transport.host == "receiver"
    assert config.transport.port == 9000
    assert config.transport.max_message_body_bytes == 1_048_576
    assert config.transport.buffer_max_age_s == 5.0
    assert config.transport.buffer_max_bytes == 16_777_216
    assert config.transport.connect_timeout_s == 3.0
    assert config.transport.send_timeout_s == 2.0
    assert config.transport.ack_timeout_s == 2.0
    assert config.transport.reconnect_initial_delay_s == 0.5
    assert config.transport.reconnect_max_delay_s == 5.0
    assert config.diagnostics.output_path == _ROOT / "examples" / "diagnostics"


def test_rejects_unknown_nested_field(valid_generator: dict[str, Any]) -> None:
    valid_generator["scenario"]["surface"]["unknown"] = True

    with pytest.raises(ConfigurationError, match="unexpected fields: unknown"):
        _parse(valid_generator)


def test_rejects_missing_nested_field(valid_generator: dict[str, Any]) -> None:
    del valid_generator["measurement"]["distance_noise"]["limit_m"]

    with pytest.raises(ConfigurationError, match="missing fields: limit_m"):
        _parse(valid_generator)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("seed",), True),
        (("seed",), 18_446_744_073_709_551_616),
        (("scenario", "mean_fill_duration_s"), 0),
        (("scenario", "collection_threshold_range"), [0, 0.7]),
        (("scenario", "collection_threshold_range"), [0.8, 0.7]),
        (("scenario", "inlet_switch_activation_ratio"), 1.1),
        (("measurement", "sample_rate_hz"), 2),
        (("measurement", "min_distance_m"), 0.01),
        (("measurement", "max_distance_m"), 31),
        (("measurement", "distance_noise", "enabled"), 1),
        (("transport", "host"), ""),
        (("transport", "port"), 0),
        (("transport", "port"), 65_536),
        (("transport", "max_message_body_bytes"), 0),
        (("transport", "max_message_body_bytes"), 4_294_967_296),
        (("transport", "buffer_max_age_s"), 0),
        (("transport", "buffer_max_bytes"), 0),
        (("transport", "connect_timeout_s"), float("inf")),
        (("transport", "send_timeout_s"), 0),
        (("transport", "ack_timeout_s"), 0),
        (("transport", "reconnect_initial_delay_s"), 0),
        (("transport", "reconnect_max_delay_s"), 0),
    ],
)
def test_rejects_invalid_generator_values(
    valid_generator: dict[str, Any],
    path: tuple[str, ...],
    value: Any,
) -> None:
    target = valid_generator
    for field in path[:-1]:
        target = target[field]
    target[path[-1]] = value

    with pytest.raises(ConfigurationError):
        _parse(valid_generator)


def test_rejects_distance_range_without_width(valid_generator: dict[str, Any]) -> None:
    valid_generator["measurement"]["min_distance_m"] = 10
    valid_generator["measurement"]["max_distance_m"] = 10

    with pytest.raises(ConfigurationError, match="must be greater"):
        _parse(valid_generator)


def test_rejects_reconnect_initial_delay_above_maximum(
    valid_generator: dict[str, Any],
) -> None:
    valid_generator["transport"]["reconnect_initial_delay_s"] = 5.1

    with pytest.raises(ConfigurationError, match="must not exceed"):
        _parse(valid_generator)


def test_rejects_duplicate_inlet_positions(valid_generator: dict[str, Any]) -> None:
    position = deepcopy(valid_generator["scenario"]["inlet_positions_xy_m"][0])
    valid_generator["scenario"]["inlet_positions_xy_m"].append(position)

    with pytest.raises(ConfigurationError, match="unique coordinates"):
        _parse(valid_generator)


def test_rejects_fill_duration_factors_not_centered_on_average(
    valid_generator: dict[str, Any],
) -> None:
    valid_generator["scenario"]["fill_duration_factor_range"] = [0.8, 1.1]

    with pytest.raises(ConfigurationError, match="centered on 1"):
        _parse(valid_generator)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fill_rate_factor_range", [1.1, 1.5]),
        ("collection_rate_factor_range", [0.4, 0.9]),
    ],
)
def test_rejects_rate_factors_without_cycle_average(
    valid_generator: dict[str, Any],
    field: str,
    value: list[float],
) -> None:
    valid_generator["scenario"][field] = value

    with pytest.raises(ConfigurationError, match="include the cycle average factor 1"):
        _parse(valid_generator)


def test_rejects_empty_reference_path(valid_generator: dict[str, Any]) -> None:
    valid_generator["quality_profile_path"] = ""

    with pytest.raises(ConfigurationError, match="non-empty string"):
        _parse(valid_generator)
