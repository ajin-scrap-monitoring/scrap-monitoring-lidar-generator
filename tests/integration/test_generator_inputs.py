"""Integration tests for generator configuration references."""

import json
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from scrap_monitoring_lidar_generator.configuration import (
    ConfigurationError,
    load_generator_inputs,
)
from scrap_monitoring_lidar_generator.geometry import HitKind, Vec2
from scrap_monitoring_lidar_generator.runtime import (
    MeasurementGenerationRuntime,
    build_measurement_generation_runtime,
    build_measurement_generators,
    build_reference_generation_runtime,
    build_rotation_schedulers,
    build_scenario_simulator,
)
from scrap_monitoring_lidar_generator.scenario import FillPlan, ScenarioPhase, scenario_time_scale

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


def test_scenario_builder_scales_rate_change_durations() -> None:
    inputs = load_generator_inputs(_EXAMPLES / "generator.v1.json")
    simulator = build_scenario_simulator(inputs)
    plan = simulator.phase_plan
    assert isinstance(plan, FillPlan)
    time_scale = scenario_time_scale(inputs.generator.scenario.mean_fill_duration_s)
    configured_range = inputs.generator.scenario.fill_rate_change_duration_s_range
    scaled_range = configured_range[0] * time_scale, configured_range[1] * time_scale

    assert plan.rate_profile.segments
    assert all(
        scaled_range[0] <= segment.duration_s <= scaled_range[1]
        for segment in plan.rate_profile.segments
    )


def test_builds_rotation_schedulers_from_generator_inputs() -> None:
    inputs = load_generator_inputs(_EXAMPLES / "generator.v1.json")
    schedulers = build_rotation_schedulers(inputs)

    assert [scheduler.sensor_id for scheduler in schedulers] == [
        sensor.sensor_id for sensor in inputs.environment.sensors
    ]
    first_scan = schedulers[0].next_scan()
    assert first_scan.scan_id == 1
    assert first_scan.point_count == 2400
    assert first_scan.captured_elapsed_s == 0.0


def test_generates_timed_reference_scan_from_generator_inputs() -> None:
    inputs = load_generator_inputs(_EXAMPLES / "generator.v1.json")
    runtime = build_reference_generation_runtime(inputs)

    (scan,) = runtime.next_completed_scans()

    assert scan.sensor_id == inputs.environment.sensors[0].sensor_id
    assert scan.scan_id == 1
    assert scan.schedule.point_count == 2400
    assert len(scan.scan.points) == scan.schedule.point_count
    assert runtime.scenario.elapsed_s == pytest.approx(1.0 / 3.0)


def test_generates_reproducible_reference_and_final_measurement_scans() -> None:
    inputs = load_generator_inputs(_EXAMPLES / "generator.v1.json")
    runtimes = [build_measurement_generation_runtime(inputs) for _ in range(2)]

    first_sequence = [runtimes[0].next_completed_scans()[0] for _ in range(4)]
    repeated_sequence = [runtimes[1].next_completed_scans()[0] for _ in range(4)]
    first = first_sequence[0]

    assert first.sensor_id == inputs.environment.sensors[0].sensor_id
    assert first.scan_id == 1
    assert first.reference.schedule is first.measured.schedule
    assert len(first.reference.scan.points) == first.measured.scan.point_count
    for generated, repeated in zip(first_sequence, repeated_sequence, strict=True):
        assert generated.reference.scan == repeated.reference.scan
        assert np.array_equal(
            generated.measured.scan.distances_m,
            repeated.measured.scan.distances_m,
        )
        assert np.array_equal(
            generated.measured.scan.qualities,
            repeated.measured.scan.qualities,
        )
    valid_distances = first.measured.scan.distances_m > 0.0
    measurement = inputs.generator.measurement
    assert bool(
        np.all(first.measured.scan.distances_m[valid_distances] >= measurement.min_distance_m)
    )
    assert bool(
        np.all(first.measured.scan.distances_m[valid_distances] <= measurement.max_distance_m)
    )
    assert set(
        int(value) for value in np.unique(first.measured.scan.qualities[valid_distances])
    ) <= {
        48,
        80,
    }
    assert set(
        int(value) for value in np.unique(first.measured.scan.qualities[~valid_distances])
    ) <= {
        0,
        24,
    }
    assert runtimes[0].scenario.elapsed_s == pytest.approx(
        4.0 / inputs.generator.measurement.rotation_rate_hz
    )


