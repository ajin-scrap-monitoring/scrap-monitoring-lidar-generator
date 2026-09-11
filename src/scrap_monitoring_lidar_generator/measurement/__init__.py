"""Sensor rotation and measurement generation."""

from scrap_monitoring_lidar_generator.measurement.dropout import SensorDropoutScheduler
from scrap_monitoring_lidar_generator.measurement.generation import MeasurementGenerator
from scrap_monitoring_lidar_generator.measurement.models import (
    MeasuredScan,
    MeasurementResult,
    ReferencePoint,
    ReferenceScan,
    TimedMeasuredScan,
    TimedReferenceScan,
)
from scrap_monitoring_lidar_generator.measurement.reference import (
    DEFAULT_MAX_DISTANCE_M,
    DEFAULT_MIN_DISTANCE_M,
    ReferenceScanner,
)
from scrap_monitoring_lidar_generator.measurement.rotation import (
    ScheduledScan,
    SensorRotationScheduler,
    create_seeded_rotation_scheduler,
)
from scrap_monitoring_lidar_generator.measurement.spatial import (
    FallingMaterialEvent,
    FallingMaterialSettings,
    SpatialDistanceResolver,
    SpatialDistortionTimeline,
    resolve_falling_material_distances,
)

__all__ = [
    "DEFAULT_MAX_DISTANCE_M",
    "DEFAULT_MIN_DISTANCE_M",
    "FallingMaterialEvent",
    "FallingMaterialSettings",
    "MeasuredScan",
    "MeasurementGenerator",
    "MeasurementResult",
    "ReferencePoint",
    "ReferenceScan",
    "ReferenceScanner",
    "ScheduledScan",
    "SensorDropoutScheduler",
    "SensorRotationScheduler",
    "SpatialDistanceResolver",
    "SpatialDistortionTimeline",
    "TimedMeasuredScan",
    "TimedReferenceScan",
    "create_seeded_rotation_scheduler",
    "resolve_falling_material_distances",
]
