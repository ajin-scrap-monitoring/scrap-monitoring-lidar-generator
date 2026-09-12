"""Tests for read-only model snapshots and bounded observation output."""

import asyncio
import json
from pathlib import Path

import numpy as np
import pytest

from scrap_monitoring_lidar_generator.configuration import load_generator_inputs
from scrap_monitoring_lidar_generator.geometry import Vec2
from scrap_monitoring_lidar_generator.observation import (
    JsonLinesObservationRecorder,
    ObservationFormatError,
    ObservationRecord,
    decode_observation_line,
    encode_observation_line,
)
from scrap_monitoring_lidar_generator.runtime import build_scenario_simulator
from scrap_monitoring_lidar_generator.scenario import (
    ScenarioModelSnapshot,
    ScenarioPhase,
    ScenarioSnapshot,
    SurfaceModelSnapshot,
)

_ROOT = Path(__file__).parents[2]


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


def test_model_snapshot_copies_and_freezes_the_surface_grid() -> None:
    inputs = load_generator_inputs(_ROOT / "examples" / "generator.v1.json")
    simulator = build_scenario_simulator(inputs)
    simulator.advance_to(1.0)

    snapshot = simulator.observation_snapshot()
    before = snapshot.surface.heights_m.copy()
    simulator.surface.add_volume(
        0.1,
        center=Vec2(*inputs.generator.scenario.inlet_positions_xy_m[0]),
        spread_radius_m=inputs.generator.scenario.surface.pile_spread_radius_m,
    )

    np.testing.assert_array_equal(snapshot.surface.heights_m, before)
    assert not snapshot.surface.heights_m.flags.writeable
    with pytest.raises(ValueError):
        snapshot.surface.heights_m[0, 0] = 1.0


def test_observation_line_round_trips_without_scan_fields() -> None:
    encoded = encode_observation_line(_record())
    decoded = decode_observation_line(encoded)

    assert decoded.environment_id == "synthetic-room-v1"
    assert decoded.snapshot.state.elapsed_s == pytest.approx(1.0)
    np.testing.assert_array_equal(decoded.snapshot.surface.heights_m, [[0.0, 0.1], [0.2, 0.3]])
    document = json.loads(encoded)
    assert document["type"] == "load_model_observation"
    assert "points" not in document


def test_observation_decoder_rejects_unknown_fields() -> None:
    document = _record().to_document()
    document["unknown"] = True

    with pytest.raises(ObservationFormatError, match="unexpected fields"):
        decode_observation_line(json.dumps(document))


def test_observation_decoder_rejects_duplicate_fields() -> None:
    encoded = encode_observation_line(_record())
    duplicate = encoded[:-1] + ',"seed":42}'

    with pytest.raises(ObservationFormatError, match="invalid observation JSON"):
        decode_observation_line(duplicate)


def test_recorder_flushes_bounded_records_asynchronously(tmp_path: Path) -> None:
    async def run() -> None:
        recorder = JsonLinesObservationRecorder(
            output_path=tmp_path / "observations.jsonl",
            environment_id="synthetic-room-v1",
            run_id="run-a",
            input_fingerprint_sha256="0" * 64,
            seed=42,
            interval_s=1.0,
            max_records=2,
            queue_capacity=2,
        )
        await recorder.start()
        assert recorder.publish(_record(0.1).snapshot)
        assert not recorder.publish(_record(0.2).snapshot)
        assert recorder.publish(_record(1.0).snapshot)
        assert not recorder.publish(_record(2.0).snapshot)
        await recorder.close()

        lines = (tmp_path / "observations.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert recorder.recorded_count == len(lines)
        assert recorder.error is None
        assert [json.loads(line)["scenario"]["elapsed_s"] for line in lines] == [0.1, 1.0]

    asyncio.run(run())


def test_recorder_keeps_latest_snapshot_when_queue_is_full(tmp_path: Path) -> None:
    async def run() -> None:
        recorder = JsonLinesObservationRecorder(
            output_path=tmp_path / "latest.jsonl",
            environment_id="synthetic-room-v1",
            run_id="run-a",
            input_fingerprint_sha256="0" * 64,
            seed=42,
            interval_s=1.0,
            max_records=3,
            queue_capacity=1,
        )
        await recorder.start()
        assert recorder.publish(_record(0.1).snapshot)
        assert recorder.publish(_record(1.0).snapshot)
        await recorder.close()

        lines = (tmp_path / "latest.jsonl").read_text(encoding="utf-8").splitlines()
        assert [json.loads(line)["scenario"]["elapsed_s"] for line in lines] == [1.0]
        assert recorder.dropped_count == 1

    asyncio.run(run())
