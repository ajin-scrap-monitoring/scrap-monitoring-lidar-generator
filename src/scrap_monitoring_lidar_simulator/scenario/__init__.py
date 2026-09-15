"""Scene state and scenario transitions."""

from scrap_monitoring_lidar_simulator.scenario.height_field import (
    DEFAULT_ANGLE_OF_REPOSE_DEG,
    DEFAULT_SLOPE_RELAXATION_MAX_ITERATIONS,
    HeightField,
    RoughnessChange,
    SlopeRelaxation,
    VolumeChange,
)
from scrap_monitoring_lidar_simulator.scenario.rate_profile import (
    SmoothRateProfile,
    SmoothRateSegment,
    create_smooth_rate_profile,
)
from scrap_monitoring_lidar_simulator.scenario.simulator import (
    CollectionPlan,
    FillPlan,
    ScenarioModelSnapshot,
    ScenarioPhase,
    ScenarioSettings,
    ScenarioSimulator,
    ScenarioSnapshot,
)
from scrap_monitoring_lidar_simulator.scenario.snapshot import SurfaceModelSnapshot
from scrap_monitoring_lidar_simulator.scenario.time_scale import (
    REFERENCE_MEAN_FILL_DURATION_S,
    scale_duration_range,
    scale_event_rate_per_s,
    scenario_time_scale,
)

__all__ = [
    "DEFAULT_ANGLE_OF_REPOSE_DEG",
    "DEFAULT_SLOPE_RELAXATION_MAX_ITERATIONS",
    "REFERENCE_MEAN_FILL_DURATION_S",
    "CollectionPlan",
    "FillPlan",
    "HeightField",
    "RoughnessChange",
    "ScenarioModelSnapshot",
    "ScenarioPhase",
    "ScenarioSettings",
    "ScenarioSimulator",
    "ScenarioSnapshot",
    "SlopeRelaxation",
    "SmoothRateProfile",
    "SmoothRateSegment",
    "SurfaceModelSnapshot",
    "VolumeChange",
    "create_smooth_rate_profile",
    "scale_duration_range",
    "scale_event_rate_per_s",
    "scenario_time_scale",
]
