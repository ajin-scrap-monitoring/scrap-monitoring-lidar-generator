"""Loopback integration tests for async framed TCP delivery."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import cast

import pytest

from scrap_monitoring_lidar_generator.transport import (
    AckMessage,
    AsyncFramedTcpConnection,
    FrameError,
    ScanIdentity,
    TcpConnectionClosedError,
    decode_response_message,
    encode_frame,
    encode_response_message,
)

type ServerHandler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


class _BlockedWriter:
    def __init__(self) -> None:
        self.written = b""
        self.closing = False

    def write(self, data: bytes) -> None:
        self.written += data

    async def drain(self) -> None:
        await asyncio.Event().wait()

    def close(self) -> None:
        self.closing = True

    async def wait_closed(self) -> None:
        return None

    def is_closing(self) -> bool:
        return self.closing


async def _with_server(
    handler: ServerHandler,
    client: Callable[[str, int], Awaitable[None]],
) -> None:
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    socket = server.sockets[0]
    host, port = socket.getsockname()[:2]
    async with server:
        await client(str(host), int(port))
    await server.wait_closed()


def test_connection_sends_complete_frame_and_recovers_split_ack() -> None:
    identity = ScanIdentity(run_id="run-a", sensor_id="sensor-a", scan_id=1)
    scan_body = b"scan-body"
    ack_frame = encode_frame(encode_response_message(AckMessage(identity=identity)))

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        body_size = int.from_bytes(await reader.readexactly(4), "big")
        assert await reader.readexactly(body_size) == scan_body
        for byte in ack_frame:
            writer.write(bytes([byte]))
            await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def client(host: str, port: int) -> None:
        async with await AsyncFramedTcpConnection.connect(
            host=host,
            port=port,
            timeout_s=1.0,
        ) as connection:
            await connection.send_frame(encode_frame(scan_body), timeout_s=1.0)
            response = decode_response_message(await connection.receive_body(timeout_s=1.0))
            assert response == AckMessage(identity=identity)

    asyncio.run(_with_server(handler, client))


def test_connection_retains_concatenated_response_bodies() -> None:
    identities = (
        ScanIdentity(run_id="run-a", sensor_id="sensor-a", scan_id=1),
        ScanIdentity(run_id="run-a", sensor_id="sensor-a", scan_id=2),
    )
    frames = b"".join(
        encode_frame(encode_response_message(AckMessage(identity=identity)))
        for identity in identities
    )

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        del reader
        writer.write(frames)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def client(host: str, port: int) -> None:
        connection = await AsyncFramedTcpConnection.connect(
            host=host,
            port=port,
            timeout_s=1.0,
        )
        try:
            responses = []
            for _ in identities:
                body = await connection.receive_body(timeout_s=1.0)
                responses.append(decode_response_message(body))
            assert responses == [AckMessage(identity=identity) for identity in identities]
        finally:
            await connection.close()

    asyncio.run(_with_server(handler, client))


def test_connection_closes_and_discards_partial_response() -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        del reader
        writer.write(b"\x00\x00\x00\x05ab")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def client(host: str, port: int) -> None:
        connection = await AsyncFramedTcpConnection.connect(
            host=host,
            port=port,
            timeout_s=1.0,
        )
        with pytest.raises(TcpConnectionClosedError):
            await connection.receive_body(timeout_s=1.0)
        assert connection.is_closing is True

    asyncio.run(_with_server(handler, client))


def test_connection_closes_on_oversized_response_prefix() -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        del reader
        writer.write(b"\x00\x00\x00\x05")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def client(host: str, port: int) -> None:
        connection = await AsyncFramedTcpConnection.connect(
            host=host,
            port=port,
            timeout_s=1.0,
            max_body_bytes=4,
        )
        with pytest.raises(FrameError, match="exceeds"):
            await connection.receive_body(timeout_s=1.0)
        assert connection.is_closing is True

    asyncio.run(_with_server(handler, client))


def test_receive_timeout_closes_connection() -> None:
    release_server = asyncio.Event()

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        del reader
        await release_server.wait()
        writer.close()
        await writer.wait_closed()

    async def client(host: str, port: int) -> None:
        connection = await AsyncFramedTcpConnection.connect(
            host=host,
            port=port,
            timeout_s=1.0,
        )
        with pytest.raises(TimeoutError):
            await connection.receive_body(timeout_s=0.001)
        assert connection.is_closing is True
        release_server.set()

    asyncio.run(_with_server(handler, client))


def test_connect_timeout_cancels_connection_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    async def blocked_open_connection(
        host: str, port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        del host, port
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(asyncio, "open_connection", blocked_open_connection)

    async def run() -> None:
        with pytest.raises(TimeoutError):
            await AsyncFramedTcpConnection.connect(
                host="receiver",
                port=9000,
                timeout_s=0.001,
            )

    asyncio.run(run())


def test_send_timeout_closes_connection_after_partial_write() -> None:
    async def run() -> None:
        writer = _BlockedWriter()
        connection = AsyncFramedTcpConnection(
            reader=asyncio.StreamReader(),
            writer=cast(asyncio.StreamWriter, writer),
        )
        frame = encode_frame(b"scan")

        with pytest.raises(TimeoutError):
            await connection.send_frame(frame, timeout_s=0.001)

        assert writer.written == frame
        assert writer.closing is True
        with pytest.raises(TcpConnectionClosedError, match="closing"):
            await connection.send_frame(frame, timeout_s=1.0)

    asyncio.run(run())
