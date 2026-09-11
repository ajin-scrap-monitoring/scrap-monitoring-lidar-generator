"""Receiver test-double integration tests for the scan sender state machine."""

import asyncio
from collections.abc import Awaitable, Callable

import numpy as np
import pytest

from scrap_monitoring_lidar_generator.measurement import MeasuredScan
from scrap_monitoring_lidar_generator.transport import (
    AckMessage,
    AsyncScanSender,
    ErrorCode,
    ErrorMessage,
    ScanIdentity,
    ScanMessage,
    SenderHaltCode,
    decode_scan_message,
    encode_frame,
    encode_response_message,
)

type ServerHandler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


def _message(scan_id: int = 1) -> ScanMessage:
    return ScanMessage(
        environment_id="environment-a",
        run_id="run-a",
        scan_id=scan_id,
        captured_at=1_800_000_000_000_000 + scan_id,
        measured_scan=MeasuredScan(
            sensor_id="sensor-a",
            angles_deg=np.asarray([0.0], dtype=np.float64),
            distances_m=np.asarray([2.5], dtype=np.float64),
            qualities=np.asarray([64], dtype=np.uint8),
        ),
    )


def _identity(scan_id: int = 1) -> ScanIdentity:
    return ScanIdentity(run_id="run-a", sensor_id="sensor-a", scan_id=scan_id)


def _sender(host: str, port: int, *, ack_timeout_s: float = 0.05) -> AsyncScanSender:
    return AsyncScanSender(
        host=host,
        port=port,
        max_body_bytes=1_048_576,
        buffer_max_age_s=2.0,
        buffer_max_bytes=1_048_580,
        connect_timeout_s=0.05,
        send_timeout_s=0.05,
        ack_timeout_s=ack_timeout_s,
        reconnect_initial_delay_s=0.001,
        reconnect_max_delay_s=0.002,
        seed=123,
    )


async def _read_frame_body(reader: asyncio.StreamReader) -> bytes:
    body_size = int.from_bytes(await reader.readexactly(4), "big")
    return await reader.readexactly(body_size)


async def _send_response(writer: asyncio.StreamWriter, response: AckMessage | ErrorMessage) -> None:
    writer.write(encode_frame(encode_response_message(response)))
    await writer.drain()


async def _wait_until(predicate: Callable[[], bool], timeout_s: float = 1.0) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(poll(), timeout=timeout_s)


async def _stop(sender: AsyncScanSender, task: asyncio.Task[None]) -> None:
    sender.request_stop()
    await asyncio.wait_for(task, timeout=1.0)


async def _run_with_server(
    handler: ServerHandler,
    scenario: Callable[[str, int], Awaitable[None]],
) -> None:
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    socket = server.sockets[0]
    host, port = socket.getsockname()[:2]
    async with server:
        await scenario(str(host), int(port))
    await server.wait_closed()


def test_sender_delivers_scan_and_removes_only_matching_ack() -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        first = decode_scan_message(await _read_frame_body(reader))
        await _send_response(
            writer,
            AckMessage(identity=ScanIdentity(first.run_id, first.sensor_id, first.scan_id + 1)),
        )
        await _send_response(writer, AckMessage(identity=_identity(first.scan_id)))
        second = decode_scan_message(await _read_frame_body(reader))
        await _send_response(writer, AckMessage(identity=_identity(second.scan_id)))
        await reader.read()
        writer.close()
        await writer.wait_closed()

    async def scenario(host: str, port: int) -> None:
        sender = _sender(host, port)
        sender.enqueue_scan(_message(1))
        sender.enqueue_scan(_message(2))
        task = asyncio.create_task(sender.run())
        await _wait_until(lambda: sender.pending_frames == 0)
        await _stop(sender, task)

        assert sender.stats.enqueued_frames == 2
        assert sender.stats.sent_frames == 2
        assert sender.stats.acknowledged_frames == 2
        assert sender.stats.connection_failures == 0

    asyncio.run(_run_with_server(handler, scenario))


def test_sender_reconnects_with_identical_frame_after_connection_loss() -> None:
    received: list[bytes] = []

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        body = await _read_frame_body(reader)
        received.append(body)
        if len(received) == 1:
            writer.close()
            await writer.wait_closed()
            return
        await _send_response(writer, AckMessage(identity=_identity()))
        await reader.read()
        writer.close()
        await writer.wait_closed()

    async def scenario(host: str, port: int) -> None:
        sender = _sender(host, port)
        sender.enqueue_scan(_message())
        task = asyncio.create_task(sender.run())
        await _wait_until(lambda: sender.pending_frames == 0)
        await _stop(sender, task)

        assert received[0] == received[1]
        assert sender.stats.sent_frames == 2
        assert sender.stats.acknowledged_frames == 1
        assert sender.stats.connection_failures == 1

    asyncio.run(_run_with_server(handler, scenario))


