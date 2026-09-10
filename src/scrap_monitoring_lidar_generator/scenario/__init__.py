"""Scene state and scenario transitions."""

from scrap_monitoring_lidar_generator.scenario.height_field import HeightField, VolumeChange
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

__all__ = [
    "CollectionPlan",
    "FillPlan",
    "HeightField",
    "ScenarioPhase",
    "ScenarioSettings",
    "ScenarioSimulator",
    "ScenarioSnapshot",
    "SmoothRateProfile",
    "SmoothRateSegment",
    "VolumeChange",
    "create_smooth_rate_profile",
]
