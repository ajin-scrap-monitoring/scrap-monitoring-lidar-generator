"""Tests for shared falling-material spatial distortions."""

import numpy as np
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
    FallingMaterialEvent,
    FallingMaterialSettings,
    ReferencePoint,
    ReferenceScan,
    ScheduledScan,
    SpatialDistortionTimeline,
    TimedReferenceScan,
    resolve_falling_material_distances,
)
from scrap_monitoring_lidar_generator.scenario import (
    HeightField,
    ScenarioSettings,
    ScenarioSimulator,
)


def _boundary() -> Polygon2:
    return Polygon2((Vec2(-2.0, -2.0), Vec2(2.0, -2.0), Vec2(2.0, 2.0), Vec2(-2.0, 2.0)))


def _scenario(
    *,
    mean_fill_duration_s: float = 2.0,
    fill_rate_factor_range: tuple[float, float] = (1.0, 1.0),
    fill_rate_change_duration_s_range: tuple[float, float] = (0.25, 0.5),
    seed: int = 123,
) -> ScenarioSimulator:
    surface = HeightField(_boundary(), floor_z_m=0.0, top_z_m=2.0, cell_size_m=0.2)
    settings = ScenarioSettings(
        mean_fill_duration_s=mean_fill_duration_s,
        fill_duration_factor_range=(1.0, 1.0),
        fill_rate_factor_range=fill_rate_factor_range,
        fill_rate_change_duration_s_range=fill_rate_change_duration_s_range,
        collection_threshold_range=(0.5, 0.5),
        collection_duration_factor_range=(0.5, 0.5),
        collection_rate_factor_range=(1.0, 1.0),
        collection_rate_change_duration_s_range=(0.25, 0.5),
        inlet_positions=(Vec2(0.0, 0.0),),
        inlet_switch_activation_ratio=0.5,
        inlet_switch_height_difference_m=0.1,
        inlet_comparison_radius_m=0.2,
        surface_update_interval_s=0.5,
        pile_spread_radius_m=0.5,
        roughness_height_range_m=(0.0, 0.0),
        roughness_radius_range_m=(0.2, 0.3),
    )
    return ScenarioSimulator(surface, settings, seed=seed)


def _timeline(*, seed: int = 456, event_rate_per_s: float = 20.0) -> SpatialDistortionTimeline:
    return SpatialDistortionTimeline(
        boundary=_boundary(),
        static_scene=EnvironmentScene(_boundary(), floor_z_m=0.0, top_z_m=2.0),
        falling_material=FallingMaterialSettings(
            event_rate_per_s=event_rate_per_s,
            radius_m_range=(0.1, 0.3),
            duration_s_range=(0.05, 0.2),
            distance_reduction_m_range=(0.5, 1.0),
            inlet_positions=(Vec2(0.0, 0.0),),
            placement_radius_m=0.5,
        ),
        seed=seed,
    )


def _advance(
    timeline: SpatialDistortionTimeline,
    scenario: ScenarioSimulator,
    elapsed_s: float,
) -> None:
    while scenario.elapsed_s < elapsed_s:
        interval_end_s = min(elapsed_s, scenario.next_surface_event_elapsed_s)
        timeline.advance_to(interval_end_s, scenario=scenario)
        scenario.advance_to(interval_end_s)


def _reference(
    *,
    hit_kinds: tuple[HitKind | None, ...] = (
        HitKind.SURFACE,
        HitKind.SURFACE,
        HitKind.WALL,
        HitKind.SURFACE,
    ),
) -> TimedReferenceScan:
    point_times_s = np.array([0.0, 0.25, 0.5, 0.75], dtype=np.float64)
    angles_deg = np.zeros(4, dtype=np.float64)
    schedule = ScheduledScan(
        sensor_id="sensor-a",
        scan_id=1,
        rotation_started_at_s=0.0,
        completed_at_s=1.0,
        angles_deg=angles_deg,
        point_elapsed_times_s=point_times_s,
    )
    return TimedReferenceScan(
        schedule=schedule,
        scan=ReferenceScan(
            sensor_id="sensor-a",
            points=tuple(
                ReferencePoint(
                    angle_deg=0.0,
                    distance_m=5.0 if hit_kind is not None else 0.0,
                    hit_kind=hit_kind,
                )
                for hit_kind in hit_kinds
            ),
        ),
    )


def _downward_frame() -> SensorFrame:
    return SensorFrame(
        origin_m=Vec3(0.0, 0.0, 5.0),
        u0=Vec3(0.0, 0.0, -1.0),
        u90=Vec3(1.0, 0.0, 0.0),
    )


def test_timeline_generates_only_during_filling_and_prunes_completed_history() -> None:
    timeline = _timeline()
    scenario = _scenario()

    _advance(timeline, scenario, 2.0)
    filling_events = timeline.falling_events
    _advance(timeline, scenario, 2.75)

    assert filling_events
    assert timeline.falling_events == filling_events
    assert all(0.0 <= event.started_at_s < 2.0 for event in filling_events)
    assert all(event.started_at_s < event.ends_at_s <= 2.0 for event in filling_events)
    assert all(_boundary().contains(event.center) for event in filling_events)

    timeline.discard_before(2.0)

    assert timeline.falling_events == ()


