"""Generate or check bounded Python reference fixtures for the Rust migration."""

import argparse
import copy
import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import numpy as np

from scrap_monitoring_lidar_generator.configuration import (
    ConfigurationError,
    GeneratorInputs,
    build_environment_scene,
    build_sensor_frame,
    load_generator_inputs,
    parse_environment,
    parse_generator_config,
    parse_quality_profile,
)
from scrap_monitoring_lidar_generator.geometry import HitKind, Vec2
from scrap_monitoring_lidar_generator.measurement import (
    CollectionOcclusionEvent,
    FallingMaterialEvent,
    MeasuredScan,
    MeasurementResult,
    ReferencePoint,
    ReferenceScan,
    ReferenceScanner,
    ScheduledScan,
    SensorDropoutScheduler,
    SensorRotationScheduler,
    TimedMeasuredScan,
    TimedReferenceScan,
    VoidEvent,
    create_seeded_rotation_scheduler,
    resolve_collection_occlusion_distances,
    resolve_falling_material_distances,
    resolve_void_distances,
)
from scrap_monitoring_lidar_generator.measurement.sdk_compatibility import (
    HQ_ANGLE_STEP_DEG,
    quantize_hq_angles_deg,
    quantize_hq_distances_m,
)
from scrap_monitoring_lidar_generator.runtime import (
    ReferenceGenerationRuntime,
    build_measurement_generation_runtime,
    build_reference_generation_runtime,
    build_scenario_simulator,
    build_spatial_distortion_timeline,
)
from scrap_monitoring_lidar_generator.scan_stream import ScanFrameFactory
from scrap_monitoring_lidar_generator.scenario import HeightField
from scrap_monitoring_lidar_generator.wire import lidar_pb2

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIRECTORY = ROOT / "tests" / "fixtures" / "rust-parity"
MAX_FIXTURE_BYTES = 256 * 1024
FLOAT_ABSOLUTE_TOLERANCE = 1e-10
FLOAT_RELATIVE_TOLERANCE = 1e-12
PYTHON_SEEDED_FLOAT_ABSOLUTE_TOLERANCE = 1e-8

_EXAMPLE_NAMES = ("environment.v1.json", "generator.v2.json", "quality-profile.v1.json")
_SCRIPTED_OVERRIDES: dict[str, Any] = {
    "scenario": {
        "mean_fill_duration_s": 4.25,
        "fill_duration_factor_range": [1.0, 1.0],
        "fill_rate_factor_range": [1.0, 1.0],
        "collection_threshold_range": [0.6, 0.6],
        "collection_duration_factor_range": [0.2, 0.2],
        "collection_rate_factor_range": [1.0, 1.0],
        "surface": {"roughness_height_range_m": [0.0, 0.0]},
    },
    "measurement": {
        "sample_rate_hz": 40.0,
        "rotation_rate_hz": 4.0,
        "distance_noise": {"enabled": False},
        "distortions": {
            name: {"enabled": False}
            for name in (
                "falling_material",
                "voids",
                "collection_occlusion",
                "reflection_error",
                "dropout",
            )
        },
    },
}
_SEEDED_OVERRIDES: dict[str, Any] = {
    "scenario": {
        "mean_fill_duration_s": 4.0,
        "fill_duration_factor_range": [1.0, 1.0],
        "fill_rate_change_duration_s_range": [21_600.0, 43_200.0],
        "collection_threshold_range": [0.6, 0.6],
        "collection_duration_factor_range": [0.25, 0.25],
        "collection_rate_change_duration_s_range": [5_400.0, 10_800.0],
    },
    "measurement": {
        "sample_rate_hz": 40.0,
        "rotation_rate_hz": 4.0,
        "distortions": {
            "falling_material": {
                "event_rate_per_s": 4.0 / 86_400.0,
                "radius_m_range": [0.2, 0.5],
                "duration_s_range": [2_160.0, 6_480.0],
            },
            "voids": {
                "surface_area_ratio": 0.01,
                "radius_m_range": [0.15, 0.25],
                "duration_s_range": [10_800.0, 21_600.0],
            },
            "collection_occlusion": {
                "event_interval_s_range": [2_160.0, 4_320.0],
                "duration_s_range": [6_480.0, 8_640.0],
            },
            "reflection_error": {"probability": 0.1},
            "dropout": {
                "enabled": True,
                "event_interval_s_range": [8_640.0, 8_640.0],
                "duration_s_range": [2_160.0, 2_160.0],
            },
        },
    },
}


