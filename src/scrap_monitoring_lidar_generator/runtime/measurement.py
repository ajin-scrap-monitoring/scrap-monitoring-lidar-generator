"""Measurement runtime assembly from validated generator inputs."""

from scrap_monitoring_lidar_generator.configuration import GeneratorInputs
from scrap_monitoring_lidar_generator.measurement import (
    MeasurementGenerator,
    SensorDropoutScheduler,
    SensorRotationScheduler,
    create_seeded_rotation_scheduler,
)
from scrap_monitoring_lidar_generator.scenario import scale_duration_range, scenario_time_scale


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
        )
        for sensor in inputs.environment.sensors
    )
