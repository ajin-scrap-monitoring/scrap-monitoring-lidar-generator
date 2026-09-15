import argparse
import asyncio
import hashlib
import json
import stat
from pathlib import Path
from typing import Any, cast

import pytest

from .observation_receiver import (
    ObservationExpectation,
    ObservationReceiver,
    ReceiverError,
    _atomic_write_json,
    run_receiver,
)


def _header() -> dict[str, object]:
    return {
        "observation_version": 1,
        "type": "load_model_stream_header",
        "environment_id": "synthetic-scrap-pit-v1",
        "run_id": "test-run",
        "input_fingerprint_sha256": "0" * 64,
        "seed": 42,
        "scene": {
            "coordinate_system": "right-handed-z-up",
            "length_unit": "m",
            "angle_unit": "deg",
            "boundary_xy_m": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]],
            "floor_z_m": 0.0,
            "top_z_m": 1.0,
            "inlet_positions_xy_m": [[0.5, 0.5]],
            "sensors": [
                {
                    "sensor_id": "lidar_1",
                    "p0_m": [0.0, 0.0, 1.0],
                    "u0": [0.0, 0.0, -1.0],
                    "u90": [1.0, 0.0, 0.0],
                },
                {
                    "sensor_id": "lidar_2",
                    "p0_m": [1.0, 1.0, 1.0],
                    "u0": [0.0, 0.0, -1.0],
                    "u90": [-1.0, 0.0, 0.0],
                },
            ],
        },
    }


def _observation(sequence: int, *, elapsed_s: float | None = None) -> dict[str, object]:
    if elapsed_s is None:
        elapsed_s = 0.1 if sequence == 1 else float(sequence - 1)
    return {
        "observation_version": 1,
        "type": "load_model_observation",
        "sequence": sequence,
        "run_id": "test-run",
        "scenario": {
            "elapsed_s": elapsed_s,
            "surface_updated_at_s": elapsed_s,
            "cycle_index": 0,
            "phase": "filling",
            "phase_started_at_s": 0.0,
            "phase_ends_at_s": 10.0,
            "phase_duration_s": 10.0,
            "rate_factor": 1.0,
            "target_fill_ratio": 0.9,
            "surface_fill_ratio": 0.2 * sequence,
            "surface_volume_m3": 0.1 * sequence,
            "current_inlet_index": 0,
        },
        "surface": {
            "cell_size_m": 1.0,
            "x_coordinates_m": [0.0, 1.0],
            "y_coordinates_m": [0.0, 1.0],
            "heights_m": [
                [0.0, 0.1 * sequence],
                [0.2 * sequence, 0.3 * sequence],
            ],
        },
    }