def test_timeline_is_independent_of_observation_chunk_boundaries() -> None:
    first_timeline = _timeline(seed=789)
    first_scenario = _scenario()
    second_timeline = _timeline(seed=789)
    second_scenario = _scenario()

    _advance(first_timeline, first_scenario, 2.0)
    for elapsed_s in np.arange(0.125, 2.001, 0.125):
        _advance(second_timeline, second_scenario, float(elapsed_s))

    assert first_timeline.falling_events == second_timeline.falling_events


def test_zero_event_rate_produces_no_events() -> None:
    timeline = _timeline(event_rate_per_s=0.0)
    scenario = _scenario()

    _advance(timeline, scenario, 2.0)

    assert timeline.falling_events == ()


def test_event_density_follows_instantaneous_fill_rate() -> None:
    scenario = _scenario(
        mean_fill_duration_s=20.0,
        fill_rate_factor_range=(0.2, 1.8),
        fill_rate_change_duration_s_range=(2.0, 3.0),
        seed=321,
    )
    timeline = _timeline(seed=654, event_rate_per_s=200.0)
    plan = scenario.phase_plan

    _advance(timeline, scenario, plan.ends_at_s)

    event_factors = np.array(
        [
            plan.rate_profile.factor_at(event.started_at_s - plan.started_at_s)
            for event in timeline.falling_events
        ]
    )
    time_samples_s = np.linspace(0.0, plan.duration_s, 10_001)
    time_factors = np.array(
        [plan.rate_profile.factor_at(float(elapsed_s)) for elapsed_s in time_samples_s]
    )
    assert event_factors.size > 3_000
    assert float(np.mean(event_factors)) > float(np.mean(time_factors)) + 0.02


def test_resolver_uses_active_surface_region_and_nearest_candidate() -> None:
    reference = _reference()
    events = (
        FallingMaterialEvent(
            cycle_index=0,
            started_at_s=0.2,
            ends_at_s=0.75,
            center=Vec2(0.0, 0.0),
            radius_m=0.2,
            distance_reduction_m=0.5,
        ),
        FallingMaterialEvent(
            cycle_index=0,
            started_at_s=0.2,
            ends_at_s=0.75,
            center=Vec2(0.0, 0.0),
            radius_m=0.2,
            distance_reduction_m=1.0,
        ),
        FallingMaterialEvent(
            cycle_index=0,
            started_at_s=0.0,
            ends_at_s=1.0,
            center=Vec2(1.0, 0.0),
            radius_m=0.1,
            distance_reduction_m=2.0,
        ),
    )

    distances_m = resolve_falling_material_distances(
        reference,
        frame=_downward_frame(),
        events=events,
        min_distance_m=1.0,
    )

    assert distances_m.tolist() == [5.0, 4.0, 5.0, 5.0]


def test_resolver_skips_reduction_beyond_minimum_measurement_distance() -> None:
    event = FallingMaterialEvent(
        cycle_index=0,
        started_at_s=0.0,
        ends_at_s=1.0,
        center=Vec2(0.0, 0.0),
        radius_m=0.2,
        distance_reduction_m=4.5,
    )

    distances_m = resolve_falling_material_distances(
        _reference(),
        frame=_downward_frame(),
        events=(event,),
        min_distance_m=1.0,
    )

    assert distances_m.tolist() == [5.0, 5.0, 5.0, 5.0]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"event_rate_per_s": -1.0}, "event rate"),
        ({"radius_m_range": (0.0, 1.0)}, "radius"),
        ({"duration_s_range": (2.0, 1.0)}, "ordered"),
        ({"distance_reduction_m_range": (1.0, float("inf"))}, "distance reduction"),
        ({"inlet_positions": ()}, "inlet"),
        ({"placement_radius_m": 0.0}, "placement radius"),
    ],
)
def test_rejects_invalid_falling_material_settings(
    overrides: dict[str, object],
    message: str,
) -> None:
    arguments: dict[str, object] = {
        "event_rate_per_s": 1.0,
        "radius_m_range": (0.1, 0.2),
        "duration_s_range": (0.1, 0.2),
        "distance_reduction_m_range": (0.5, 1.0),
        "inlet_positions": (Vec2(0.0, 0.0),),
        "placement_radius_m": 0.5,
    }
    arguments.update(overrides)

    with pytest.raises(ValueError, match=message):
        FallingMaterialSettings(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"cycle_index": 1.5}, "cycle index"),
        ({"started_at_s": -1.0}, "start"),
        ({"ends_at_s": 0.0}, "end"),
        ({"radius_m": 0.0}, "radius"),
        ({"distance_reduction_m": float("nan")}, "distance reduction"),
    ],
)
def test_rejects_invalid_falling_material_event(
    overrides: dict[str, object],
    message: str,
) -> None:
    arguments: dict[str, object] = {
        "cycle_index": 0,
        "started_at_s": 0.0,
        "ends_at_s": 1.0,
        "center": Vec2(0.0, 0.0),
        "radius_m": 0.2,
        "distance_reduction_m": 0.5,
    }
    arguments.update(overrides)

    with pytest.raises(ValueError, match=message):
        FallingMaterialEvent(**arguments)  # type: ignore[arg-type]
