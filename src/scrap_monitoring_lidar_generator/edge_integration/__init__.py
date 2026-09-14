"""Configuration bridge for the ajin edge LiDAR processing service."""

from scrap_monitoring_lidar_generator.edge_integration.synthetic_processing_config import (
    ProcessingConfigError,
    build_synthetic_processing_config,
    write_synthetic_processing_config,
)

__all__ = [
    "ProcessingConfigError",
    "build_synthetic_processing_config",
    "write_synthetic_processing_config",
]