def _surface_stream_sha256(*documents: dict[str, object]) -> str:
    hasher = hashlib.sha256()
    for document in documents:
        sequence = cast(int, document["sequence"])
        payload = json.dumps(
            document["surface"],
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        hasher.update(sequence.to_bytes(8, byteorder="big"))
        hasher.update(len(payload).to_bytes(8, byteorder="big"))
        hasher.update(payload)
    return hasher.hexdigest()


def _expectation(expected_observations: int = 1) -> ObservationExpectation:
    last_elapsed_s = 0.1 if expected_observations == 1 else float(expected_observations - 1)
    return ObservationExpectation.from_header(
        _header(),
        generator_source_commit="1" * 40,
        expected_observations=expected_observations,
        first_elapsed_s=0.1,
        last_elapsed_s=last_elapsed_s,
        interval_s=1.0,
        cell_size_m=1.0,
    )


async def _send(port: int, documents: list[dict[str, object]]) -> None:
    _reader, writer = await asyncio.open_connection("127.0.0.1", port)
    for document in documents:
        writer.write(json.dumps(document).encode() + b"\n")
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def _wait_for_connections(receiver: ObservationReceiver, expected: int) -> None:
    for _ in range(100):
        if receiver.completed_connections == expected or receiver.failure is not None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("receiver did not finish the expected connection")


async def _wait_for_failure(receiver: ObservationReceiver) -> None:
    for _ in range(100):
        if receiver.failure is not None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("receiver did not report the expected failure")


def test_receiver_counts_records_across_matching_reconnect_headers() -> None:
    async def exercise() -> dict[str, object]:
        receiver = ObservationReceiver(
            expectation=_expectation(2), max_observations=4, max_connections=2
        )
        server = await asyncio.start_server(receiver.handle, "127.0.0.1", 0)
        assert server.sockets is not None
        port = int(server.sockets[0].getsockname()[1])
        try:
            await _send(port, [_header(), _observation(1)])
            await _wait_for_connections(receiver, 1)
            await _send(port, [_header(), _observation(2)])
            await _wait_for_connections(receiver, 2)
        finally:
            server.close()
            await server.wait_closed()
        return receiver.result()

    first = _observation(1)
    second = _observation(2)
    assert asyncio.run(exercise()) == {
        "schema_version": "long-validation-observation-result.v1",
        "run_id": "test-run",
        "generator_source_commit": "1" * 40,
        "environment_id": "synthetic-scrap-pit-v1",
        "input_fingerprint_sha256": "0" * 64,
        "seed": 42,
        "scene_fingerprint_sha256": _expectation(2).scene_fingerprint_sha256,
        "received_records": 2,
        "first_sequence": 1,
        "last_sequence": 2,
        "first_elapsed_s": 0.1,
        "last_elapsed_s": 1.0,
        "surface_stream_sha256": _surface_stream_sha256(first, second),
        "surface_change_count": 1,
        "minimum_surface_volume_m3": 0.1,
        "maximum_surface_volume_m3": 0.2,
    }


def test_receiver_rejects_observation_as_first_record() -> None:
    async def exercise() -> ReceiverError | None:
        receiver = ObservationReceiver(
            expectation=_expectation(), max_observations=4, max_connections=2
        )
        server = await asyncio.start_server(receiver.handle, "127.0.0.1", 0)
        assert server.sockets is not None
        port = int(server.sockets[0].getsockname()[1])
        try:
            await _send(port, [_observation(1)])
            await _wait_for_failure(receiver)
        finally:
            server.close()
            await server.wait_closed()
        return receiver.failure

    error = asyncio.run(exercise())
    assert error is not None
    assert "header" in str(error)


def test_receiver_rejects_duplicate_sequence_and_changed_header() -> None:
    receiver = ObservationReceiver(
        expectation=_expectation(), max_observations=4, max_connections=2
    )
    receiver._accept_header(_header())
    receiver._accept_observation(_observation(1))
    with pytest.raises(ReceiverError, match="not continuous"):
        receiver._accept_observation(_observation(1))
    changed = _header()
    changed["seed"] = 43
    with pytest.raises(ReceiverError, match="expected public input"):
        receiver._accept_header(changed)


def test_receiver_rejects_sequence_gap_and_wrong_simulation_cadence() -> None:
    receiver = ObservationReceiver(
        expectation=_expectation(2), max_observations=4, max_connections=2
    )
    receiver._accept_header(_header())
    with pytest.raises(ReceiverError, match="not continuous"):
        receiver._accept_observation(_observation(2))

    receiver._accept_observation(_observation(1))
    with pytest.raises(ReceiverError, match="one-second cadence"):
        receiver._accept_observation(_observation(2, elapsed_s=1.1))


def test_receiver_rejects_surface_outside_public_grid_and_height_bounds() -> None:
    receiver = ObservationReceiver(
        expectation=_expectation(2), max_observations=4, max_connections=2
    )
    receiver._accept_header(_header())
    wrong_grid = _observation(1)
    wrong_grid_surface = cast(dict[str, Any], wrong_grid["surface"])
    wrong_grid_surface["x_coordinates_m"] = [0.0, 0.9]
    with pytest.raises(ReceiverError, match="grid differs"):
        receiver._accept_observation(wrong_grid)

    out_of_bounds = _observation(1)
    out_of_bounds_surface = cast(dict[str, Any], out_of_bounds["surface"])
    out_of_bounds_surface["heights_m"] = [[0.0, 0.1], [0.2, 1.1]]
    with pytest.raises(ReceiverError, match="outside the scene bounds"):
        receiver._accept_observation(out_of_bounds)


def test_receiver_rejects_stale_surface_and_wrong_phase_volume_direction() -> None:
    stale_receiver = ObservationReceiver(
        expectation=_expectation(2), max_observations=4, max_connections=2
    )
    stale_receiver._accept_header(_header())
    first = _observation(1)
    stale_receiver._accept_observation(first)
    stale = _observation(2)
    cast(dict[str, Any], stale["surface"])["heights_m"] = cast(dict[str, Any], first["surface"])[
        "heights_m"
    ]
    with pytest.raises(ReceiverError, match="payload did not change"):
        stale_receiver._accept_observation(stale)

    regressing_receiver = ObservationReceiver(
        expectation=_expectation(2), max_observations=4, max_connections=2
    )
    regressing_receiver._accept_header(_header())
    regressing_receiver._accept_observation(_observation(1))
    regressing = _observation(2)
    scenario = cast(dict[str, Any], regressing["scenario"])
    scenario["surface_volume_m3"] = 0.05
    scenario["surface_fill_ratio"] = 0.1
    with pytest.raises(ReceiverError, match="did not increase"):
        regressing_receiver._accept_observation(regressing)


def test_receiver_rejects_nested_records_outside_version_one_contract() -> None:
    receiver = ObservationReceiver(
        expectation=_expectation(), max_observations=4, max_connections=2
    )
    invalid_header = _header()
    scene = cast(dict[str, Any], invalid_header["scene"])
    sensors = cast(list[dict[str, Any]], scene["sensors"])
    del sensors[0]["u90"]
    with pytest.raises(ReceiverError, match="version 1 contract"):
        receiver._accept_header(invalid_header)

    receiver._accept_header(_header())
    invalid_observation = _observation(1)
    surface = cast(dict[str, Any], invalid_observation["surface"])
    del surface["heights_m"]
    with pytest.raises(ReceiverError, match="version 1 contract"):
        receiver._accept_observation(invalid_observation)


def test_receiver_requires_completed_nonempty_delivery() -> None:
    receiver = ObservationReceiver(
        expectation=_expectation(), max_observations=1, max_connections=1
    )
    with pytest.raises(ReceiverError, match="did not complete"):
        receiver.result()
    receiver.active_connections = 1
    with pytest.raises(ReceiverError, match="active"):
        receiver.result()

    incomplete = ObservationReceiver(
        expectation=_expectation(2), max_observations=2, max_connections=1
    )
    incomplete._accept_header(_header())
    incomplete._accept_observation(_observation(1))
    incomplete.completed_connections = 1
    with pytest.raises(ReceiverError, match="exact expected"):
        incomplete.result()


def test_receiver_cli_boundary_stops_from_coordinator_file(tmp_path: Path) -> None:
    async def exercise() -> dict[str, object]:
        ready = tmp_path / "ready.json"
        stop = tmp_path / "stop"
        arguments = argparse.Namespace(
            bind_host="127.0.0.1",
            port=0,
            ready_file=ready,
            stop_file=stop,
            output=tmp_path / "result.json",
            max_observations=4,
            max_connections=2,
        )
        task = asyncio.create_task(run_receiver(arguments, expectation=_expectation()))
        for _ in range(100):
            if ready.is_file():
                break
            await asyncio.sleep(0.01)
        ready_document = json.loads(ready.read_text(encoding="utf-8"))
        await _send(int(ready_document["port"]), [_header(), _observation(1)])
        stop.touch()
        return await task

    assert asyncio.run(exercise())["received_records"] == 1


def test_observation_result_is_created_once_with_public_read_mode(tmp_path: Path) -> None:
    output = tmp_path / "observation-result.json"
    document = {
        "schema_version": "long-validation-observation-result.v1",
        "run_id": "test-run",
        "generator_source_commit": "1" * 40,
        "environment_id": "synthetic-scrap-pit-v1",
        "input_fingerprint_sha256": "0" * 64,
        "seed": 42,
        "scene_fingerprint_sha256": _expectation().scene_fingerprint_sha256,
        "received_records": 1,
        "first_sequence": 1,
        "last_sequence": 1,
        "first_elapsed_s": 0.1,
        "last_elapsed_s": 0.1,
        "surface_stream_sha256": _surface_stream_sha256(_observation(1)),
        "surface_change_count": 0,
        "minimum_surface_volume_m3": 0.1,
        "maximum_surface_volume_m3": 0.1,
    }
    _atomic_write_json(output, document, mode=0o644)
    assert stat.S_IMODE(output.stat().st_mode) == 0o644
    with pytest.raises(ReceiverError, match="already exists"):
        _atomic_write_json(output, document, mode=0o644)
