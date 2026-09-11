"""Async scan delivery state machine with bounded recovery."""

import asyncio
import math
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum

from scrap_monitoring_lidar_generator.transport.backoff import ReconnectBackoff
from scrap_monitoring_lidar_generator.transport.codec import encode_scan_message
from scrap_monitoring_lidar_generator.transport.framing import (
    FrameError,
    encode_frame,
    validate_max_body_bytes,
)
from scrap_monitoring_lidar_generator.transport.models import ScanMessage
from scrap_monitoring_lidar_generator.transport.responses import (
    AckMessage,
    ErrorCode,
    ErrorMessage,
    ResponseCodecError,
    ScanIdentity,
    decode_response_message,
)
from scrap_monitoring_lidar_generator.transport.tcp import AsyncFramedTcpConnection
from scrap_monitoring_lidar_generator.transport.unacked import (
    BufferDiscardReason,
    BufferEnqueueResult,
    DiscardedFrame,
    UnackedFrameBuffer,
)


class SenderHaltCode(StrEnum):
    """Non-retryable reason that network delivery stopped."""

    ENVIRONMENT_MISMATCH = "environment_mismatch"
    UNSUPPORTED_VERSION = "unsupported_version"
    PROTOCOL_ERROR = "protocol_error"


@dataclass(frozen=True, slots=True)
class SenderHalt:
    """One non-retryable delivery state with an operator-facing detail."""

    code: SenderHaltCode
    detail: str


@dataclass(frozen=True, slots=True)
class SenderStats:
    """Cumulative scan delivery counters for logs and monitoring."""

    enqueued_frames: int
    sent_frames: int
    acknowledged_frames: int
    rejected_frames: int
    expired_frames: int
    capacity_discarded_frames: int
    oversized_frames: int
    connection_failures: int


@dataclass(slots=True)
class _MutableSenderStats:
    enqueued_frames: int = 0
    sent_frames: int = 0
    acknowledged_frames: int = 0
    rejected_frames: int = 0
    expired_frames: int = 0
    capacity_discarded_frames: int = 0
    oversized_frames: int = 0
    connection_failures: int = 0


class _ReconnectRequested(Exception):
    pass


