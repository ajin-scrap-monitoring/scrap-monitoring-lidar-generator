"""Scenario runtime assembly from validated generator inputs."""

from scrap_monitoring_lidar_generator.configuration import GeneratorInputs
from scrap_monitoring_lidar_generator.geometry import Polygon2, Vec2
from scrap_monitoring_lidar_generator.scenario import (
    HeightField,
    ScenarioSettings,
    ScenarioSimulator,
)


def build_scenario_simulator(inputs: GeneratorInputs) -> ScenarioSimulator:
    """Build an empty deterministic scenario from validated inputs."""
    environment = inputs.environment
    config = inputs.generator.scenario
    boundary = Polygon2(tuple(Vec2(x, y) for x, y in environment.boundary_xy_m))
    surface = HeightField(
        boundary,
        floor_z_m=environment.floor_z_m,
        top_z_m=environment.top_z_m,
        cell_size_m=config.surface.cell_size_m,
    )
    settings = ScenarioSettings(
        mean_fill_duration_s=config.mean_fill_duration_s,
        fill_duration_factor_range=config.fill_duration_factor_range,
        fill_rate_factor_range=config.fill_rate_factor_range,
        fill_rate_change_duration_s_range=config.fill_rate_change_duration_s_range,
        collection_threshold_range=config.collection_threshold_range,
        collection_duration_factor_range=config.collection_duration_factor_range,
        collection_rate_factor_range=config.collection_rate_factor_range,
        collection_rate_change_duration_s_range=config.collection_rate_change_duration_s_range,
        inlet_positions=tuple(Vec2(x, y) for x, y in config.inlet_positions_xy_m),
        inlet_switch_activation_ratio=config.inlet_switch_activation_ratio,
        inlet_switch_height_difference_m=config.inlet_switch_height_difference_m,
        inlet_comparison_radius_m=config.inlet_comparison_radius_m,
        surface_update_interval_s=config.surface.update_interval_s,
        pile_spread_radius_m=config.surface.pile_spread_radius_m,
    )
    return ScenarioSimulator(surface, settings, seed=inputs.generator.seed)
