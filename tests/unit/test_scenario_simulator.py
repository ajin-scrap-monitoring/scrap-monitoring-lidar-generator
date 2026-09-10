"""Tests for deterministic fill and collection state transitions."""

import numpy as np
import pytest

from scrap_monitoring_lidar_generator.geometry import Polygon2, Vec2
from scrap_monitoring_lidar_generator.scenario import (
    CollectionPlan,
    FillPlan,
    HeightField,
    ScenarioPhase,
    ScenarioSettings,
    ScenarioSimulator,
)


def _surface() -> HeightField:
    return HeightField(
        Polygon2((Vec2(0.0, 0.0), Vec2(2.0, 0.0), Vec2(2.0, 1.0), Vec2(0.0, 1.0))),
        floor_z_m=0.0,
        top_z_m=1.0,
        cell_size_m=0.1,
    )


def _settings(
    *,
    mean_fill_duration_s: float = 10.0,
    fill_rate_change_duration_s_range: tuple[float, float] = (0.5, 1.0),
    surface_update_interval_s: float = 0.25,
    inlet_switch_activation_ratio: float = 0.25,
    inlet_switch_height_difference_m: float = 0.1,
    pile_spread_radius_m: float = 0.3,
) -> ScenarioSettings:
    return ScenarioSettings(
        mean_fill_duration_s=mean_fill_duration_s,
        fill_duration_factor_range=(1.0, 1.0),
        fill_rate_factor_range=(0.5, 1.5),
        fill_rate_change_duration_s_range=fill_rate_change_duration_s_range,
        collection_threshold_range=(0.5, 0.5),
        collection_duration_factor_range=(0.2, 0.2),
        collection_rate_factor_range=(0.4, 1.6),
        collection_rate_change_duration_s_range=(0.25, 0.5),
        inlet_positions=(Vec2(0.4, 0.5), Vec2(1.6, 0.5)),
        inlet_switch_activation_ratio=inlet_switch_activation_ratio,
        inlet_switch_height_difference_m=inlet_switch_height_difference_m,
        inlet_comparison_radius_m=0.2,
        surface_update_interval_s=surface_update_interval_s,
        pile_spread_radius_m=pile_spread_radius_m,
    )


def test_fill_reaches_threshold_then_collection_empties_at_exact_targets() -> None:
    simulator = ScenarioSimulator(_surface(), _settings(), seed=123)
    fill_plan = simulator.phase_plan
    assert isinstance(fill_plan, FillPlan)

    before_threshold = simulator.advance_to(fill_plan.ends_at_s - 0.001)
    at_threshold = simulator.advance_to(fill_plan.ends_at_s)
    collection_plan = simulator.phase_plan

    assert before_threshold.phase is ScenarioPhase.FILLING
    assert before_threshold.surface_fill_ratio < fill_plan.target_fill_ratio
    assert at_threshold.phase is ScenarioPhase.COLLECTING
    assert at_threshold.surface_fill_ratio == pytest.approx(fill_plan.target_fill_ratio)
    assert isinstance(collection_plan, CollectionPlan)

    during_collection = simulator.advance_to(
        collection_plan.started_at_s + collection_plan.duration_s / 2.0
    )
    next_cycle = simulator.advance_to(collection_plan.ends_at_s)

    assert during_collection.phase is ScenarioPhase.COLLECTING
    assert 0.0 < during_collection.surface_volume_m3 < collection_plan.starting_volume_m3
    assert next_cycle.phase is ScenarioPhase.FILLING
    assert next_cycle.cycle_index == 1
    assert next_cycle.surface_volume_m3 == pytest.approx(0.0, abs=1e-12)
    assert np.all(simulator.surface.heights_m == simulator.surface.floor_z_m)


def test_phase_boundary_applies_partial_interval_outside_update_cadence() -> None:
    settings = _settings(mean_fill_duration_s=10.3, surface_update_interval_s=0.7)
    simulator = ScenarioSimulator(_surface(), settings, seed=456)
    fill_plan = simulator.phase_plan
    assert isinstance(fill_plan, FillPlan)

    snapshot = simulator.advance_to(fill_plan.ends_at_s)

    assert snapshot.phase is ScenarioPhase.COLLECTING
    assert snapshot.surface_updated_at_s == fill_plan.ends_at_s
    assert snapshot.surface_volume_m3 == pytest.approx(fill_plan.target_volume_m3)


