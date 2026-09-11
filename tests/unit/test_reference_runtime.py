"""Tests for simulation-time reference scan coordination."""

from dataclasses import dataclass, field

import pytest

from scrap_monitoring_lidar_generator.geometry import (
    EnvironmentScene,
    HitKind,
    Polygon2,
    SensorFrame,
    Vec2,
    Vec3,
)
from scrap_monitoring_lidar_generator.measurement import (
    ReferenceScanner,
    SensorRotationScheduler,
)
from scrap_monitoring_lidar_generator.runtime import ReferenceGenerationRuntime
from scrap_monitoring_lidar_generator.scenario import (
    HeightField,
    ScenarioSettings,
    ScenarioSimulator,
)


def _scenario() -> ScenarioSimulator:
    boundary = Polygon2((Vec2(0.0, 0.0), Vec2(2.0, 0.0), Vec2(2.0, 2.0), Vec2(0.0, 2.0)))
    surface = HeightField(boundary, floor_z_m=0.0, top_z_m=2.0, cell_size_m=0.1)
    settings = ScenarioSettings(
        mean_fill_duration_s=10.0,
        fill_duration_factor_range=(1.0, 1.0),
        fill_rate_factor_range=(1.0, 1.0),
        fill_rate_change_duration_s_range=(1.0, 2.0),
        collection_threshold_range=(0.5, 0.5),
        collection_duration_factor_range=(0.2, 0.2),
        collection_rate_factor_range=(1.0, 1.0),
        collection_rate_change_duration_s_range=(0.25, 0.5),
        inlet_positions=(Vec2(1.0, 1.0),),
        inlet_switch_activation_ratio=0.5,
        inlet_switch_height_difference_m=0.1,
        inlet_comparison_radius_m=0.2,
        surface_update_interval_s=0.5,
        pile_spread_radius_m=0.5,
        roughness_height_range_m=(0.0, 0.0),
        roughness_radius_range_m=(0.2, 0.3),
    )
    return ScenarioSimulator(surface, settings, seed=123)


def _scene(scenario: ScenarioSimulator) -> EnvironmentScene:
    return EnvironmentScene(
        scenario.surface.boundary,
        floor_z_m=scenario.surface.floor_z_m,
        top_z_m=scenario.surface.top_z_m,
        dynamic_surface=scenario.surface,
    )


def _scanner(sensor_id: str) -> ReferenceScanner:
    return ReferenceScanner(
        sensor_id=sensor_id,
        frame=SensorFrame(
            origin_m=Vec3(1.0, 1.0, 1.5),
            u0=Vec3(0.0, 0.0, -1.0),
            u90=Vec3(1.0, 0.0, 0.0),
        ),
    )


def _scheduler(
    sensor_id: str,
    *,
    rotation_rate_hz: float = 1.0,
    initial_angle_deg: float = 180.0,
) -> SensorRotationScheduler:
    return SensorRotationScheduler(
        sensor_id=sensor_id,
        sample_rate_hz=4.0,
        rotation_rate_hz=rotation_rate_hz,
        initial_angle_deg=initial_angle_deg,
    )


def test_surface_event_precedes_measurement_at_the_same_time() -> None:
    scenario = _scenario()
    runtime = ReferenceGenerationRuntime(
        scenario=scenario,
        scene=_scene(scenario),
        scanners=(_scanner("sensor-a"),),
        schedulers=(_scheduler("sensor-a"),),
    )

    (result,) = runtime.next_completed_scans()

    assert result.sensor_id == "sensor-a"
    assert result.scan_id == 1
    assert result.captured_elapsed_s == 0.0
    assert result.completed_at_s == 1.0
    assert [point.angle_deg for point in result.scan.points] == [180.0, 270.0, 0.0, 90.0]
    assert result.scan.points[2].hit_kind is HitKind.SURFACE
    assert 0.0 < result.scan.points[2].distance_m < 1.5
    assert runtime.scenario.elapsed_s == 1.0


def test_returns_only_next_completions_in_sensor_id_order() -> None:
    scenario = _scenario()
    runtime = ReferenceGenerationRuntime(
        scenario=scenario,
        scene=_scene(scenario),
        scanners=(_scanner("sensor-b"), _scanner("sensor-a")),
        schedulers=(
            _scheduler("sensor-b", rotation_rate_hz=1.0),
            _scheduler("sensor-a", rotation_rate_hz=2.0),
        ),
    )

    first = runtime.next_completed_scans()
    second = runtime.next_completed_scans()

    assert [(scan.sensor_id, scan.scan_id) for scan in first] == [("sensor-a", 1)]
    assert [(scan.sensor_id, scan.scan_id) for scan in second] == [
        ("sensor-a", 2),
        ("sensor-b", 1),
    ]
    assert second[1].schedule.point_count == 4
    assert runtime.scenario.elapsed_s == 1.0


def test_same_inputs_reproduce_timed_reference_scans() -> None:
    runtimes = []
    for _ in range(2):
        scenario = _scenario()
        runtimes.append(
            ReferenceGenerationRuntime(
                scenario=scenario,
                scene=_scene(scenario),
                scanners=(_scanner("sensor-a"),),
                schedulers=(_scheduler("sensor-a", initial_angle_deg=37.0),),
            )
        )

    first = runtimes[0].next_completed_scans()[0]
    second = runtimes[1].next_completed_scans()[0]

    assert first.schedule.scan_id == second.schedule.scan_id
    assert first.schedule.completed_at_s == second.schedule.completed_at_s
    assert first.scan == second.scan


def test_notifies_observer_before_each_scenario_interval() -> None:
    @dataclass
    class RecordingObserver:
        intervals: list[tuple[float, float]] = field(default_factory=list)

        def advance_to(self, elapsed_s: float, *, scenario: ScenarioSimulator) -> None:
            self.intervals.append((scenario.elapsed_s, elapsed_s))

    observer = RecordingObserver()
    scenario = _scenario()
    runtime = ReferenceGenerationRuntime(
        scenario=scenario,
        scene=_scene(scenario),
        scanners=(_scanner("sensor-a"),),
        schedulers=(_scheduler("sensor-a"),),
        observers=(observer,),
    )

    runtime.next_completed_scans()

    assert observer.intervals == [(0.0, 0.5), (0.5, 1.0)]


def test_rejects_mismatched_or_preadvanced_inputs() -> None:
    scenario = _scenario()
    scene = _scene(scenario)
    with pytest.raises(ValueError, match="sensor sets"):
        ReferenceGenerationRuntime(
            scenario=scenario,
            scene=scene,
            scanners=(_scanner("sensor-a"),),
            schedulers=(_scheduler("sensor-b"),),
        )

    scheduler = _scheduler("sensor-a")
    scheduler.next_scan()
    with pytest.raises(ValueError, match="scan_id 1"):
        ReferenceGenerationRuntime(
            scenario=scenario,
            scene=scene,
            scanners=(_scanner("sensor-a"),),
            schedulers=(scheduler,),
        )
