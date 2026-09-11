"""Async TCP adapter for complete framed messages."""

import asyncio
import math
from collections import deque
from contextlib import suppress
from typing import Self

from scrap_monitoring_lidar_generator.transport.framing import (
    DEFAULT_MAX_MESSAGE_BODY_BYTES,
    FrameDecoder,
    validate_frame,
    validate_max_body_bytes,
)

_READ_CHUNK_BYTES = 65_536


class TcpConnectionClosedError(ConnectionError):
    """The peer closed before the requested operation completed."""


class AsyncFramedTcpConnection:
    """Send complete frames and recover MessagePack bodies on one connection."""

    __slots__ = ("_decoder", "_max_body_bytes", "_pending_bodies", "_reader", "_writer")

    def __init__(
        self,
        *,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        max_body_bytes: int = DEFAULT_MAX_MESSAGE_BODY_BYTES,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._decoder = FrameDecoder(max_body_bytes=max_body_bytes)
        self._max_body_bytes = max_body_bytes
        self._pending_bodies: deque[bytes] = deque()

    @classmethod
    async def connect(
        cls,
        *,
        host: str,
        port: int,
        timeout_s: float,
        max_body_bytes: int = DEFAULT_MAX_MESSAGE_BODY_BYTES,
    ) -> Self:
        """Open one TCP connection within the monotonic timeout."""
        if not isinstance(host, str) or not host:
            raise ValueError("TCP host must be a non-empty string")
        if type(port) is not int or not 1 <= port <= 65_535:
            raise ValueError("TCP port must be an integer in [1, 65535]")
        timeout = _validate_timeout(timeout_s)
        validate_max_body_bytes(max_body_bytes)
        async with asyncio.timeout(timeout):
            reader, writer = await asyncio.open_connection(host, port)
        return cls(reader=reader, writer=writer, max_body_bytes=max_body_bytes)

    @property
    def is_closing(self) -> bool:
        """Return whether this connection has begun closing."""
        return self._writer.is_closing()

    async def send_frame(self, frame: bytes, *, timeout_s: float) -> None:
        """Send one validated complete frame or close the connection."""
        self._require_open()
        validate_frame(frame, max_body_bytes=self._max_body_bytes)
        timeout = _validate_timeout(timeout_s)
        try:
            self._writer.write(frame)
            async with asyncio.timeout(timeout):
                await self._writer.drain()
        except BaseException:
            await self.close()
            raise

    async def receive_body(self, *, timeout_s: float) -> bytes:
        """Receive one complete body while retaining additional decoded bodies."""
        self._require_open()
        timeout = _validate_timeout(timeout_s)
        if self._pending_bodies:
            return self._pending_bodies.popleft()
        try:
            async with asyncio.timeout(timeout):
                while True:
                    data = await self._reader.read(_READ_CHUNK_BYTES)
                    if not data:
                        raise TcpConnectionClosedError(
                            "TCP peer closed before a complete frame arrived"
                        )
                    self._pending_bodies.extend(self._decoder.feed(data))
                    if self._pending_bodies:
                        return self._pending_bodies.popleft()
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        """Close the socket and discard connection-specific receive state."""
        self._decoder.reset()
        self._pending_bodies.clear()
        self._writer.close()
        with suppress(OSError):
            await self._writer.wait_closed()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    def _require_open(self) -> None:
        if self._writer.is_closing():
            raise TcpConnectionClosedError("TCP connection is closing")


def _validate_timeout(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value <= 0.0
    ):
        raise ValueError("TCP timeout must be finite and positive")
    return float(value)