def test_large_time_jump_processes_every_cycle_boundary() -> None:
    simulator = ScenarioSimulator(_surface(), _settings(), seed=789)

    snapshot = simulator.advance_to(36.0)

    assert snapshot.phase is ScenarioPhase.FILLING
    assert snapshot.cycle_index == 3
    assert snapshot.phase_started_at_s == pytest.approx(36.0)
    assert snapshot.surface_volume_m3 == pytest.approx(0.0, abs=1e-12)


def test_same_seed_and_times_produce_identical_states() -> None:
    simulators = [ScenarioSimulator(_surface(), _settings(), seed=2026) for _ in range(2)]

    for elapsed_s in (0.25, 2.75, 9.9, 10.0, 10.75, 12.0, 14.5):
        snapshots = [simulator.advance_to(elapsed_s) for simulator in simulators]
        assert snapshots[0] == snapshots[1]
        np.testing.assert_array_equal(
            simulators[0].surface.heights_m,
            simulators[1].surface.heights_m,
        )


def test_rate_profile_random_consumption_does_not_change_cycle_targets() -> None:
    first = ScenarioSimulator(
        _surface(),
        _settings(fill_rate_change_duration_s_range=(0.25, 0.5)),
        seed=55,
    )
    second = ScenarioSimulator(
        _surface(),
        _settings(fill_rate_change_duration_s_range=(2.0, 3.0)),
        seed=55,
    )

    first_fill = first.phase_plan
    second_fill = second.phase_plan
    assert isinstance(first_fill, FillPlan)
    assert isinstance(second_fill, FillPlan)
    assert first_fill.duration_s == second_fill.duration_s
    assert first_fill.target_fill_ratio == second_fill.target_fill_ratio

    first.advance_to(first_fill.ends_at_s)
    second.advance_to(second_fill.ends_at_s)
    first_collection = first.phase_plan
    second_collection = second.phase_plan
    assert isinstance(first_collection, CollectionPlan)
    assert isinstance(second_collection, CollectionPlan)
    assert first_collection.duration_s == second_collection.duration_s

    first.advance_to(first_collection.ends_at_s)
    second.advance_to(second_collection.ends_at_s)
    next_first_fill = first.phase_plan
    next_second_fill = second.phase_plan
    assert isinstance(next_first_fill, FillPlan)
    assert isinstance(next_second_fill, FillPlan)
    assert next_first_fill.duration_s == next_second_fill.duration_s
    assert next_first_fill.target_fill_ratio == next_second_fill.target_fill_ratio


def test_inlet_switches_to_lower_comparison_area() -> None:
    simulator = ScenarioSimulator(
        _surface(),
        _settings(
            inlet_switch_activation_ratio=0.0,
            inlet_switch_height_difference_m=0.01,
            pile_spread_radius_m=0.12,
        ),
        seed=11,
    )

    snapshot = simulator.advance_to(0.25)

    assert snapshot.current_inlet_index == 1


def test_surface_changes_only_on_update_or_transition_events() -> None:
    simulator = ScenarioSimulator(
        _surface(),
        _settings(surface_update_interval_s=1.0),
        seed=22,
    )

    before_update = simulator.advance_to(0.9)
    after_update = simulator.advance_to(1.0)

    assert before_update.surface_updated_at_s == 0.0
    assert before_update.surface_volume_m3 == 0.0
    assert after_update.surface_updated_at_s == 1.0
    assert after_update.surface_volume_m3 > 0.0


def test_rejects_backward_time_and_nonempty_initial_surface() -> None:
    simulator = ScenarioSimulator(_surface(), _settings(), seed=33)
    simulator.advance_to(1.0)

    with pytest.raises(ValueError, match="monotonic"):
        simulator.advance_to(0.5)

    nonempty_surface = _surface()
    nonempty_surface.add_volume(0.1, center=Vec2(0.4, 0.5), spread_radius_m=0.2)
    with pytest.raises(ValueError, match="empty"):
        ScenarioSimulator(nonempty_surface, _settings(), seed=33)
