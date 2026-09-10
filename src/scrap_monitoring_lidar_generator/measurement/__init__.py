"""Sensor rotation and measurement generation."""

from scrap_monitoring_lidar_generator.measurement.models import ReferencePoint, ReferenceScan
from scrap_monitoring_lidar_generator.measurement.reference import (
    DEFAULT_MAX_DISTANCE_M,
    DEFAULT_MIN_DISTANCE_M,
    ReferenceScanner,
)

__all__ = [
    "DEFAULT_MAX_DISTANCE_M",
    "DEFAULT_MIN_DISTANCE_M",
    "ReferencePoint",
    "ReferenceScan",
    "ReferenceScanner",
]
