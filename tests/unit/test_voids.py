"""Tests for persistent surface void distortions."""

import math

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
    ReferencePoint,
    ReferenceScan,
    ScheduledScan,
    TimedReferenceScan,
)
from scrap_monitoring_lidar_generator.measurement.spatial import (
    SpatialDistortionTimeline,
    VoidEvent,
    VoidSettings,
    resolve_void_distances,
)
from scrap_monitoring_lidar_generator.scenario import (
    HeightField,
    ScenarioSettings,
    ScenarioSimulator,
)


def _boundary() -> Polygon2:
    return Polygon2((Vec2(-2.0, -2.0), Vec2(2.0, -2.0), Vec2(2.0, 2.0), Vec2(-2.0, 2.0)))


def _static_scene() -> EnvironmentScene:
    return EnvironmentScene(_boundary(), floor_z_m=0.0, top_z_m=6.0)


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


def _timeline(
    *,
    duration_s_range: tuple[float, float] = (0.5, 0.5),
    cover_height_increase_m: float = 10.0,
    surface_area_ratio: float = 0.005,
) -> SpatialDistortionTimeline:
    return SpatialDistortionTimeline(
        boundary=_boundary(),
        static_scene=_static_scene(),
        falling_material=None,
        voids=VoidSettings(
            surface_area_ratio=surface_area_ratio,
            radius_m_range=(0.1, 0.1),
            duration_s_range=duration_s_range,
            cover_height_increase_m=cover_height_increase_m,
            distance_increase_m_range=(0.5, 0.5),
        ),
        seed=456,
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
    hit_kinds = (HitKind.SURFACE, HitKind.SURFACE, HitKind.WALL, HitKind.SURFACE)
    return TimedReferenceScan(
        schedule=schedule,
        scan=ReferenceScan(
            sensor_id="sensor-a",
            points=tuple(
                ReferencePoint(angle_deg=0.0, distance_m=3.0, hit_kind=hit_kind)
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


def test_voids_start_only_after_material_exists_and_maintain_target_area() -> None:
    timeline = _timeline()
    scenario = _scenario()

    _advance(timeline, scenario, 0.25)
    initial_events = timeline.void_events
    assert not initial_events

    _advance(timeline, scenario, 0.75)
    active: list[VoidEvent] = []
    for void_event in timeline.void_events:
        if void_event.started_at_s <= 0.75 < void_event.ends_at_s:
            active.append(void_event)
    target_area_m2 = scenario.surface.surface_area_m2 * 0.005

    assert active
    assert sum(math.pi * event.radius_m**2 for event in active) >= target_area_m2
    assert all(
        scenario.surface.height_at(event.center) > scenario.surface.floor_z_m for event in active
    )
    assert all(_boundary().contains(event.center) for event in active)
    assert all(
        math.hypot(first.center.x - second.center.x, first.center.y - second.center.y)
        >= first.radius_m + second.radius_m
        for index, first in enumerate(active)
        for second in active[index + 1 :]
    )


def test_expired_voids_are_replaced_during_filling() -> None:
    timeline = _timeline(duration_s_range=(0.25, 0.25))
    scenario = _scenario()

    _advance(timeline, scenario, 1.26)

    assert any(event.started_at_s == pytest.approx(0.5) for event in timeline.void_events)
    assert any(event.started_at_s == pytest.approx(0.75) for event in timeline.void_events)
    assert any(event.started_at_s == pytest.approx(1.0) for event in timeline.void_events)
    assert any(event.started_at_s == pytest.approx(1.25) for event in timeline.void_events)


def test_surface_rise_ends_void_and_creates_replacement() -> None:
    timeline = _timeline(
        duration_s_range=(10.0, 10.0),
        cover_height_increase_m=1e-6,
    )
    scenario = _scenario()

    _advance(timeline, scenario, 0.75)
    original_events = tuple(timeline.void_events)
    _advance(timeline, scenario, 1.01)

    assert original_events
    assert any(event.started_at_s == pytest.approx(1.0) for event in timeline.void_events)
    assert any(
        event.started_at_s == pytest.approx(0.5) and event.ends_at_s == pytest.approx(1.0)
        for event in timeline.void_events
    )


def test_collection_phase_has_no_active_voids() -> None:
    timeline = _timeline(duration_s_range=(10.0, 10.0))
    scenario = _scenario()

    _advance(timeline, scenario, 2.25)

    assert timeline.void_events
    assert all(not (event.started_at_s <= 2.25 < event.ends_at_s) for event in timeline.void_events)
    assert all(event.started_at_s < 2.0 for event in timeline.void_events)


def test_void_resolver_uses_nearest_inner_reflection_and_half_open_lifetime() -> None:
    events = (
        VoidEvent(
            cycle_index=0,
            started_at_s=0.2,
            expires_at_s=0.75,
            ends_at_s=0.75,
            center=Vec2(0.0, 0.0),
            surface_height_at_start_m=2.0,
            radius_m=0.2,
            distance_increase_m=1.0,
        ),
        VoidEvent(
            cycle_index=0,
            started_at_s=0.2,
            expires_at_s=0.75,
            ends_at_s=0.75,
            center=Vec2(0.0, 0.0),
            surface_height_at_start_m=2.0,
            radius_m=0.2,
            distance_increase_m=0.5,
        ),
    )

    distances_m = resolve_void_distances(
        _reference(),
        frame=_downward_frame(),
        events=events,
        min_distance_m=1.0,
        max_distance_m=10.0,
        static_scene=_static_scene(),
    )

    assert distances_m.tolist() == [3.0, 3.5, 3.0, 3.0]


@pytest.mark.parametrize("maximum_distance_m", [3.75, 10.0])
def test_void_resolver_skips_candidate_beyond_measurement_or_static_surface(
    maximum_distance_m: float,
) -> None:
    increase_m = 1.0 if maximum_distance_m == 3.75 else 3.0
    event = VoidEvent(
        cycle_index=0,
        started_at_s=0.0,
        expires_at_s=1.0,
        ends_at_s=1.0,
        center=Vec2(0.0, 0.0),
        surface_height_at_start_m=2.0,
        radius_m=0.2,
        distance_increase_m=increase_m,
    )

    distances_m = resolve_void_distances(
        _reference(),
        frame=_downward_frame(),
        events=(event,),
        min_distance_m=1.0,
        max_distance_m=maximum_distance_m,
        static_scene=_static_scene(),
    )

    assert distances_m.tolist() == [3.0, 3.0, 3.0, 3.0]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"surface_area_ratio": 1.1}, "area ratio"),
        ({"radius_m_range": (0.0, 1.0)}, "radius"),
        ({"duration_s_range": (2.0, 1.0)}, "ordered"),
        ({"cover_height_increase_m": -1.0}, "cover height"),
        ({"distance_increase_m_range": (1.0, float("inf"))}, "distance increase"),
    ],
)
def test_rejects_invalid_void_settings(overrides: dict[str, object], message: str) -> None:
    arguments: dict[str, object] = {
        "surface_area_ratio": 0.1,
        "radius_m_range": (0.1, 0.2),
        "duration_s_range": (1.0, 2.0),
        "cover_height_increase_m": 0.1,
        "distance_increase_m_range": (0.2, 0.5),
    }
    arguments.update(overrides)

    with pytest.raises(ValueError, match=message):
        VoidSettings(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"cycle_index": 1.5}, "cycle index"),
        ({"started_at_s": -1.0}, "start"),
        ({"expires_at_s": 0.0}, "expiry"),
        ({"ends_at_s": 2.0}, "end"),
        ({"surface_height_at_start_m": float("nan")}, "surface height"),
        ({"radius_m": 0.0}, "radius"),
        ({"distance_increase_m": 0.0}, "distance increase"),
    ],
)
def test_rejects_invalid_void_event(overrides: dict[str, object], message: str) -> None:
    arguments: dict[str, object] = {
        "cycle_index": 0,
        "started_at_s": 0.0,
        "expires_at_s": 1.0,
        "ends_at_s": 1.0,
        "center": Vec2(0.0, 0.0),
        "surface_height_at_start_m": 1.0,
        "radius_m": 0.2,
        "distance_increase_m": 0.5,
    }
    arguments.update(overrides)

    with pytest.raises(ValueError, match=message):
        VoidEvent(**arguments)  # type: ignore[arg-type]