def test_generator_inputs_apply_shared_falling_material_events(tmp_path: Path) -> None:
    generator = _load_example("generator.v1.json")
    environment = _load_example("environment.v1.json")
    quality = _load_example("quality-profile.v1.json")
    generator["scenario"]["mean_fill_duration_s"] = 86_400
    generator["scenario"]["fill_duration_factor_range"] = [1, 1]
    generator["scenario"]["fill_rate_factor_range"] = [1, 1]
    generator["scenario"]["surface"]["update_interval_s"] = 0.05
    generator["scenario"]["surface"]["roughness_height_range_m"] = [0, 0]
    generator["measurement"]["distance_noise"]["enabled"] = False
    generator["measurement"]["distortions"]["reflection_error"]["enabled"] = False
    generator["measurement"]["distortions"]["dropout"]["enabled"] = False
    generator["measurement"]["distortions"]["falling_material"] = {
        "enabled": True,
        "event_rate_per_s": 100,
        "radius_m_range": [20, 20],
        "duration_s_range": [1, 1],
        "distance_reduction_m_range": [0.2, 0.2],
    }
    path = _write_inputs(tmp_path, generator, environment, quality)
    runtime = build_measurement_generation_runtime(load_generator_inputs(path))

    results = [runtime.next_completed_scans()[0] for _ in range(6)]
    reductions_m: list[float] = []
    for result in results:
        for reference_point, measured_distance_m in zip(
            result.reference.scan.points,
            result.measured.scan.distances_m,
            strict=True,
        ):
            if reference_point.hit_kind is HitKind.SURFACE:
                reductions_m.append(reference_point.distance_m - float(measured_distance_m))

    assert reductions_m
    assert any(reduction_m == pytest.approx(0.2) for reduction_m in reductions_m)
    assert all(
        reduction_m == pytest.approx(0.0) or reduction_m == pytest.approx(0.2)
        for reduction_m in reductions_m
    )


def test_rejects_invalid_measurement_runtime_sensor_sets() -> None:
    inputs = load_generator_inputs(_EXAMPLES / "generator.v1.json")
    reference_runtime = build_reference_generation_runtime(inputs)
    (generator,) = build_measurement_generators(inputs)

    with pytest.raises(ValueError, match="sensor sets"):
        MeasurementGenerationRuntime(reference_runtime=reference_runtime, generators=())
    with pytest.raises(ValueError, match="unique"):
        MeasurementGenerationRuntime(
            reference_runtime=reference_runtime,
            generators=(generator, generator),
        )


def test_generator_inputs_scale_and_apply_dropout_intervals(tmp_path: Path) -> None:
    generator = _load_example("generator.v1.json")
    environment = _load_example("environment.v1.json")
    quality = _load_example("quality-profile.v1.json")
    generator["scenario"]["mean_fill_duration_s"] = 86_400
    generator["measurement"]["distortions"]["dropout"] = {
        "enabled": True,
        "event_interval_s_range": [1, 1],
        "duration_s_range": [1, 1],
    }
    path = _write_inputs(tmp_path, generator, environment, quality)
    runtime = build_measurement_generation_runtime(load_generator_inputs(path))

    results = [runtime.next_completed_scans()[0] for _ in range(4)]
    dropout_scan = results[-1].measured.scan

    assert results[-1].measured.captured_elapsed_s == 1.0
    assert bool(np.all(dropout_scan.distances_m == 0.0))
    assert set(int(value) for value in np.unique(dropout_scan.qualities)) <= {0, 24}


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