def _document(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _merge(target: dict[str, Any], overrides: dict[str, Any]) -> None:
    for key, value in overrides.items():
        if isinstance(value, dict):
            _merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def _inputs(overrides: dict[str, Any] | None = None) -> GeneratorInputs:
    public_document = _document("generator.v2.json")
    _require_public_references(public_document)
    public = load_generator_inputs(ROOT / "examples" / "generator.v2.json")
    if overrides is None:
        return public
    document = copy.deepcopy(public_document)
    _merge(document, overrides)
    _require_public_references(document)
    config = parse_generator_config(json.dumps(document), base_directory=ROOT / "examples")
    return replace(public, generator=config)


def _require_public_references(document: dict[str, Any]) -> None:
    examples_directory = (ROOT / "examples").resolve()
    for field, expected_name in (
        ("environment_path", "environment.v1.json"),
        ("quality_profile_path", "quality-profile.v1.json"),
    ):
        value = document.get(field)
        if value != expected_name:
            raise ValueError(f"Rust parity fixtures require examples/{expected_name}")
        source = ROOT / "examples" / expected_name
        if source.is_symlink() or source.resolve().parent != examples_directory:
            raise ValueError(
                f"Rust parity fixture source must be a regular public example: {field}"
            )


def _normalize(value: Any) -> Any:
    if isinstance(value, Path):
        return value.relative_to(ROOT).as_posix()
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_normalize(item) for item in value]
    return value


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(_normalize(value), indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode()


def _configuration_cases() -> dict[str, Any]:
    parsers: dict[str, Callable[[str], Any]] = {
        "environment.v1.json": parse_environment,
        "generator.v2.json": lambda document: parse_generator_config(
            document, base_directory=ROOT / "examples"
        ),
        "quality-profile.v1.json": parse_quality_profile,
    }
    cases: list[dict[str, Any]] = []
    mutations: tuple[tuple[str, str, list[str | int], Any], ...] = (
        ("maximum-seed", "generator.v2.json", ["seed"], (1 << 64) - 1),
        ("seed-overflow", "generator.v2.json", ["seed"], 1 << 64),
        ("boolean-seed", "generator.v2.json", ["seed"], True),
        ("fractional-seed", "generator.v2.json", ["seed"], 1.0),
        ("numeric-version", "generator.v2.json", ["config_version"], 2.0),
        ("boolean-version", "generator.v2.json", ["config_version"], True),
        ("unknown-nested-field", "generator.v2.json", ["scenario", "extra"], 1),
        ("sampling-under-rotation", "generator.v2.json", ["measurement", "sample_rate_hz"], 1),
        ("large-noise", "generator.v2.json", ["measurement", "distance_noise", "limit_m"], 20),
        ("wrong-unit", "environment.v1.json", ["length_unit"], "mm"),
        ("non-unit-frame", "environment.v1.json", ["sensors", 0, "u0"], [0, 0, -2]),
        ("empty-sensors", "environment.v1.json", ["sensors"], []),
        ("repeated-polygon-node", "environment.v1.json", ["boundary_xy_m"], [[0, 0]] * 3),
        (
            "noncanonical-quality-key",
            "quality-profile.v1.json",
            ["sensors", 0, "valid_distance_frequencies"],
            {"01": 1},
        ),
        (
            "maximum-quality-frequency",
            "quality-profile.v1.json",
            ["sensors", 0, "valid_distance_frequencies"],
            {"255": (1 << 63) - 1},
        ),
        (
            "boolean-quality-frequency",
            "quality-profile.v1.json",
            ["sensors", 0, "valid_distance_frequencies"],
            {"48": True},
        ),
    )
    for name, source, path, replacement in mutations:
        document = _document(source)
        parent: Any = document
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = replacement
        cases.append(
            {
                "name": name,
                "source": f"examples/{source}",
                "replace": {"path": path, "value": replacement},
                "expected": _parse_outcome(parsers[source], json.dumps(document)),
            }
        )
    for name, raw_document in (
        ("duplicate-field", '{"seed":1,"seed":2}'),
        ("non-finite-number", '{"seed":NaN}'),
        ("missing-fields", "{}"),
        ("non-object-root", "[]"),
    ):
        cases.append(
            {
                "name": name,
                "source": "examples/generator.v2.json",
                "document": raw_document,
                "expected": _parse_outcome(parsers["generator.v2.json"], raw_document),
            }
        )
    return {
        "comparison_class": "rng-independent",
        "public_normalized": {
            name: asdict(parsers[name]((ROOT / "examples" / name).read_text()))
            for name in _EXAMPLE_NAMES
        },
        "cases": cases,
    }


def _parse_outcome(parser: Callable[[str], Any], document: str) -> dict[str, Any]:
    try:
        parser(document)
    except ConfigurationError as error:
        return {"accepted": False, "error": str(error)}
    return {"accepted": True}


def _surface(inputs: GeneratorInputs) -> HeightField:
    return build_scenario_simulator(inputs).surface


def _deposited_surface(inputs: GeneratorInputs) -> HeightField:
    surface = _surface(inputs)
    scenario = inputs.generator.scenario
    surface.add_volume(
        surface.capacity_m3 * 0.3,
        center=Vec2(*scenario.inlet_positions_xy_m[0]),
        spread_radius_m=scenario.surface.pile_spread_radius_m,
    )
    surface.relax_slopes()
    return surface


def _surface_state(surface: HeightField) -> dict[str, Any]:
    return {
        "heights_m": surface.heights_m.tolist(),
        "volume_m3": surface.volume_m3,
        "fill_ratio": surface.fill_ratio,
    }


def _height_field_operations(inputs: GeneratorInputs) -> dict[str, Any]:
    surface = _surface(inputs)
    config = inputs.generator.scenario
    center = Vec2(*config.inlet_positions_xy_m[0])
    operations: list[dict[str, Any]] = []
    change = surface.add_volume(
        surface.capacity_m3 * 0.3,
        center=center,
        spread_radius_m=config.surface.pile_spread_radius_m,
    )
    operations.append(
        {
            "operation": "add_capacity_fraction",
            "fraction": 0.3,
            "change": asdict(change),
            "state": _surface_state(surface),
        }
    )
    relaxation = surface.relax_slopes()
    operations.append(
        {
            "operation": "relax_slopes",
            "result": asdict(relaxation),
            "state": _surface_state(surface),
        }
    )
    roughness = surface.apply_local_roughness(center=center, radius_m=0.75, peak_delta_m=0.1)
    operations.append(
        {
            "operation": "roughness",
            "radius_m": 0.75,
            "peak_delta_m": 0.1,
            "result": asdict(roughness),
            "state": _surface_state(surface),
        }
    )
    removed = surface.remove_volume_uniformly(surface.capacity_m3 * 0.1)
    operations.append(
        {
            "operation": "remove_capacity_fraction",
            "fraction": 0.1,
            "change": asdict(removed),
            "state": _surface_state(surface),
        }
    )
    emptied = surface.remove_volume_uniformly(surface.capacity_m3)
    operations.append(
        {
            "operation": "remove_capacity_fraction",
            "fraction": 1.0,
            "change": asdict(emptied),
            "state": _surface_state(surface),
        }
    )
    return {
        "comparison_class": "rng-independent",
        "source": "examples/generator.v2.json",
        "grid_order": "y-major",
        "x_coordinates_m": surface.x_coordinates_m.tolist(),
        "y_coordinates_m": surface.y_coordinates_m.tolist(),
        "node_area_m2": surface._volume_weights_m2.tolist(),
        "capacity_m3": surface.capacity_m3,
        "surface_area_m2": surface.surface_area_m2,
        "inlet_index": 0,
        "operations": operations,
    }


def _coordinates(inputs: GeneratorInputs) -> dict[str, Any]:
    surface = _deposited_surface(inputs)
    scenes = {
        "empty": build_environment_scene(inputs.environment),
        "deposited": build_environment_scene(inputs.environment, dynamic_surface=surface),
    }
    requested_angles = np.array((0.0, 15.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0))
    angles = quantize_hq_angles_deg(requested_angles)
    measurements = []
    for sensor in inputs.environment.sensors:
        frame = build_sensor_frame(sensor)
        for scene_name, scene in scenes.items():
            for requested, angle in zip(requested_angles, angles, strict=True):
                ray = frame.ray_at(float(angle))
                hit = scene.first_hit(ray, min_distance_m=0.05, max_distance_m=30.0)
                measurements.append(
                    {
                        "sensor_id": sensor.sensor_id,
                        "scene": scene_name,
                        "requested_angle_deg": float(requested),
                        "angle_deg": float(angle),
                        "direction": asdict(ray.direction),
                        "hit": None if hit is None else asdict(hit),
                    }
                )
    return {
        "comparison_class": "rng-independent",
        "deposited_surface": "height-field-operations.json operations[1]",
        "measurements": measurements,
    }


def _scenario_scripted() -> dict[str, Any]:
    scenario = build_scenario_simulator(_inputs(_SCRIPTED_OVERRIDES))
    fill_end = scenario.phase_plan.ends_at_s
    collection_end = fill_end + scenario.phase_plan.duration_s * 0.2
    times = (
        0.0,
        0.49,
        0.5,
        0.51,
        1.0,
        fill_end - 1e-6,
        fill_end,
        collection_end - 1e-6,
        collection_end,
        5.5,
    )
    states = []
    for elapsed_s in times:
        scenario.advance_to(elapsed_s)
        state: dict[str, Any] = {"snapshot": asdict(scenario.snapshot)}
        if elapsed_s in (1.0, fill_end, collection_end):
            state["heights_m"] = scenario.surface.heights_m.tolist()
        states.append(state)
    return {
        "comparison_class": "rng-independent",
        "generator_overrides": _SCRIPTED_OVERRIDES,
        "states": states,
        "sample_event_rule": "surface events precede samples with the same elapsed time",
        "within_scan_event": _within_scan_event(),
    }


def _within_scan_event() -> dict[str, Any]:
    overrides = copy.deepcopy(_SCRIPTED_OVERRIDES)
    _merge(overrides, {"measurement": {"rotation_rate_hz": 1.0}})
    inputs = _inputs(overrides)
    scenario = build_scenario_simulator(inputs)
    scene = build_environment_scene(inputs.environment, dynamic_surface=scenario.surface)
    measurement = inputs.generator.measurement
    event_angles_deg = {
        "lidar_1": 353.583984375,
        "lidar_2": 13.53515625,
    }
    scanners = tuple(
        ReferenceScanner(
            sensor_id=sensor.sensor_id,
            frame=build_sensor_frame(sensor),
            min_distance_m=measurement.min_distance_m,
            max_distance_m=measurement.max_distance_m,
        )
        for sensor in inputs.environment.sensors
    )
    schedulers = tuple(
        SensorRotationScheduler(
            sensor_id=sensor.sensor_id,
            sample_rate_hz=measurement.sample_rate_hz,
            rotation_rate_hz=measurement.rotation_rate_hz,
            initial_angle_deg=(event_angles_deg[sensor.sensor_id] - 180.0) % 360.0,
        )
        for sensor in inputs.environment.sensors
    )
    runtime = ReferenceGenerationRuntime(
        scenario=scenario,
        scene=scene,
        scanners=scanners,
        schedulers=schedulers,
    )
    results = runtime.next_completed_scans()
    static_scene = build_environment_scene(inputs.environment)
    event_samples = []
    scanners_by_id = {scanner.sensor_id: scanner for scanner in scanners}
    for result in results:
        matching_indices = np.flatnonzero(result.schedule.point_elapsed_times_s == 0.5)
        if matching_indices.size != 1:
            raise RuntimeError("within-scan fixture must contain one sample at the surface event")
        index = int(matching_indices[0])
        point = result.scan.points[index]
        angle_deg = float(result.schedule.angles_deg[index])
        prior_hit = static_scene.first_hit(
            scanners_by_id[result.sensor_id].frame.ray_at(angle_deg),
            min_distance_m=measurement.min_distance_m,
            max_distance_m=measurement.max_distance_m,
        )
        if prior_hit is None:
            raise RuntimeError("within-scan fixture event ray must meet the prior scene")
        event_samples.append(
            {
                "sensor_id": result.sensor_id,
                "sample_index": index,
                "angle_deg": angle_deg,
                "prior_hit_kind": prior_hit.kind,
                "prior_distance_m": prior_hit.distance_m,
                "runtime_hit_kind": point.hit_kind,
                "runtime_distance_m": point.distance_m,
            }
        )
    return {
        "generator_overrides": overrides,
        "completed_at_s": 1.0,
        "surface_event_s": 0.5,
        "event_angles_deg": event_angles_deg,
        "event_samples": event_samples,
        "scans": [_reference_record(result) for result in results],
    }


def _reference_record(reference: TimedReferenceScan) -> dict[str, Any]:
    return {
        "sensor_id": reference.sensor_id,
        "scan_id": reference.scan_id,
        "rotation_started_at_s": reference.schedule.rotation_started_at_s,
        "completed_at_s": reference.completed_at_s,
        "angles_deg": reference.schedule.angles_deg.tolist(),
        "point_elapsed_times_s": reference.schedule.point_elapsed_times_s.tolist(),
        "distances_m": [point.distance_m for point in reference.scan.points],
        "hit_kinds": [point.hit_kind for point in reference.scan.points],
    }


def _scenario_seeded() -> dict[str, Any]:
    inputs = _inputs(_SEEDED_OVERRIDES)
    runtime = build_measurement_generation_runtime(inputs)
    records = []
    for index in range(24):
        results = runtime.next_completed_scans()
        if index not in (1, 7, 14, 15, 18, 19, 23):
            continue
        records.append(
            {
                "snapshot": asdict(runtime.scenario.snapshot),
                "inlet_heights_m": [
                    runtime.scenario.surface.height_at(Vec2(*position))
                    for position in inputs.generator.scenario.inlet_positions_xy_m
                ],
                "scans": [
                    {
                        "reference": _reference_record(result.reference),
                        "distances_m": result.measured.scan.distances_m.tolist(),
                        "quality_bytes": result.measured.scan.qualities.tolist(),
                    }
                    for result in results
                ],
            }
        )
    return {
        "comparison_class": "python-seeded-reference",
        "generator_overrides": _SEEDED_OVERRIDES,
        "records": records,
        "scenario_plan": _seeded_plans(inputs),
        "spatial_events": _seeded_spatial_events(inputs, through_s=6.0),
    }


def _seeded_plans(inputs: GeneratorInputs) -> list[dict[str, Any]]:
    scenario = build_scenario_simulator(inputs)
    plans = []
    for _ in range(3):
        plan = scenario.phase_plan
        plans.append(
            {
                "snapshot": asdict(scenario.snapshot),
                "segments": [asdict(segment) for segment in plan.rate_profile.segments],
            }
        )
        scenario.advance_to(plan.ends_at_s)
    return plans


def _seeded_spatial_events(inputs: GeneratorInputs, *, through_s: float) -> dict[str, Any]:
    scenario = build_scenario_simulator(inputs)
    timeline = build_spatial_distortion_timeline(inputs)
    if timeline is None:
        raise RuntimeError("seeded fixture requires a spatial distortion timeline")
    while scenario.elapsed_s < through_s:
        interval_end_s = min(scenario.next_surface_event_elapsed_s, through_s)
        timeline.advance_to(interval_end_s, scenario=scenario)
        scenario.advance_to(interval_end_s)
    return {
        "generated_through_s": through_s,
        "falling_material": [asdict(event) for event in timeline.falling_events],
        "voids": [asdict(event) for event in timeline.void_events],
        "collection_occlusion": [asdict(event) for event in timeline.collection_events],
    }


def _rotation_and_hq(inputs: GeneratorInputs) -> dict[str, Any]:
    rotations = []
    sensor_id = inputs.environment.sensors[0].sensor_id
    for sample_rate, rotation_rate in ((8.0, 2.0), (5.0, 2.0), (7.5, 2.0)):
        scheduler = SensorRotationScheduler(
            sensor_id=sensor_id,
            sample_rate_hz=sample_rate,
            rotation_rate_hz=rotation_rate,
            initial_angle_deg=350.0,
        )
        scans = []
        for _ in range(4):
            schedule = scheduler.next_scan()
            scans.append(
                {
                    "scan_id": schedule.scan_id,
                    "rotation_started_at_s": schedule.rotation_started_at_s,
                    "completed_at_s": schedule.completed_at_s,
                    "angles_deg": schedule.angles_deg.tolist(),
                    "point_elapsed_times_s": schedule.point_elapsed_times_s.tolist(),
                }
            )
        rotations.append(
            {
                "sample_rate_hz": sample_rate,
                "rotation_rate_hz": rotation_rate,
                "initial_angle_deg": 350.0,
                "scans": scans,
            }
        )
    angle_half_tick = HQ_ANGLE_STEP_DEG / 2.0
    distance_half_tick = 1.0 / 8_000.0
    angles = np.array(
        (
            0.0,
            np.nextafter(angle_half_tick, 0.0),
            angle_half_tick,
            np.nextafter(angle_half_tick, 1.0),
            90.0,
            180.0,
            270.0,
            359.999,
        )
    )
    distances = np.array(
        (
            0.0,
            np.nextafter(distance_half_tick, 0.0),
            distance_half_tick,
            np.nextafter(distance_half_tick, 1.0),
            0.0499,
            0.05,
            1.2346,
            30.0,
        )
    )
    settings = inputs.generator.measurement
    return {
        "comparison_class": "rng-independent",
        "rotations": rotations,
        "seeded_initial_angles_deg": {
            sensor.sensor_id: create_seeded_rotation_scheduler(
                sensor_id=sensor.sensor_id,
                sample_rate_hz=settings.sample_rate_hz,
                rotation_rate_hz=settings.rotation_rate_hz,
                seed=inputs.generator.seed,
            ).initial_angle_deg
            for sensor in inputs.environment.sensors
        },
        "angle_quantization": {
            "input_deg": angles.tolist(),
            "output_deg": quantize_hq_angles_deg(angles).tolist(),
            "hq_ticks": np.rint(quantize_hq_angles_deg(angles) / HQ_ANGLE_STEP_DEG)
            .astype(int)
            .tolist(),
        },
        "distance_quantization": {
            "input_m": distances.tolist(),
            "output_m": quantize_hq_distances_m(distances).tolist(),
            "hq_ticks": np.rint(quantize_hq_distances_m(distances) * 4_000).astype(int).tolist(),
        },
    }


def _timed_reference(
    sensor_id: str,
    *,
    angles_deg: list[float],
    distances_m: list[float],
    hit_kinds: list[HitKind | None],
    completed_at_s: float,
) -> TimedReferenceScan:
    count = len(angles_deg)
    schedule = ScheduledScan(
        sensor_id=sensor_id,
        scan_id=1,
        rotation_started_at_s=0.0,
        completed_at_s=completed_at_s,
        angles_deg=np.array(angles_deg),
        point_elapsed_times_s=np.arange(count, dtype=np.float64) * completed_at_s / count,
    )
    return TimedReferenceScan(
        schedule=schedule,
        scan=ReferenceScan(
            sensor_id=sensor_id,
            points=tuple(
                ReferencePoint(angle_deg=angle, distance_m=distance, hit_kind=kind)
                for angle, distance, kind in zip(angles_deg, distances_m, hit_kinds, strict=True)
            ),
        ),
    )


def _distortion_events(inputs: GeneratorInputs) -> dict[str, Any]:
    surface = _deposited_surface(inputs)
    scene = build_environment_scene(inputs.environment, dynamic_surface=surface)
    static_scene = build_environment_scene(inputs.environment)
    results = []
    for sensor in inputs.environment.sensors:
        frame = build_sensor_frame(sensor)
        ray = frame.ray_at(0.0)
        hit = scene.first_hit(ray, min_distance_m=0.05, max_distance_m=30.0)
        if hit is None or hit.kind is not HitKind.SURFACE:
            raise RuntimeError("public zero-angle fixture ray must meet the deposited surface")
        center = Vec2(hit.position_m.x, hit.position_m.y)
        reference = _timed_reference(
            sensor.sensor_id,
            angles_deg=[0.0] * 5,
            distances_m=[hit.distance_m] * 5,
            hit_kinds=[HitKind.SURFACE] * 5,
            completed_at_s=2.5,
        )
        falling = (
            FallingMaterialEvent(0, 0.5, 1.5, center, 0.25, 0.5),
            FallingMaterialEvent(0, 1.0, 1.5, center, 0.25, 0.75),
        )
        collection = (
            CollectionOcclusionEvent(
                0,
                0.5,
                1.5,
                Vec2(center.x - 0.5, center.y),
                Vec2(center.x + 0.5, center.y),
                0.25,
                0.5,
            ),
        )
        voids = tuple(
            VoidEvent(0, 0.5, 1.5, 1.5, center, hit.position_m.z, 0.25, increase)
            for increase in (0.3, 0.1)
        )
        blocked_void = replace(voids[0], distance_increase_m=30.0)
        floor_reference = replace(
            reference,
            scan=ReferenceScan(
                sensor_id=sensor.sensor_id,
                points=tuple(
                    replace(point, hit_kind=HitKind.FLOOR) for point in reference.scan.points
                ),
            ),
        )
        dropout = SensorDropoutScheduler(
            sensor_id=sensor.sensor_id,
            event_interval_s_range=(0.5, 0.5),
            duration_s_range=(0.5, 0.5),
            seed=inputs.generator.seed,
        )
        results.append(
            {
                "reference": _reference_record(reference),
                "falling": {
                    "events": [asdict(event) for event in falling],
                    "distances_m": resolve_falling_material_distances(
                        reference,
                        frame=frame,
                        events=falling,
                        min_distance_m=0.05,
                    ).tolist(),
                    "floor_hit_distances_m": resolve_falling_material_distances(
                        floor_reference,
                        frame=frame,
                        events=falling,
                        min_distance_m=0.05,
                    ).tolist(),
                },
                "collection": {
                    "events": [asdict(event) for event in collection],
                    "distances_m": resolve_collection_occlusion_distances(
                        reference,
                        frame=frame,
                        events=collection,
                        min_distance_m=0.05,
                    ).tolist(),
                },
                "voids": {
                    "events": [asdict(event) for event in voids],
                    "distances_m": resolve_void_distances(
                        reference,
                        frame=frame,
                        events=voids,
                        min_distance_m=0.05,
                        max_distance_m=30.0,
                        static_scene=static_scene,
                    ).tolist(),
                    "blocked_event": asdict(blocked_void),
                    "blocked_distances_m": resolve_void_distances(
                        reference,
                        frame=frame,
                        events=(blocked_void,),
                        min_distance_m=0.05,
                        max_distance_m=30.0,
                        static_scene=static_scene,
                    ).tolist(),
                },
                "dropout": {
                    "inactive_gap_s": 0.5,
                    "duration_s": 0.5,
                    "active_mask": dropout.active_mask(
                        reference.schedule.point_elapsed_times_s
                    ).tolist(),
                },
            }
        )
    return {
        "comparison_class": "rng-independent",
        "deposited_surface": "height-field-operations.json operations[1]",
        "event_lifetime": "[started_at_s, ends_at_s)",
        "sensors": results,
    }


def _fixed_quality_result(
    reference: TimedReferenceScan, inputs: GeneratorInputs
) -> MeasurementResult:
    profile = next(
        sensor
        for sensor in inputs.quality_profile.sensors
        if sensor.sensor_id == reference.sensor_id
    )
    valid_quality = next(
        index for index, weight in enumerate(profile.valid_distance_frequencies) if weight > 0
    )
    invalid_quality = next(
        index for index, weight in enumerate(profile.invalid_distance_frequencies) if weight > 0
    )
    distances = quantize_hq_distances_m(
        np.array([point.distance_m for point in reference.scan.points])
    )
    measured = MeasuredScan(
        sensor_id=reference.sensor_id,
        angles_deg=reference.schedule.angles_deg,
        distances_m=distances,
        qualities=np.where(distances > 0.0, valid_quality, invalid_quality).astype(np.uint8),
    )
    return MeasurementResult(
        reference=reference, measured=TimedMeasuredScan(schedule=reference.schedule, scan=measured)
    )


def _frame_record(frame: lidar_pb2.ScanFrame) -> dict[str, Any]:
    return {
        "schema_version": frame.schema_version,
        "edge_id": frame.edge_id,
        "sensor_id": frame.sensor_id,
        "sequence": frame.sequence,
        "acquired_at_unix_ms": frame.acquired_at_unix_ms,
        "acquired_monotonic_ns": frame.acquired_monotonic_ns,
        "sdk_status": frame.sdk_status,
        "scan_hz": frame.scan_hz,
        "samples": [
            {
                "angle_mdeg": point.angle_mdeg,
                "distance_mm": point.distance_mm,
                "quality": point.quality,
            }
            for point in frame.samples
        ],
        "instance_id": frame.instance_id,
        "config_revision": frame.config_revision,
    }


def _scan_frames() -> dict[str, Any]:
    inputs = _inputs(_SCRIPTED_OVERRIDES)
    runtime = build_reference_generation_runtime(inputs)
    sensor_ids = tuple(sensor.sensor_id for sensor in inputs.environment.sensors)
    completion_times = iter(
        1_000_000_000 + rotation * 250_000_000 + index
        for rotation in range(4)
        for index in range(len(sensor_ids))
    )
    factory = ScanFrameFactory(
        sensor_ids=sensor_ids,
        edge_id="synthetic-edge",
        config_revision="parity-v1",
        instance_ids={sensor_id: f"parity-{sensor_id}" for sensor_id in sensor_ids},
        monotonic_ns=lambda: next(completion_times),
        unix_ms=lambda: 1_800_000_000_000,
    )
    records = []
    for _ in range(4):
        for reference in runtime.next_completed_scans():
            frame = factory.build(_fixed_quality_result(reference, inputs))
            records.append(
                {
                    "reference": _reference_record(reference),
                    "frame": None if frame is None else _frame_record(frame),
                    "protobuf_hex": None
                    if frame is None
                    else frame.SerializeToString(deterministic=True).hex(),
                }
            )
    return {
        "comparison_class": "rng-independent",
        "generator_overrides": _SCRIPTED_OVERRIDES,
        "quality_policy": "lowest configured quality byte with positive frequency",
        "records": records,
        "stable_sort_case": _stable_sort_frame(sensor_ids[0]),
    }


def _stable_sort_frame(sensor_id: str) -> dict[str, Any]:
    angles = quantize_hq_angles_deg(np.array((90.0, 10.0, 10.0, 359.999, 180.0)))
    distances = quantize_hq_distances_m(np.array((1.2346, 2.00025, 3.00075, 0.0, 30.0)))
    qualities = np.array((255, 48, 80, 24, 0), dtype=np.uint8)
    reference = _timed_reference(
        sensor_id,
        angles_deg=angles.tolist(),
        distances_m=distances.tolist(),
        hit_kinds=[HitKind.WALL, HitKind.WALL, HitKind.WALL, None, HitKind.WALL],
        completed_at_s=0.1,
    )
    result = MeasurementResult(
        reference=reference,
        measured=TimedMeasuredScan(
            schedule=reference.schedule,
            scan=MeasuredScan(
                sensor_id=sensor_id, angles_deg=angles, distances_m=distances, qualities=qualities
            ),
        ),
    )
    times = iter((1_000_000_000, 1_100_000_000))
    factory = ScanFrameFactory(
        sensor_ids=(sensor_id,),
        edge_id="synthetic-edge",
        config_revision="parity-v1",
        instance_ids={sensor_id: f"parity-{sensor_id}"},
        monotonic_ns=lambda: next(times),
        unix_ms=lambda: 1_800_000_000_000,
    )
    factory.build(result)
    frame = factory.build(result)
    if frame is None:
        raise RuntimeError("second completed scan must publish a frame")
    return {
        "input_angles_deg": angles.tolist(),
        "input_distances_m": distances.tolist(),
        "input_quality_bytes": qualities.tolist(),
        "frame": _frame_record(frame),
        "protobuf_hex": frame.SerializeToString(deterministic=True).hex(),
    }


def _source_fingerprints() -> dict[str, str]:
    package = ROOT / "src" / "scrap_monitoring_lidar_generator"
    sources = [ROOT / "examples" / name for name in _EXAMPLE_NAMES]
    sources.extend(
        (
            ROOT / "uv.lock",
            ROOT / "contracts" / "lidar" / "v1" / "lidar.proto",
            Path(__file__).resolve(),
        )
    )
    for directory in ("configuration", "geometry", "scenario", "measurement"):
        sources.extend((package / directory).glob("*.py"))
    sources.extend(
        package / "runtime" / name
        for name in (
            "generation.py",
            "reference.py",
            "scenario.py",
            "measurement.py",
        )
    )
    sources.append(package / "scan_stream" / "frames.py")
    return {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(sources)
    }


def build_fixtures() -> dict[str, bytes]:
    """Return all fixture files without writing or reading private input data."""
    inputs = _inputs()
    documents = {
        "configuration-cases.json": _configuration_cases(),
        "coordinates.json": _coordinates(inputs),
        "height-field-operations.json": _height_field_operations(inputs),
        "scenario-scripted.json": _scenario_scripted(),
        "scenario-seeded-baseline.json": _scenario_seeded(),
        "rotation-and-hq.json": _rotation_and_hq(inputs),
        "distortion-events.json": _distortion_events(inputs),
        "scan-frames.json": _scan_frames(),
    }
    fixtures = {name: _json_bytes(document) for name, document in documents.items()}
    metadata = {
        "fixture_version": 1,
        "baseline": {
            "implementation": "python",
            "release": "0.9.0",
            "random_sources": ["random.Random", "numpy.random.PCG64"],
        },
        "provenance": "public examples and explicit synthetic test overrides",
        "source_sha256": _source_fingerprints(),
        "comparison": {
            "float_absolute_tolerance": FLOAT_ABSOLUTE_TOLERANCE,
            "float_relative_tolerance": FLOAT_RELATIVE_TOLERANCE,
            "python_seeded_float_absolute_tolerance": PYTHON_SEEDED_FLOAT_ABSOLUTE_TOLERANCE,
            "float_rule": "abs(actual-expected) <= max(class_atol, rtol*max(abs(actual),abs(expected)))",
            "integers_booleans_strings_shapes": "exact",
            "hq_ticks_and_decoded_wire_values": "exact",
            "protobuf_hex": "exact fixture integrity only; not a cross-language canonical form",
            "rng-independent": "Rust parity required; includes deterministic hash-derived angles",
            "python-seeded-reference": "Python replay reference with host-libm tolerance; Rust RNG sequence equality not required",
            "rust_random_acceptance": "same model version, configuration and seed reproduce output; distributions and event invariants remain required",
            "regeneration": "source and stored file hashes exact; rng-independent floats use the default tolerance and Python-seeded floats use their stated tolerance",
        },
        "fixtures": {
            name: {
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "comparison_class": documents[name]["comparison_class"],
            }
            for name, data in fixtures.items()
        },
        "maximum_total_bytes": MAX_FIXTURE_BYTES,
    }
    fixtures["metadata.json"] = _json_bytes(metadata)
    if sum(map(len, fixtures.values())) > MAX_FIXTURE_BYTES:
        raise RuntimeError("Rust parity fixture set exceeds its 256 KiB size limit")
    return fixtures


def _compare(
    expected: Any,
    actual: Any,
    path: str,
    *,
    abs_tol: float = FLOAT_ABSOLUTE_TOLERANCE,
    rel_tol: float = FLOAT_RELATIVE_TOLERANCE,
) -> None:
    if type(actual) is not type(expected):
        raise ValueError(f"stale Rust parity fixture at {path}: value type changed")
    if isinstance(expected, float):
        if not math.isclose(expected, actual, rel_tol=rel_tol, abs_tol=abs_tol):
            raise ValueError(f"stale Rust parity fixture at {path}: float changed")
    elif isinstance(expected, dict):
        if expected.keys() != actual.keys():
            raise ValueError(f"stale Rust parity fixture at {path}: fields changed")
        for key in expected:
            _compare(expected[key], actual[key], f"{path}.{key}", abs_tol=abs_tol, rel_tol=rel_tol)
    elif isinstance(expected, list):
        _compare_lists(expected, actual, path, abs_tol=abs_tol, rel_tol=rel_tol)
    elif expected != actual:
        raise ValueError(f"stale Rust parity fixture at {path}: value changed")


def _compare_lists(
    expected: list[Any],
    actual: list[Any],
    path: str,
    *,
    abs_tol: float,
    rel_tol: float,
) -> None:
    if len(expected) != len(actual):
        raise ValueError(f"stale Rust parity fixture at {path}: array length changed")
    for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
        _compare(left, right, f"{path}[{index}]", abs_tol=abs_tol, rel_tol=rel_tol)


def check_fixtures(directory: Path, generated: dict[str, bytes] | None = None) -> None:
    """Check provenance, file integrity and regenerated semantic values."""
    expected = build_fixtures() if generated is None else generated
    names = {path.name for path in directory.iterdir() if path.is_file()}
    if names != expected.keys():
        raise ValueError("Rust parity fixture file set is missing or unexpected")
    saved_metadata = json.loads((directory / "metadata.json").read_bytes())
    expected_metadata = json.loads(expected["metadata.json"])
    saved_total_bytes = sum((directory / name).stat().st_size for name in names)
    if saved_total_bytes > MAX_FIXTURE_BYTES:
        raise ValueError("Rust parity fixture set exceeds its 256 KiB size limit")
    saved_manifest = saved_metadata.get("fixtures")
    expected_manifest = expected_metadata["fixtures"]
    if not isinstance(saved_manifest, dict) or saved_manifest.keys() != expected_manifest.keys():
        raise ValueError("Rust parity fixture manifest is missing or unexpected")
    for name, data in expected.items():
        if name == "metadata.json":
            continue
        saved = (directory / name).read_bytes()
        info = saved_manifest[name]
        if not isinstance(info, dict) or info.keys() != expected_manifest[name].keys():
            raise ValueError(f"Rust parity fixture manifest fields mismatch: {name}")
        _compare(
            expected_metadata["fixtures"][name]["comparison_class"],
            info["comparison_class"],
            f"metadata.json.fixtures.{name}.comparison_class",
        )
        if info["sha256"] != hashlib.sha256(saved).hexdigest() or info["bytes"] != len(saved):
            raise ValueError(f"Rust parity fixture integrity mismatch: {name}")
        comparison_class = expected_metadata["fixtures"][name]["comparison_class"]
        absolute_tolerance = (
            PYTHON_SEEDED_FLOAT_ABSOLUTE_TOLERANCE
            if comparison_class == "python-seeded-reference"
            else FLOAT_ABSOLUTE_TOLERANCE
        )
        _compare(
            json.loads(data),
            json.loads(saved),
            name,
            abs_tol=absolute_tolerance,
            rel_tol=FLOAT_RELATIVE_TOLERANCE,
        )
    saved_metadata.pop("fixtures")
    expected_metadata.pop("fixtures")
    _compare(expected_metadata, saved_metadata, "metadata.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=FIXTURE_DIRECTORY)
    arguments = parser.parse_args()
    try:
        generated = build_fixtures()
        if arguments.check:
            check_fixtures(arguments.output_dir, generated)
        else:
            arguments.output_dir.mkdir(parents=True, exist_ok=True)
            for name, data in generated.items():
                (arguments.output_dir / name).write_bytes(data)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
