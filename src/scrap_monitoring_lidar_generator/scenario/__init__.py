"""Scene state and scenario transitions."""

from scrap_monitoring_lidar_generator.scenario.height_field import (
    HeightField,
    RoughnessChange,
    VolumeChange,
)
from scrap_monitoring_lidar_generator.scenario.rate_profile import (
    SmoothRateProfile,
    SmoothRateSegment,
    create_smooth_rate_profile,
)
from scrap_monitoring_lidar_generator.scenario.simulator import (
    CollectionPlan,
    FillPlan,
    ScenarioPhase,
    ScenarioSettings,
    ScenarioSimulator,
    ScenarioSnapshot,
)
from scrap_monitoring_lidar_generator.scenario.time_scale import (
    REFERENCE_MEAN_FILL_DURATION_S,
    scale_duration_range,
    scale_event_rate_per_s,
    scenario_time_scale,
)

__all__ = [
    "REFERENCE_MEAN_FILL_DURATION_S",
    "CollectionPlan",
    "FillPlan",
    "HeightField",
    "RoughnessChange",
    "ScenarioPhase",
    "ScenarioSettings",
    "ScenarioSimulator",
    "ScenarioSnapshot",
    "SmoothRateProfile",
    "SmoothRateSegment",
    "VolumeChange",
    "create_smooth_rate_profile",
    "scale_duration_range",
    "scale_event_rate_per_s",
    "scenario_time_scale",
]
