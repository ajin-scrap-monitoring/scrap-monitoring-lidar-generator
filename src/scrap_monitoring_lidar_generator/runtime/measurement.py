"""Measurement runtime assembly from validated generator inputs."""

from scrap_monitoring_lidar_generator.configuration import (
    GeneratorInputs,
    build_environment_scene,
    build_sensor_frame,
)
from scrap_monitoring_lidar_generator.geometry import Polygon2, Vec2
from scrap_monitoring_lidar_generator.measurement import (
    CollectionOcclusionSettings,
    FallingMaterialSettings,
    MeasurementGenerator,
    SensorDropoutScheduler,
    SensorRotationScheduler,
    SpatialDistortionTimeline,
    VoidSettings,
    create_seeded_rotation_scheduler,
)
from scrap_monitoring_lidar_generator.scenario import (
    scale_duration_range,
    scale_event_rate_per_s,
    scenario_time_scale,
)


def build_rotation_schedulers(
    inputs: GeneratorInputs,
) -> tuple[SensorRotationScheduler, ...]:
    """Build independent rotation schedulers in environment sensor order."""
    measurement = inputs.generator.measurement
    return tuple(
        create_seeded_rotation_scheduler(
            sensor_id=sensor.sensor_id,
            sample_rate_hz=measurement.sample_rate_hz,
            rotation_rate_hz=measurement.rotation_rate_hz,
            seed=inputs.generator.seed,
        )
        for sensor in inputs.environment.sensors
    )


def build_measurement_generators(
    inputs: GeneratorInputs,
    *,
    spatial_distortions: SpatialDistortionTimeline | None = None,
) -> tuple[MeasurementGenerator, ...]:
    """Build sensor-specific distance and quality generators in environment order."""
    measurement = inputs.generator.measurement
    time_scale = scenario_time_scale(inputs.generator.scenario.mean_fill_duration_s)
    dropout = measurement.distortions.dropout
    quality_by_sensor_id = {
        quality.sensor_id: quality for quality in inputs.quality_profile.sensors
    }
    return tuple(
        MeasurementGenerator(
            sensor_id=sensor.sensor_id,
            min_distance_m=measurement.min_distance_m,
            max_distance_m=measurement.max_distance_m,
            noise_enabled=measurement.distance_noise.enabled,
            noise_standard_deviation_m=measurement.distance_noise.standard_deviation_m,
            noise_limit_m=measurement.distance_noise.limit_m,
            valid_quality_frequencies=quality_by_sensor_id[
                sensor.sensor_id
            ].valid_distance_frequencies,
            invalid_quality_frequencies=quality_by_sensor_id[
                sensor.sensor_id
            ].invalid_distance_frequencies,
            reflection_error_enabled=measurement.distortions.reflection_error.enabled,
            reflection_error_probability=measurement.distortions.reflection_error.probability,
            reflection_error_reduction_range_m=(
                measurement.distortions.reflection_error.distance_reduction_m_range
            ),
            dropout_scheduler=(
                SensorDropoutScheduler(
                    sensor_id=sensor.sensor_id,
                    event_interval_s_range=scale_duration_range(
                        dropout.event_interval_s_range,
                        time_scale,
                    ),
                    duration_s_range=scale_duration_range(
                        dropout.duration_s_range,
                        time_scale,
                    ),
                    seed=inputs.generator.seed,
                )
                if dropout.enabled
                else None
            ),
            seed=inputs.generator.seed,
            spatial_distortions=spatial_distortions,
            sensor_frame=(build_sensor_frame(sensor) if spatial_distortions is not None else None),
        )
        for sensor in inputs.environment.sensors
    )


def build_spatial_distortion_timeline(
    inputs: GeneratorInputs,
) -> SpatialDistortionTimeline | None:
    """Build the shared spatial event timeline when an implemented cause is enabled."""
    config = inputs.generator
    falling = config.measurement.distortions.falling_material
    voids = config.measurement.distortions.voids
    collection = config.measurement.distortions.collection_occlusion
    if not falling.enabled and not voids.enabled and not collection.enabled:
        return None

    time_scale = scenario_time_scale(config.scenario.mean_fill_duration_s)
    boundary = Polygon2(tuple(Vec2(x, y) for x, y in inputs.environment.boundary_xy_m))
    return SpatialDistortionTimeline(
        boundary=boundary,
        static_scene=build_environment_scene(inputs.environment),
        falling_material=(
            FallingMaterialSettings(
                event_rate_per_s=scale_event_rate_per_s(
                    falling.event_rate_per_s,
                    time_scale,
                ),
                radius_m_range=falling.radius_m_range,
                duration_s_range=scale_duration_range(
                    falling.duration_s_range,
                    time_scale,
                ),
                distance_reduction_m_range=falling.distance_reduction_m_range,
                inlet_positions=tuple(Vec2(x, y) for x, y in config.scenario.inlet_positions_xy_m),
                placement_radius_m=config.scenario.surface.pile_spread_radius_m,
            )
            if falling.enabled
            else None
        ),
        voids=(
            VoidSettings(
                surface_area_ratio=voids.surface_area_ratio,
                radius_m_range=voids.radius_m_range,
                duration_s_range=scale_duration_range(
                    voids.duration_s_range,
                    time_scale,
                ),
                cover_height_increase_m=voids.cover_height_increase_m,
                distance_increase_m_range=voids.distance_increase_m_range,
            )
            if voids.enabled
            else None
        ),
        collection_occlusion=(
            CollectionOcclusionSettings(
                event_interval_s_range=scale_duration_range(
                    collection.event_interval_s_range,
                    time_scale,
                ),
                radius_m_range=collection.radius_m_range,
                duration_s_range=scale_duration_range(
                    collection.duration_s_range,
                    time_scale,
                ),
                distance_reduction_m_range=collection.distance_reduction_m_range,
            )
            if collection.enabled
            else None
        ),
        seed=config.seed,
    )
