"""Validated generator and quality profile configuration models."""

from dataclasses import dataclass
from pathlib import Path

from scrap_monitoring_lidar_generator.configuration.models import Coordinate2, EnvironmentConfig

type FloatRange = tuple[float, float]
type QualityFrequencies = tuple[int, ...]


@dataclass(frozen=True, slots=True)
class SurfaceConfig:
    """Resolution and shape controls for the generated surface."""

    cell_size_m: float
    update_interval_s: float
    pile_spread_radius_m: float
    roughness_height_range_m: FloatRange
    roughness_radius_range_m: FloatRange


@dataclass(frozen=True, slots=True)
class ScenarioConfig:
    """Fill, collection, inlet, and surface evolution controls."""

    mean_fill_duration_s: float
    fill_duration_factor_range: FloatRange
    fill_rate_factor_range: FloatRange
    fill_rate_change_duration_s_range: FloatRange
    collection_threshold_range: FloatRange
    collection_duration_factor_range: FloatRange
    collection_rate_factor_range: FloatRange
    collection_rate_change_duration_s_range: FloatRange
    inlet_positions_xy_m: tuple[Coordinate2, ...]
    inlet_switch_activation_ratio: float
    inlet_switch_height_difference_m: float
    inlet_comparison_radius_m: float
    surface: SurfaceConfig


@dataclass(frozen=True, slots=True)
class DistanceNoiseConfig:
    """Independent bounded distance noise controls."""

    enabled: bool
    standard_deviation_m: float
    limit_m: float


@dataclass(frozen=True, slots=True)
class FallingMaterialConfig:
    """Short occlusion controls for falling material."""

    enabled: bool
    event_rate_per_s: float
    radius_m_range: FloatRange
    duration_s_range: FloatRange
    distance_reduction_m_range: FloatRange


@dataclass(frozen=True, slots=True)
class VoidsConfig:
    """Persistent local void controls."""

    enabled: bool
    surface_area_ratio: float
    radius_m_range: FloatRange
    duration_s_range: FloatRange
    cover_height_increase_m: float
    distance_increase_m_range: FloatRange


@dataclass(frozen=True, slots=True)
class CollectionOcclusionConfig:
    """Collection-period occlusion controls."""

    enabled: bool
    event_interval_s_range: FloatRange
    radius_m_range: FloatRange
    duration_s_range: FloatRange
    distance_reduction_m_range: FloatRange


@dataclass(frozen=True, slots=True)
class ReflectionErrorConfig:
    """Independent reflection path error controls."""

    enabled: bool
    probability: float
    distance_reduction_m_range: FloatRange


@dataclass(frozen=True, slots=True)
class DropoutConfig:
    """Persistent invalid measurement interval controls."""

    enabled: bool
    event_interval_s_range: FloatRange
    duration_s_range: FloatRange


@dataclass(frozen=True, slots=True)
class DistortionConfig:
    """Cause-specific measurement distortion controls."""

    falling_material: FallingMaterialConfig
    voids: VoidsConfig
    collection_occlusion: CollectionOcclusionConfig
    reflection_error: ReflectionErrorConfig
    dropout: DropoutConfig


@dataclass(frozen=True, slots=True)
class MeasurementConfig:
    """Sensor sampling, range, noise, and distortion controls."""

    sample_rate_hz: float
    rotation_rate_hz: float
    min_distance_m: float
    max_distance_m: float
    distance_noise: DistanceNoiseConfig
    distortions: DistortionConfig


@dataclass(frozen=True, slots=True)
class DiagnosticsConfig:
    """Bounded reference output controls."""

    enabled: bool
    output_path: Path
    sample_scan_limit_per_sensor: int


@dataclass(frozen=True, slots=True)
class GeneratorConfig:
    """Generator-only execution configuration."""

    seed: int
    environment_path: Path
    quality_profile_path: Path
    scenario: ScenarioConfig
    measurement: MeasurementConfig
    diagnostics: DiagnosticsConfig


@dataclass(frozen=True, slots=True)
class SensorQualityConfig:
    """Quality frequencies for valid and invalid measurements."""

    sensor_id: str
    valid_distance_frequencies: QualityFrequencies
    invalid_distance_frequencies: QualityFrequencies


@dataclass(frozen=True, slots=True)
class QualityProfileConfig:
    """Per-sensor synthetic quality distributions."""

    sensors: tuple[SensorQualityConfig, ...]


@dataclass(frozen=True, slots=True)
class GeneratorInputs:
    """Generator configuration with its validated referenced inputs."""

    generator: GeneratorConfig
    environment: EnvironmentConfig
    quality_profile: QualityProfileConfig
