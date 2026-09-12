"""Transport runtime assembly from validated generator inputs."""

from scrap_monitoring_lidar_generator.configuration import GeneratorInputs
from scrap_monitoring_lidar_generator.transport import (
    AsyncMultiSensorScanSender,
    SenderHaltCallback,
)


def build_scan_sender(
    inputs: GeneratorInputs,
    *,
    on_halt: SenderHaltCallback | None = None,
) -> AsyncMultiSensorScanSender:
    """Build independent sensor delivery lanes from validated settings."""
    config = inputs.generator.transport
    return AsyncMultiSensorScanSender(
        sensor_ids=tuple(sensor.sensor_id for sensor in inputs.environment.sensors),
        host=config.host,
        port=config.port,
        max_body_bytes=config.max_message_body_bytes,
        buffer_max_age_s=config.buffer_max_age_s,
        buffer_max_bytes=config.buffer_max_bytes,
        connect_timeout_s=config.connect_timeout_s,
        send_timeout_s=config.send_timeout_s,
        ack_timeout_s=config.ack_timeout_s,
        reconnect_initial_delay_s=config.reconnect_initial_delay_s,
        reconnect_max_delay_s=config.reconnect_max_delay_s,
        seed=inputs.generator.seed,
        on_halt=on_halt,
    )
