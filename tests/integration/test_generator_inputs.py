"""Integration tests for generator configuration references."""

import json
from pathlib import Path
from typing import Any, cast

import pytest

from scrap_monitoring_lidar_generator.configuration import (
    ConfigurationError,
    load_generator_inputs,
)
from scrap_monitoring_lidar_generator.geometry import Vec2
from scrap_monitoring_lidar_generator.runtime import build_scenario_simulator
from scrap_monitoring_lidar_generator.scenario import ScenarioPhase

_ROOT = Path(__file__).parents[2]
_EXAMPLES = _ROOT / "examples"


def _load_example(name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((_EXAMPLES / name).read_text(encoding="utf-8")),
    )


def _write_inputs(
    directory: Path,
    generator: dict[str, Any],
    environment: dict[str, Any],
    quality: dict[str, Any],
) -> Path:
    files = {
        "generator.v1.json": generator,
        "environment.v1.json": environment,
        "quality-profile.v1.json": quality,
    }
    for name, value in files.items():
        (directory / name).write_text(json.dumps(value), encoding="utf-8")
    return directory / "generator.v1.json"


def test_loads_generator_and_referenced_inputs() -> None:
    inputs = load_generator_inputs(_EXAMPLES / "generator.v1.json")

    assert inputs.environment.environment_id == "synthetic-room-v1"
    assert inputs.generator.seed == 123456789
    assert inputs.quality_profile.sensors[0].sensor_id == "sensor-a"


def test_builds_running_scenario_from_generator_inputs() -> None:
    inputs = load_generator_inputs(_EXAMPLES / "generator.v1.json")
    simulator = build_scenario_simulator(inputs)

    initial = simulator.snapshot
    updated = simulator.advance_to(inputs.generator.scenario.surface.update_interval_s)

    assert initial.phase is ScenarioPhase.FILLING
    assert initial.surface_volume_m3 == 0.0
    assert updated.surface_volume_m3 > 0.0
    assert simulator.surface.boundary.contains(
        Vec2(*inputs.generator.scenario.inlet_positions_xy_m[0])
    )


def test_rejects_quality_sensor_mismatch(tmp_path: Path) -> None:
    generator = _load_example("generator.v1.json")
    environment = _load_example("environment.v1.json")
    quality = _load_example("quality-profile.v1.json")
    quality["sensors"][0]["sensor_id"] = "different-sensor"
    path = _write_inputs(tmp_path, generator, environment, quality)

    with pytest.raises(ConfigurationError, match="exactly match"):
        load_generator_inputs(path)


def test_rejects_inlet_outside_environment(tmp_path: Path) -> None:
    generator = _load_example("generator.v1.json")
    environment = _load_example("environment.v1.json")
    quality = _load_example("quality-profile.v1.json")
    generator["scenario"]["inlet_positions_xy_m"][0] = [100, 100]
    path = _write_inputs(tmp_path, generator, environment, quality)

    with pytest.raises(ConfigurationError, match="inside the environment boundary"):
        load_generator_inputs(path)
