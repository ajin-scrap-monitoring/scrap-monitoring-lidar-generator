"""Headless Matplotlib renderer for load-model observation records."""

from collections.abc import Sequence
from pathlib import Path
from shutil import which
from typing import Any

from scrap_monitoring_lidar_generator.configuration import EnvironmentConfig, GeneratorInputs
from scrap_monitoring_lidar_generator.observation import ObservationRecord
from tools.visualization.mesh import build_environment_mesh
from tools.visualization.timeline import RenderTimeline


class VisualizationError(RuntimeError):
    """Raised when optional rendering cannot produce the requested output."""


MAX_WIDTH = 3_840
MAX_HEIGHT = 2_160
MAX_PIXELS = MAX_WIDTH * MAX_HEIGHT


def render_mp4(
    timeline: RenderTimeline,
    *,
    inputs: GeneratorInputs,
    output_path: Path,
    width: int,
    height: int,
    camera: str,
) -> None:
    """Render selected model states to an MP4 using external FFmpeg."""
    validate_resolution(width, height)
    if which("ffmpeg") is None:
        raise VisualizationError("FFmpeg executable is required for MP4 output")
    plt, FFMpegWriter, Path3D = _load_matplotlib()
    output_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    figure = plt.figure(figsize=(width / 100.0, height / 100.0), dpi=100)
    try:
        writer = FFMpegWriter(fps=timeline.fps, metadata={"title": "load model observation"})
        environment = inputs.environment
        with writer.saving(figure, str(output_path), dpi=100):
            for record in timeline.records:
                _draw_frame(
                    figure,
                    record,
                    environment=environment,
                    inlet_positions=inputs.generator.scenario.inlet_positions_xy_m,
                    camera=camera,
                    path_class=Path3D,
                )
                writer.grab_frame()
    except Exception as error:
        raise VisualizationError(f"MP4 rendering failed: {error}") from error
    finally:
        plt.close(figure)


def preview(
    timeline: RenderTimeline,
    *,
    inputs: GeneratorInputs,
    width: int,
    height: int,
    camera: str,
) -> None:
    """Show the selected model states in an interactive development window."""
    validate_resolution(width, height)
    plt, _writer, Path3D = _load_matplotlib(interactive=True)
    figure = plt.figure(figsize=(width / 100.0, height / 100.0), dpi=100)
    try:
        for record in timeline.records:
            _draw_frame(
                figure,
                record,
                environment=inputs.environment,
                inlet_positions=inputs.generator.scenario.inlet_positions_xy_m,
                camera=camera,
                path_class=Path3D,
            )
            figure.canvas.draw_idle()
            plt.pause(1.0 / timeline.fps)
        plt.show()
    except Exception as error:
        raise VisualizationError(f"preview failed: {error}") from error
    finally:
        plt.close(figure)


def validate_resolution(width: int, height: int) -> None:
    """Reject output dimensions above the development rendering cap."""
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
        or width > MAX_WIDTH
        or height > MAX_HEIGHT
        or width * height > MAX_PIXELS
    ):
        raise VisualizationError(f"resolution must not exceed {MAX_WIDTH}x{MAX_HEIGHT} pixels")


def _load_matplotlib(*, interactive: bool = False) -> tuple[Any, Any, Any]:
    try:
        import matplotlib

        if not interactive:
            matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
        from matplotlib.animation import FFMpegWriter
        from matplotlib.path import Path as Path3D
    except ImportError as error:
        raise VisualizationError("matplotlib is required for visualization") from error
    return plt, FFMpegWriter, Path3D


def _draw_frame(
    figure: Any,
    record: ObservationRecord,
    *,
    environment: EnvironmentConfig,
    inlet_positions: Sequence[tuple[float, float]],
    camera: str,
    path_class: Any,
) -> None:
    figure.clear()
    axes = figure.add_subplot(111, projection="3d")
    x_values = record.snapshot.surface.x_coordinates_m
    y_values = record.snapshot.surface.y_coordinates_m
    heights_m = record.snapshot.surface.heights_m
    inside = (
        path_class(environment.boundary_xy_m)
        .contains_points([(float(x), float(y)) for y in y_values for x in x_values])
        .reshape(heights_m.shape)
    )
    masked_heights = heights_m.copy()
    masked_heights[~inside] = float("nan")
    x_grid, y_grid = _meshgrid(x_values, y_values)
    axes.plot_surface(x_grid, y_grid, masked_heights, cmap="copper", linewidth=0.0, alpha=0.9)

    environment_mesh = build_environment_mesh(environment)
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # type: ignore[import-untyped]

    axes.add_collection3d(
        Poly3DCollection(
            [environment_mesh.vertices[face] for face in environment_mesh.faces],
            facecolors="lightgray",
            edgecolors="slategray",
            linewidths=0.4,
            alpha=0.18,
        )
    )
    for face in environment_mesh.faces:
        vertices = environment_mesh.vertices[face]
        axes.plot(vertices[:, 0], vertices[:, 1], vertices[:, 2], color="slategray", linewidth=0.7)
    boundary = [*environment.boundary_xy_m, environment.boundary_xy_m[0]]
    axes.plot(
        [point[0] for point in boundary],
        [point[1] for point in boundary],
        [environment.floor_z_m for _point in boundary],
        color="black",
        linewidth=1.0,
    )
    axes.scatter(
        [position[0] for position in inlet_positions],
        [position[1] for position in inlet_positions],
        [environment.floor_z_m for _position in inlet_positions],
        color="royalblue",
        marker="s",
        label="inlet",
    )
    for sensor in environment.sensors:
        x, y, z = sensor.p0_m
        axes.scatter([x], [y], [z], color="crimson", marker="^", label=sensor.sensor_id)
        axes.quiver(x, y, z, *sensor.u0, length=0.5, color="crimson")

    if camera == "top":
        axes.view_init(elev=89.0, azim=-90.0)
    else:
        axes.view_init(elev=28.0, azim=-55.0)
    axes.set_xlim(
        min(point[0] for point in environment.boundary_xy_m),
        max(point[0] for point in environment.boundary_xy_m),
    )
    axes.set_ylim(
        min(point[1] for point in environment.boundary_xy_m),
        max(point[1] for point in environment.boundary_xy_m),
    )
    axes.set_zlim(environment.floor_z_m, environment.top_z_m)
    axes.set_xlabel("x (m)")
    axes.set_ylabel("y (m)")
    axes.set_zlabel("height (m)")
    state = record.snapshot.state
    axes.set_title("Load model surface")
    figure.text(
        0.02,
        0.02,
        f"t={state.elapsed_s:.2f}s  fill={state.surface_fill_ratio:.1%}  state={state.phase.value}",
    )


def _meshgrid(x_values: Any, y_values: Any) -> tuple[Any, Any]:
    import numpy as np

    return np.meshgrid(x_values, y_values)
