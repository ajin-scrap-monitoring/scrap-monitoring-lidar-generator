"""Configuration bridge for the ajin edge LiDAR processing service."""

from scrap_monitoring_lidar_generator.edge_integration.processing_config import (
    ProcessingConfigError,
    build_processing_config,
    write_processing_config,
)

__all__ = [
    "ProcessingConfigError",
    "build_processing_config",
    "write_processing_config",
]
