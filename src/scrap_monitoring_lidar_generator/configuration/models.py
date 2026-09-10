"""Validated environment configuration models."""

from dataclasses import dataclass

type Coordinate2 = tuple[float, float]
type Coordinate3 = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class SensorConfig:
    """Sensor installation in the shared environment coordinate system."""

    sensor_id: str
    p0_m: Coordinate3
    u0: Coordinate3
    u90: Coordinate3


@dataclass(frozen=True, slots=True)
class EnvironmentConfig:
    """Environment geometry and sensor installations."""

    environment_id: str
    boundary_xy_m: tuple[Coordinate2, ...]
    floor_z_m: float
    top_z_m: float
    sensors: tuple[SensorConfig, ...]
