"""Best-effort TCP streaming for load-model observations."""

import asyncio
import math
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol

from scrap_monitoring_lidar_generator.observation.format import (
    ObservationRecord,
    encode_observation_line,
)
from scrap_monitoring_lidar_generator.scenario import ScenarioModelSnapshot
from scrap_monitoring_lidar_generator.transport import ReconnectBackoff

DEFAULT_OBSERVATION_HOST = "127.0.0.1"
DEFAULT_OBSERVATION_PORT = 9100
DEFAULT_OBSERVATION_INTERVAL_S = 1.0
MAX_OBSERVATION_INTERVAL_S = 86_400.0
_TIME_TOLERANCE_S = 1e-12


@dataclass(frozen=True, slots=True)
class ObservationPublisherStats:
    """Cumulative observation streaming counters."""

    accepted_records: int
    sent_records: int
    dropped_records: int
    connection_failures: int


class ObservationPublisher(Protocol):
    """Publish due immutable snapshots outside the scan delivery path."""

    @property
    def endpoint(self) -> str: ...

    @property
    def stats(self) -> ObservationPublisherStats: ...

    def is_due(self, elapsed_s: float) -> bool: ...

    def publish(self, snapshot: ScenarioModelSnapshot) -> bool: ...

    async def start(self) -> None: ...

    async def close(self) -> None: ...


class TcpObservationPublisher:
    """Keep and stream only the latest due observation record."""

    __slots__ = (
        "_accepted_records",
        "_backoff",
        "_closed",
        "_connect_timeout_s",
        "_connection_failures",
        "_dropped_records",
        "_environment_id",
        "_fingerprint",
        "_host",
        "_interval_s",
        "_last_elapsed_s",
        "_latest",
        "_next_sample_s",
        "_port",
        "_run_id",
        "_seed",
        "_send_timeout_s",
        "_sent_records",
        "_stop_event",
        "_task",
        "_wakeup",
    )

    def __init__(
        self,
        *,
        host: str,
        port: int,
        environment_id: str,
        run_id: str,
        input_fingerprint_sha256: str,
        seed: int,
        interval_s: float = DEFAULT_OBSERVATION_INTERVAL_S,
        connect_timeout_s: float,
        send_timeout_s: float,
        reconnect_initial_delay_s: float,
        reconnect_max_delay_s: float,
    ) -> None:
        if not isinstance(host, str) or not host:
            raise ValueError("observation host must be a non-empty string")
        if type(port) is not int or not 1 <= port <= 65_535:
            raise ValueError("observation port must be an integer in [1, 65535]")
        if not math.isfinite(interval_s) or not 0.0 < interval_s <= MAX_OBSERVATION_INTERVAL_S:
            raise ValueError("observation interval must be finite and between 0 and 86400 seconds")
        self._host = host
        self._port = port
        self._environment_id = environment_id
        self._run_id = run_id
        self._fingerprint = input_fingerprint_sha256
        self._seed = seed
        self._interval_s = float(interval_s)
        self._connect_timeout_s = _require_positive_duration(
            connect_timeout_s, "observation connect timeout"
        )
        self._send_timeout_s = _require_positive_duration(
            send_timeout_s, "observation send timeout"
        )
        self._backoff = ReconnectBackoff(
            initial_delay_s=reconnect_initial_delay_s,
            maximum_delay_s=reconnect_max_delay_s,
            seed=seed,
        )
        self._last_elapsed_s: float | None = None
        self._next_sample_s = 0.0
        self._latest: ObservationRecord | None = None
        self._accepted_records = 0
        self._sent_records = 0
        self._dropped_records = 0
        self._connection_failures = 0
        self._wakeup = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._closed = False
        self._task: asyncio.Task[None] | None = None

    @property
    def endpoint(self) -> str:
        """Return the configured observation TCP endpoint."""
        return f"{self._host}:{self._port}"

    @property
    def stats(self) -> ObservationPublisherStats:
        """Return an immutable snapshot of streaming counters."""
        return ObservationPublisherStats(
            accepted_records=self._accepted_records,
            sent_records=self._sent_records,
            dropped_records=self._dropped_records,
            connection_failures=self._connection_failures,
        )

    def is_due(self, elapsed_s: float) -> bool:
        """Advance the cadence and report whether a snapshot should be copied."""
        if self._closed:
            return False
        if not math.isfinite(elapsed_s):
            return False
        if (
            self._last_elapsed_s is not None
            and elapsed_s + _TIME_TOLERANCE_S < self._last_elapsed_s
        ):
            return False
        self._last_elapsed_s = elapsed_s
        if elapsed_s + _TIME_TOLERANCE_S < self._next_sample_s:
            return False
        self._next_sample_s = (
            math.floor(elapsed_s / self._interval_s + _TIME_TOLERANCE_S) * self._interval_s
            + self._interval_s
        )
        return True

    def publish(self, snapshot: ScenarioModelSnapshot) -> bool:
        """Replace the pending record without waiting for network I/O."""
        if self._closed:
            return False
        record = ObservationRecord.from_snapshot(
            snapshot,
            environment_id=self._environment_id,
            run_id=self._run_id,
            input_fingerprint_sha256=self._fingerprint,
            seed=self._seed,
        )
        if self._latest is not None:
            self._dropped_records += 1
        self._latest = record
        self._accepted_records += 1
        self._wakeup.set()
        return True

    async def start(self) -> None:
        """Start the isolated streaming task."""
        if self._closed:
            raise RuntimeError("observation publisher is closed")
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        """Stop streaming immediately without waiting for delivery."""
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()
        self._wakeup.set()
        if self._latest is not None:
            self._latest = None
            self._dropped_records += 1
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        writer: asyncio.StreamWriter | None = None
        try:
            while not self._closed:
                if self._latest is None:
                    self._wakeup.clear()
                    if self._latest is None and not self._closed:
                        await self._wakeup.wait()
                    continue
                try:
                    async with asyncio.timeout(self._connect_timeout_s):
                        _, writer = await asyncio.open_connection(self._host, self._port)
                    await self._use_connection(writer)
                except Exception:
                    self._connection_failures += 1
                finally:
                    if writer is not None:
                        writer.close()
                        with suppress(OSError):
                            await writer.wait_closed()
                        writer = None
                if not self._closed:
                    await self._wait_reconnect_delay(self._backoff.next_delay_after_failure())
        finally:
            if writer is not None:
                writer.close()
                with suppress(OSError):
                    await writer.wait_closed()

    async def _use_connection(self, writer: asyncio.StreamWriter) -> None:
        while not self._closed:
            record = self._take_latest()
            if record is None:
                self._wakeup.clear()
                if self._latest is None and not self._closed:
                    await self._wakeup.wait()
                continue
            try:
                writer.write(encode_observation_line(record).encode("utf-8") + b"\n")
                async with asyncio.timeout(self._send_timeout_s):
                    await writer.drain()
            except Exception:
                self._dropped_records += 1
                raise
            self._sent_records += 1
            self._backoff.reset_after_ack()

    def _take_latest(self) -> ObservationRecord | None:
        record = self._latest
        self._latest = None
        return record

    async def _wait_reconnect_delay(self, delay_s: float) -> None:
        with suppress(TimeoutError):
            async with asyncio.timeout(delay_s):
                await self._stop_event.wait()


def _require_positive_duration(value: object, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value <= 0.0
    ):
        raise ValueError(f"{name} must be finite and positive")
    return float(value)
