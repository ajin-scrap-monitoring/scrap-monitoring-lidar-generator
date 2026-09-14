"""gRPC scan source compatible with the edge LiDAR driver contract."""

from scrap_monitoring_lidar_generator.scan_stream.frames import ScanFrameFactory
from scrap_monitoring_lidar_generator.scan_stream.server import (
    GrpcScanServer,
    ScanServerStats,
)

__all__ = ["GrpcScanServer", "ScanFrameFactory", "ScanServerStats"]
