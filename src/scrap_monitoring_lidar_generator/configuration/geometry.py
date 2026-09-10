"""Conversion from validated configuration to geometry models."""

from collections.abc import Iterable

from scrap_monitoring_lidar_generator.configuration.models import (
    EnvironmentConfig,
    SensorConfig,
)
from scrap_monitoring_lidar_generator.geometry import (
    EnvironmentScene,
    Polygon2,
    RaySurface,
    SensorFrame,
    Triangle,
    Vec2,
    Vec3,
)


def build_environment_scene(
    environment: EnvironmentConfig,
    surface_triangles: Iterable[Triangle] = (),
    *,
    dynamic_surface: RaySurface | None = None,
) -> EnvironmentScene:
    """Build a scene from validated environment geometry."""
    boundary = Polygon2(tuple(Vec2(x, y) for x, y in environment.boundary_xy_m))
    return EnvironmentScene(
        boundary=boundary,
        floor_z_m=environment.floor_z_m,
        top_z_m=environment.top_z_m,
        surface_triangles=tuple(surface_triangles),
        dynamic_surface=dynamic_surface,
    )


def build_sensor_frame(sensor: SensorConfig) -> SensorFrame:
    """Build a sensor frame from validated installation vectors."""
    return SensorFrame(
        origin_m=Vec3(*sensor.p0_m),
        u0=Vec3(*sensor.u0),
        u90=Vec3(*sensor.u90),
    )
