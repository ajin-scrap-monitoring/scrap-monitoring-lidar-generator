"""Tests for deterministic observation timeline and engineering meshes."""

import numpy as np
import pytest
from tools.visualization import cli as visualization_cli
from tools.visualization.mesh import build_environment_mesh, build_surface_mesh
from tools.visualization.timeline import select_render_timeline

from scrap_monitoring_lidar_generator.configuration import EnvironmentConfig
from scrap_monitoring_lidar_generator.observation import ObservationRecord
from scrap_monitoring_lidar_generator.scenario import (
    ScenarioModelSnapshot,
    ScenarioPhase,
    ScenarioSnapshot,
    SurfaceModelSnapshot,
)


def _record(elapsed_s: float = 1.0) -> ObservationRecord:
    state = ScenarioSnapshot(
        elapsed_s=elapsed_s,
        surface_updated_at_s=elapsed_s,
        cycle_index=0,
        phase=ScenarioPhase.FILLING,
        phase_started_at_s=0.0,
        phase_ends_at_s=10.0,
        phase_duration_s=10.0,
        rate_factor=1.0,
        target_fill_ratio=0.9,
        surface_fill_ratio=0.1,
        surface_volume_m3=1.0,
        current_inlet_index=0,
    )
    surface = SurfaceModelSnapshot(
        x_coordinates_m=np.array([0.0, 1.0]),
        y_coordinates_m=np.array([0.0, 1.0]),
        heights_m=np.array([[0.0, 0.1], [0.2, 0.3]]),
        cell_size_m=1.0,
    )
    return ObservationRecord.from_snapshot(
        ScenarioModelSnapshot(state=state, surface=surface),
        environment_id="synthetic-room-v1",
        run_id="run-a",
        input_fingerprint_sha256="0" * 64,
        seed=42,
    )


def test_timeline_selects_records_in_requested_interval() -> None:
    records = tuple(_record(time_s) for time_s in (0.0, 1.0, 2.0, 3.0))

    timeline = select_render_timeline(
        records,
        fps=2,
        start_time_s=1.0,
        end_time_s=3.0,
        duration_s=2.0,
    )

    assert timeline.frame_count == 4
    assert timeline.records[0].snapshot.state.elapsed_s == pytest.approx(1.0)
    assert timeline.records[-1].snapshot.state.elapsed_s == pytest.approx(3.0)


def test_timeline_rejects_frame_limit() -> None:
    records = tuple(_record(time_s) for time_s in (0.0, 1.0))

    with pytest.raises(ValueError, match="exceeding max_frames"):
        select_render_timeline(records, fps=10, duration_s=1.0, max_frames=5)


def test_meshes_are_deterministic_and_read_only() -> None:
    record = _record()
    first = build_surface_mesh(record)
    second = build_surface_mesh(record)

    np.testing.assert_array_equal(first.vertices, second.vertices)
    np.testing.assert_array_equal(first.faces, second.faces)
    assert not first.vertices.flags.writeable
    assert not first.faces.flags.writeable

    environment = EnvironmentConfig(
        environment_id="synthetic-room-v1",
        boundary_xy_m=((0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0)),
        floor_z_m=0.0,
        top_z_m=2.0,
        sensors=(),
    )
    enclosure = build_environment_mesh(environment)
    assert len(enclosure.vertices) == 8
    assert len(enclosure.faces) == 10


def test_visualization_cli_requires_output_or_preview() -> None:
    with pytest.raises(SystemExit) as exit_info:
        visualization_cli.main(["--config", "generator.json", "--input", "observations.jsonl"])

    assert exit_info.value.code == 2


def test_visualization_cli_rejects_resolution_over_cap() -> None:
    with pytest.raises(SystemExit) as exit_info:
        visualization_cli.main(
            [
                "--config",
                "generator.json",
                "--input",
                "observations.jsonl",
                "--output",
                "output.mp4",
                "--width",
                "3840",
                "--height",
                "2161",
            ]
        )

    assert exit_info.value.code == 2
