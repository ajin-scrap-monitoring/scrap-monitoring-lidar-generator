"""Tests for read-only model snapshots and bounded observation streaming."""

import asyncio
import json
from pathlib import Path

import numpy as np
import pytest

from scrap_monitoring_lidar_generator.configuration import load_generator_inputs
from scrap_monitoring_lidar_generator.geometry import Vec2
from scrap_monitoring_lidar_generator.observation import (
    ObservationFormatError,
    ObservationRecord,
    ObservationScene,
    ObservationStreamHeader,
    TcpObservationPublisher,
    decode_observation_header_line,
    decode_observation_line,
    encode_observation_frame,
    encode_observation_header_line,
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
_INPUTS = load_generator_inputs(_ROOT / "examples" / "generator.v1.json")
_SCENE = ObservationScene.from_inputs(_INPUTS)


def _header() -> ObservationStreamHeader:
    return ObservationStreamHeader(
        environment_id="synthetic-scrap-pit-v1",
        run_id="run-a",
        input_fingerprint_sha256="0" * 64,
        seed=42,
        scene=_SCENE,
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
        sequence=max(1, round(elapsed_s * 10)),
        run_id="run-a",
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

    assert decoded.run_id == "run-a"
    assert decoded.sequence == 10
    assert decoded.snapshot.state.elapsed_s == pytest.approx(1.0)
    np.testing.assert_array_equal(decoded.snapshot.surface.heights_m, [[0.0, 0.1], [0.2, 0.3]])
    document = json.loads(encoded)
    assert document["type"] == "load_model_observation"
    assert "scene" not in document
    assert "points" not in document


def test_observation_header_round_trips_with_static_scene() -> None:
    encoded = encode_observation_header_line(_header())
    decoded = decode_observation_header_line(encoded)

    assert decoded.environment_id == "synthetic-scrap-pit-v1"
    assert decoded.run_id == "run-a"
    assert decoded.scene.boundary_xy_m == (
        (0.0, 0.0),
        (4.0, 0.0),
        (4.0, 5.3),
        (2.7, 5.3),
        (1.9, 2.5),
        (0.0, 2.5),
    )
    assert [sensor.sensor_id for sensor in decoded.scene.sensors] == ["lidar_1", "lidar_2"]


def test_observation_decoder_rejects_unknown_fields() -> None:
    document = _record().to_document()
    document["unknown"] = True

    with pytest.raises(ObservationFormatError, match="unexpected fields"):
        decode_observation_line(json.dumps(document))


def test_observation_decoder_rejects_duplicate_fields() -> None:
    encoded = encode_observation_line(_record())
    duplicate = encoded[:-1] + ',"run_id":"run-b"}'

    with pytest.raises(ObservationFormatError, match="invalid observation JSON"):
        decode_observation_line(duplicate)


def test_observation_frame_enforces_the_wire_size_bound() -> None:
    with pytest.raises(ObservationFormatError, match="exceeds 100 bytes"):
        encode_observation_frame(_record(), max_line_bytes=100)


def _publisher(port: int) -> TcpObservationPublisher:
    return TcpObservationPublisher(
        host="127.0.0.1",
        port=port,
        environment_id="synthetic-scrap-pit-v1",
        run_id="run-a",
        input_fingerprint_sha256="0" * 64,
        seed=42,
        scene=_SCENE,
        interval_s=1.0,
        connect_timeout_s=0.2,
        send_timeout_s=0.2,
        reconnect_initial_delay_s=0.01,
        reconnect_max_delay_s=0.02,
    )


def test_publisher_streams_due_records_as_json_lines() -> None:
    async def run() -> None:
        received: list[str] = []
        complete = asyncio.Event()

        async def receive(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            while len(received) < 3:
                received.append((await reader.readline()).decode("utf-8"))
            complete.set()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(receive, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        publisher = _publisher(port)
        await publisher.start()
        assert publisher.is_due(0.1)
        assert publisher.publish(_record(0.1).snapshot)
        while publisher.stats.sent_records < 1:
            await asyncio.sleep(0)
        assert not publisher.is_due(0.2)
        assert publisher.is_due(1.0)
        assert publisher.publish(_record(1.0).snapshot)
        await asyncio.wait_for(complete.wait(), timeout=1.0)
        await publisher.close()
        server.close()
        await server.wait_closed()

        documents = [json.loads(line) for line in received]
        assert [document["type"] for document in documents] == [
            "load_model_stream_header",
            "load_model_observation",
            "load_model_observation",
        ]
        assert [document["scenario"]["elapsed_s"] for document in documents[1:]] == [0.1, 1.0]
        assert [document["sequence"] for document in documents[1:]] == [1, 2]
        assert publisher.stats.accepted_records == 2
        assert publisher.stats.sent_records == 2
        assert publisher.stats.dropped_records == 0

    asyncio.run(run())


def test_publisher_keeps_only_latest_pending_snapshot() -> None:
    async def run() -> None:
        received = asyncio.Future[tuple[str, str]]()

        async def receive(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            header = (await reader.readline()).decode("utf-8")
            observation = (await reader.readline()).decode("utf-8")
            received.set_result((header, observation))
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(receive, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        publisher = _publisher(port)
        assert publisher.publish(_record(0.1).snapshot)
        assert publisher.publish(_record(1.0).snapshot)
        await publisher.start()
        header_line, line = await asyncio.wait_for(received, timeout=1.0)
        await publisher.close()
        server.close()
        await server.wait_closed()

        assert json.loads(header_line)["type"] == "load_model_stream_header"
        assert json.loads(line)["scenario"]["elapsed_s"] == 1.0
        assert json.loads(line)["sequence"] == 2
        assert publisher.stats.dropped_records == 1

    asyncio.run(run())


def test_connection_failure_is_isolated_and_shutdown_is_prompt() -> None:
    async def run() -> None:
        server = await asyncio.start_server(lambda _reader, _writer: None, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()
        publisher = _publisher(port)
        await publisher.start()
        assert publisher.publish(_record().snapshot)
        async with asyncio.timeout(1.0):
            while publisher.stats.connection_failures == 0:
                await asyncio.sleep(0.001)
        await asyncio.wait_for(publisher.close(), timeout=0.1)

        assert publisher.stats.sent_records == 0
        assert publisher.stats.dropped_records == 1

    asyncio.run(run())
