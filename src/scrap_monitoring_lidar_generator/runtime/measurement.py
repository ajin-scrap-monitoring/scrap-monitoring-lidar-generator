"""Measurement runtime assembly from validated generator inputs."""

from scrap_monitoring_lidar_generator.configuration import GeneratorInputs
from scrap_monitoring_lidar_generator.measurement import (
    SensorRotationScheduler,
    create_seeded_rotation_scheduler,
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
