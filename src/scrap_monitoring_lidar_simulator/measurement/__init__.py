"""Sensor rotation and measurement generation."""

from scrap_monitoring_lidar_simulator.measurement.dropout import SensorDropoutScheduler
from scrap_monitoring_lidar_simulator.measurement.generation import MeasurementGenerator
from scrap_monitoring_lidar_simulator.measurement.models import (
    MeasuredScan,
    MeasurementResult,
    ReferencePoint,
    ReferenceScan,
    TimedMeasuredScan,
    TimedReferenceScan,
)
from scrap_monitoring_lidar_simulator.measurement.reference import (
    DEFAULT_MAX_DISTANCE_M,
    DEFAULT_MIN_DISTANCE_M,
    ReferenceScanner,
)
from scrap_monitoring_lidar_simulator.measurement.rotation import (
    ScheduledScan,
    SensorRotationScheduler,
    create_seeded_rotation_scheduler,
)
from scrap_monitoring_lidar_simulator.measurement.spatial import (
    CollectionOcclusionEvent,
    CollectionOcclusionSettings,
    FallingMaterialEvent,
    FallingMaterialSettings,
    SpatialDistanceResolver,
    SpatialDistortionTimeline,
    VoidEvent,
    VoidSettings,
    resolve_collection_occlusion_distances,
    resolve_falling_material_distances,
    resolve_void_distances,
)

__all__ = [
    "DEFAULT_MAX_DISTANCE_M",
    "DEFAULT_MIN_DISTANCE_M",
    "CollectionOcclusionEvent",
    "CollectionOcclusionSettings",
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
    "VoidEvent",
    "VoidSettings",
    "create_seeded_rotation_scheduler",
    "resolve_collection_occlusion_distances",
    "resolve_falling_material_distances",
    "resolve_void_distances",
]
