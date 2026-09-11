"""Transport runtime assembly from validated generator inputs."""

from scrap_monitoring_lidar_generator.configuration import GeneratorInputs
from scrap_monitoring_lidar_generator.transport import AsyncScanSender


def build_scan_sender(inputs: GeneratorInputs) -> AsyncScanSender:
    """Build scan delivery state from validated transport settings."""
    config = inputs.generator.transport
    return AsyncScanSender(
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
    )