class AsyncScanSender:
    """Deliver scans independently from generation with bounded retry state."""

    __slots__ = (
        "_ack_timeout_s",
        "_backoff",
        "_buffer",
        "_buffer_max_age_s",
        "_clock",
        "_connect_timeout_s",
        "_halt",
        "_host",
        "_max_body_bytes",
        "_port",
        "_running",
        "_send_timeout_s",
        "_stats",
        "_stop_event",
        "_stop_requested",
        "_wakeup",
    )

    def __init__(
        self,
        *,
        host: str,
        port: int,
        max_body_bytes: int,
        buffer_max_age_s: float,
        buffer_max_bytes: int,
        connect_timeout_s: float,
        send_timeout_s: float,
        ack_timeout_s: float,
        reconnect_initial_delay_s: float,
        reconnect_max_delay_s: float,
        seed: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(host, str) or not host:
            raise ValueError("sender host must be a non-empty string")
        if type(port) is not int or not 1 <= port <= 65_535:
            raise ValueError("sender port must be an integer in [1, 65535]")
        validate_max_body_bytes(max_body_bytes)
        self._host = host
        self._port = port
        self._max_body_bytes = max_body_bytes
        self._buffer = UnackedFrameBuffer(
            max_age_s=buffer_max_age_s,
            max_bytes=buffer_max_bytes,
        )
        self._buffer_max_age_s = float(buffer_max_age_s)
        self._connect_timeout_s = _require_positive_duration(
            connect_timeout_s, "sender connect timeout"
        )
        self._send_timeout_s = _require_positive_duration(send_timeout_s, "sender send timeout")
        self._ack_timeout_s = _require_positive_duration(
            ack_timeout_s, "sender acknowledgement timeout"
        )
        self._backoff = ReconnectBackoff(
            initial_delay_s=reconnect_initial_delay_s,
            maximum_delay_s=reconnect_max_delay_s,
            seed=seed,
        )
        self._clock = clock
        self._stats = _MutableSenderStats()
        self._halt: SenderHalt | None = None
        self._wakeup = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._stop_requested = False
        self._running = False

    @property
    def stats(self) -> SenderStats:
        """Return an immutable snapshot of cumulative delivery counters."""
        return SenderStats(
            enqueued_frames=self._stats.enqueued_frames,
            sent_frames=self._stats.sent_frames,
            acknowledged_frames=self._stats.acknowledged_frames,
            rejected_frames=self._stats.rejected_frames,
            expired_frames=self._stats.expired_frames,
            capacity_discarded_frames=self._stats.capacity_discarded_frames,
            oversized_frames=self._stats.oversized_frames,
            connection_failures=self._stats.connection_failures,
        )

    @property
    def halt(self) -> SenderHalt | None:
        """Return the non-retryable delivery state, if one was observed."""
        return self._halt

    @property
    def pending_frames(self) -> int:
        """Return the number of retained unacknowledged frames."""
        return len(self._buffer)

    @property
    def pending_bytes(self) -> int:
        """Return retained wire bytes including length prefixes."""
        return self._buffer.total_bytes

    def enqueue_scan(self, message: ScanMessage) -> BufferEnqueueResult:
        """Encode and retain one generated scan without waiting for the network."""
        if self._stop_requested:
            raise RuntimeError("scan sender has stopped")
        identity = ScanIdentity(
            run_id=message.run_id,
            sensor_id=message.sensor_id,
            scan_id=message.scan_id,
        )
        frame = encode_frame(
            encode_scan_message(message),
            max_body_bytes=self._max_body_bytes,
        )
        result = self._buffer.enqueue(identity=identity, frame=frame, now_s=self._clock())
        self._record_discards(result.discarded)
        if result.accepted:
            self._stats.enqueued_frames += 1
            self._wakeup.set()
        return result

    def request_stop(self) -> None:
        """Request a clean stop after the current timed operation returns."""
        self._stop_requested = True
        self._stop_event.set()
        self._wakeup.set()

    async def run(self) -> None:
        """Maintain one persistent connection until stopped or cancelled."""
        if self._running:
            raise RuntimeError("scan sender is already running")
        if self._stop_requested:
            return
        self._running = True
        connection: AsyncFramedTcpConnection | None = None
        try:
            while not self._stop_requested:
                self._expire()
                if self._halt is not None:
                    await self._wait_while_halted()
                    continue
                if not self._buffer.entries:
                    await self._wait_for_work()
                    continue
                reconnect = False
                try:
                    connection = await AsyncFramedTcpConnection.connect(
                        host=self._host,
                        port=self._port,
                        timeout_s=self._connect_timeout_s,
                        max_body_bytes=self._max_body_bytes,
                    )
                    await self._use_connection(connection)
                except _ReconnectRequested, OSError:
                    self._stats.connection_failures += 1
                    reconnect = True
                except (FrameError, ResponseCodecError) as error:
                    self._set_protocol_halt(error)
                finally:
                    if connection is not None:
                        await connection.close()
                        connection = None
                if reconnect and not self._stop_requested:
                    delay_s = self._backoff.next_delay_after_failure()
                    await self._wait_reconnect_delay(delay_s)
        finally:
            if connection is not None:
                await connection.close()
            self._running = False

    async def _use_connection(self, connection: AsyncFramedTcpConnection) -> None:
        while not self._stop_requested and self._halt is None:
            self._expire()
            entries = self._buffer.entries
            if not entries:
                await self._wait_for_work()
                continue
            current = entries[0]
            await connection.send_frame(current.frame, timeout_s=self._send_timeout_s)
            self._stats.sent_frames += 1
            if not self._buffer.contains(current.identity):
                continue
            await self._wait_for_ack(connection, current.identity, current.enqueued_at_s)

    async def _wait_for_ack(
        self,
        connection: AsyncFramedTcpConnection,
        identity: ScanIdentity,
        enqueued_at_s: float,
    ) -> None:
        ack_deadline_s = self._clock() + self._ack_timeout_s
        expiry_s = enqueued_at_s + self._buffer_max_age_s
        while self._buffer.contains(identity):
            self._expire()
            if not self._buffer.contains(identity):
                return
            remaining_s = min(ack_deadline_s, expiry_s) - self._clock()
            if remaining_s <= 0.0:
                raise TimeoutError("scan acknowledgement timed out")
            response = decode_response_message(await connection.receive_body(timeout_s=remaining_s))
            if isinstance(response, AckMessage):
                acknowledged = (
                    self._buffer.acknowledge(response.identity)
                    if response.identity == identity
                    else None
                )
                if acknowledged is not None:
                    self._stats.acknowledged_frames += 1
                    self._backoff.reset_after_ack()
            else:
                self._handle_error(response, identity)

    def _handle_error(self, response: ErrorMessage, current_identity: ScanIdentity) -> None:
        if response.code is ErrorCode.TEMPORARY_UNAVAILABLE:
            raise _ReconnectRequested
        if response.code is ErrorCode.INVALID_SCAN:
            assert response.identity is not None
            rejected = (
                self._buffer.acknowledge(response.identity)
                if response.identity == current_identity
                else None
            )
            if rejected is not None:
                self._stats.rejected_frames += 1
            return
        halt_code = (
            SenderHaltCode.ENVIRONMENT_MISMATCH
            if response.code is ErrorCode.ENVIRONMENT_MISMATCH
            else SenderHaltCode.UNSUPPORTED_VERSION
        )
        self._halt = SenderHalt(
            code=halt_code,
            detail=response.message or response.code.value,
        )
        self._wakeup.set()

    def _set_protocol_halt(self, error: Exception) -> None:
        self._halt = SenderHalt(code=SenderHaltCode.PROTOCOL_ERROR, detail=str(error))
        self._wakeup.set()

    def _expire(self) -> None:
        self._record_discards(self._buffer.expire(now_s=self._clock()))

    def _record_discards(self, discarded: tuple[DiscardedFrame, ...]) -> None:
        for item in discarded:
            if item.reason is BufferDiscardReason.EXPIRED:
                self._stats.expired_frames += 1
            elif item.reason is BufferDiscardReason.CAPACITY:
                self._stats.capacity_discarded_frames += 1
            else:
                self._stats.oversized_frames += 1

    async def _wait_for_work(self) -> None:
        self._wakeup.clear()
        if self._buffer.entries or self._stop_requested or self._halt is not None:
            return
        await self._wakeup.wait()

    async def _wait_reconnect_delay(self, delay_s: float) -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay_s)

    async def _wait_while_halted(self) -> None:
        self._wakeup.clear()
        entries = self._buffer.entries
        if not entries:
            await self._wakeup.wait()
            return
        oldest = entries[0]
        remaining_s = max(0.0, oldest.enqueued_at_s + self._buffer_max_age_s - self._clock())
        with suppress(TimeoutError):
            await asyncio.wait_for(self._wakeup.wait(), timeout=remaining_s)


def _require_positive_duration(value: float, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value <= 0.0
    ):
        raise ValueError(f"{name} must be finite and positive")
    return float(value)
