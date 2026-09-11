"""Tests for moving collection-period occlusions."""

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
    CollectionOcclusionEvent,
    CollectionOcclusionSettings,
    ReferencePoint,
    ReferenceScan,
    ScheduledScan,
    SpatialDistortionTimeline,
    TimedReferenceScan,
    resolve_collection_occlusion_distances,
)
from scrap_monitoring_lidar_generator.scenario import (
    HeightField,
    ScenarioSettings,
    ScenarioSimulator,
)


def _boundary() -> Polygon2:
    return Polygon2((Vec2(-2.0, -2.0), Vec2(2.0, -2.0), Vec2(2.0, 2.0), Vec2(-2.0, 2.0)))


def _scenario() -> ScenarioSimulator:
    surface = HeightField(_boundary(), floor_z_m=0.0, top_z_m=2.0, cell_size_m=0.1)
    settings = ScenarioSettings(
        mean_fill_duration_s=2.0,
        fill_duration_factor_range=(1.0, 1.0),
        fill_rate_factor_range=(1.0, 1.0),
        fill_rate_change_duration_s_range=(0.25, 0.5),
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
    return ScenarioSimulator(surface, settings, seed=123)


def _timeline(*, seed: int = 456) -> SpatialDistortionTimeline:
    boundary = _boundary()
    return SpatialDistortionTimeline(
        boundary=boundary,
        static_scene=EnvironmentScene(boundary, floor_z_m=0.0, top_z_m=2.0),
        falling_material=None,
        collection_occlusion=CollectionOcclusionSettings(
            event_interval_s_range=(0.1, 0.1),
            radius_m_range=(0.1, 0.2),
            duration_s_range=(0.2, 0.3),
            distance_reduction_m_range=(0.5, 1.0),
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


def _reference() -> TimedReferenceScan:
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
                ReferencePoint(angle_deg=0.0, distance_m=5.0, hit_kind=HitKind.SURFACE)
                for _ in point_times_s
            ),
        ),
    )


def _downward_frame() -> SensorFrame:
    return SensorFrame(
        origin_m=Vec3(0.0, 0.0, 5.0),
        u0=Vec3(0.0, 0.0, -1.0),
        u90=Vec3(1.0, 0.0, 0.0),
    )


def test_timeline_generates_moving_events_only_during_collection() -> None:
    timeline = _timeline()
    scenario = _scenario()

    _advance(timeline, scenario, 2.0)
    before_collection = timeline.collection_events
    _advance(timeline, scenario, 2.75)
    events = timeline.collection_events

    assert not before_collection
    assert events
    assert all(2.0 < event.started_at_s < 2.75 for event in events)
    assert all(event.started_at_s < event.ends_at_s <= 3.0 for event in events)
    assert all(
        _boundary().contains(event.center_at(float(elapsed_s)))
        for event in events
        for elapsed_s in np.linspace(event.started_at_s, event.ends_at_s, 21)
    )

    _advance(timeline, scenario, 3.25)

    assert all(event.started_at_s < 3.0 for event in timeline.collection_events)


def test_timeline_schedule_is_independent_of_observation_chunks() -> None:
    first_timeline = _timeline(seed=789)
    first_scenario = _scenario()
    second_timeline = _timeline(seed=789)
    second_scenario = _scenario()

    _advance(first_timeline, first_scenario, 3.0)
    for elapsed_s in np.arange(0.125, 3.001, 0.125):
        _advance(second_timeline, second_scenario, float(elapsed_s))

    assert first_timeline.collection_events == second_timeline.collection_events


def test_moving_event_center_interpolates_over_its_lifetime() -> None:
    event = CollectionOcclusionEvent(
        cycle_index=0,
        started_at_s=2.0,
        ends_at_s=4.0,
        start_center=Vec2(-1.0, 0.0),
        end_center=Vec2(1.0, 2.0),
        radius_m=0.2,
        distance_reduction_m=0.5,
    )

    assert event.center_at(2.0) == Vec2(-1.0, 0.0)
    assert event.center_at(3.0) == Vec2(0.0, 1.0)
    assert event.center_at(4.0) == Vec2(1.0, 2.0)
    with pytest.raises(ValueError, match="within"):
        event.center_at(4.1)


def test_resolver_uses_point_time_moving_center_and_nearest_candidate() -> None:
    events = (
        CollectionOcclusionEvent(
            cycle_index=0,
            started_at_s=0.0,
            ends_at_s=1.0,
            start_center=Vec2(-0.5, 0.0),
            end_center=Vec2(0.5, 0.0),
            radius_m=0.1,
            distance_reduction_m=0.5,
        ),
        CollectionOcclusionEvent(
            cycle_index=0,
            started_at_s=0.0,
            ends_at_s=1.0,
            start_center=Vec2(-0.5, 0.0),
            end_center=Vec2(0.5, 0.0),
            radius_m=0.1,
            distance_reduction_m=1.0,
        ),
    )

    distances_m = resolve_collection_occlusion_distances(
        _reference(),
        frame=_downward_frame(),
        events=events,
        min_distance_m=1.0,
    )

    assert distances_m.tolist() == [5.0, 5.0, 4.0, 5.0]


def test_resolver_skips_reduction_beyond_minimum_measurement_distance() -> None:
    event = CollectionOcclusionEvent(
        cycle_index=0,
        started_at_s=0.0,
        ends_at_s=1.0,
        start_center=Vec2(0.0, 0.0),
        end_center=Vec2(0.0, 0.0),
        radius_m=0.2,
        distance_reduction_m=4.5,
    )

    distances_m = resolve_collection_occlusion_distances(
        _reference(),
        frame=_downward_frame(),
        events=(event,),
        min_distance_m=1.0,
    )

    assert distances_m.tolist() == [5.0, 5.0, 5.0, 5.0]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"event_interval_s_range": (0.0, 1.0)}, "event interval"),
        ({"radius_m_range": (2.0, 1.0)}, "ordered"),
        ({"duration_s_range": (1.0, float("inf"))}, "duration"),
        ({"distance_reduction_m_range": (-1.0, 1.0)}, "distance reduction"),
    ],
)
def test_rejects_invalid_collection_settings(
    overrides: dict[str, object],
    message: str,
) -> None:
    arguments: dict[str, object] = {
        "event_interval_s_range": (1.0, 2.0),
        "radius_m_range": (0.1, 0.2),
        "duration_s_range": (0.5, 1.0),
        "distance_reduction_m_range": (0.5, 1.0),
    }
    arguments.update(overrides)

    with pytest.raises(ValueError, match=message):
        CollectionOcclusionSettings(**arguments)  # type: ignore[arg-type]


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
def test_rejects_invalid_collection_event(
    overrides: dict[str, object],
    message: str,
) -> None:
    arguments: dict[str, object] = {
        "cycle_index": 0,
        "started_at_s": 0.0,
        "ends_at_s": 1.0,
        "start_center": Vec2(0.0, 0.0),
        "end_center": Vec2(1.0, 1.0),
        "radius_m": 0.2,
        "distance_reduction_m": 0.5,
    }
    arguments.update(overrides)

    with pytest.raises(ValueError, match=message):
        CollectionOcclusionEvent(**arguments)  # type: ignore[arg-type]