def test_sender_reconnects_after_ack_timeout() -> None:
    connection_count = 0

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal connection_count
        connection_count += 1
        await _read_frame_body(reader)
        if connection_count == 1:
            await reader.read()
        else:
            await _send_response(writer, AckMessage(identity=_identity()))
            await reader.read()
        writer.close()
        await writer.wait_closed()

    async def scenario(host: str, port: int) -> None:
        sender = _sender(host, port, ack_timeout_s=0.01)
        sender.enqueue_scan(_message())
        task = asyncio.create_task(sender.run())
        await _wait_until(lambda: sender.pending_frames == 0)
        await _stop(sender, task)

        assert connection_count == 2
        assert sender.stats.connection_failures == 1

    asyncio.run(_run_with_server(handler, scenario))


def test_sender_accepts_new_scan_while_waiting_for_ack() -> None:
    first_received = asyncio.Event()
    release_first_ack = asyncio.Event()

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        first = decode_scan_message(await _read_frame_body(reader))
        first_received.set()
        await release_first_ack.wait()
        await _send_response(writer, AckMessage(identity=_identity(first.scan_id)))
        second = decode_scan_message(await _read_frame_body(reader))
        await _send_response(writer, AckMessage(identity=_identity(second.scan_id)))
        await reader.read()
        writer.close()
        await writer.wait_closed()

    async def scenario(host: str, port: int) -> None:
        sender = _sender(host, port)
        sender.enqueue_scan(_message(1))
        task = asyncio.create_task(sender.run())
        await asyncio.wait_for(first_received.wait(), timeout=1.0)

        sender.enqueue_scan(_message(2))
        assert sender.pending_frames == 2
        release_first_ack.set()

        await _wait_until(lambda: sender.pending_frames == 0)
        await _stop(sender, task)
        assert sender.stats.enqueued_frames == 2
        assert sender.stats.sent_frames == 2
        assert sender.stats.acknowledged_frames == 2

    asyncio.run(_run_with_server(handler, scenario))


def test_temporary_unavailable_reconnects_without_removing_scan() -> None:
    connection_count = 0

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal connection_count
        connection_count += 1
        await _read_frame_body(reader)
        if connection_count == 1:
            await _send_response(writer, ErrorMessage(code=ErrorCode.TEMPORARY_UNAVAILABLE))
        else:
            await _send_response(writer, AckMessage(identity=_identity()))
            await reader.read()
        writer.close()
        await writer.wait_closed()

    async def scenario(host: str, port: int) -> None:
        sender = _sender(host, port)
        sender.enqueue_scan(_message())
        task = asyncio.create_task(sender.run())
        await _wait_until(lambda: sender.pending_frames == 0)
        await _stop(sender, task)

        assert sender.stats.sent_frames == 2
        assert sender.stats.connection_failures == 1
        assert sender.stats.acknowledged_frames == 1

    asyncio.run(_run_with_server(handler, scenario))


@pytest.mark.parametrize(
    ("response", "expected_halt", "expected_rejected", "expected_pending"),
    [
        (
            ErrorMessage(code=ErrorCode.INVALID_SCAN, identity=_identity()),
            None,
            1,
            0,
        ),
        (
            ErrorMessage(
                code=ErrorCode.ENVIRONMENT_MISMATCH,
                message="wrong environment",
                identity=_identity(),
            ),
            SenderHaltCode.ENVIRONMENT_MISMATCH,
            0,
            1,
        ),
        (
            ErrorMessage(code=ErrorCode.UNSUPPORTED_VERSION, message="version 1 required"),
            SenderHaltCode.UNSUPPORTED_VERSION,
            0,
            1,
        ),
    ],
)
def test_sender_applies_non_temporary_error_policy(
    response: ErrorMessage,
    expected_halt: SenderHaltCode | None,
    expected_rejected: int,
    expected_pending: int,
) -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _read_frame_body(reader)
        await _send_response(writer, response)
        await reader.read()
        writer.close()
        await writer.wait_closed()

    async def scenario(host: str, port: int) -> None:
        sender = _sender(host, port)
        sender.enqueue_scan(_message())
        task = asyncio.create_task(sender.run())
        if expected_halt is None:
            await _wait_until(lambda: sender.pending_frames == 0)
        else:
            await _wait_until(lambda: sender.halt is not None)
        await _stop(sender, task)

        assert sender.pending_frames == expected_pending
        assert sender.stats.rejected_frames == expected_rejected
        actual_halt = None if sender.halt is None else sender.halt.code
        assert actual_halt == expected_halt

    asyncio.run(_run_with_server(handler, scenario))


def test_malformed_response_halts_transport_without_removing_scan() -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _read_frame_body(reader)
        writer.write(encode_frame(b"not-messagepack"))
        await writer.drain()
        await reader.read()
        writer.close()
        await writer.wait_closed()

    async def scenario(host: str, port: int) -> None:
        sender = _sender(host, port)
        sender.enqueue_scan(_message())
        task = asyncio.create_task(sender.run())
        await _wait_until(lambda: sender.halt is not None)
        await _stop(sender, task)

        assert sender.halt is not None
        assert sender.halt.code is SenderHaltCode.PROTOCOL_ERROR
        assert sender.pending_frames == 1

    asyncio.run(_run_with_server(handler, scenario))
