"""Non-blocking bounded observation recording for generator runs."""

import asyncio
import math
import os
from pathlib import Path
from typing import Protocol, TextIO

from scrap_monitoring_lidar_generator.observation.format import (
    ObservationRecord,
    encode_observation_line,
)
from scrap_monitoring_lidar_generator.scenario import ScenarioModelSnapshot

DEFAULT_OBSERVATION_INTERVAL_S = 1.0
DEFAULT_OBSERVATION_MAX_RECORDS = 300
MAX_OBSERVATION_INTERVAL_S = 86_400.0
MAX_OBSERVATION_RECORDS = 10_000
_DEFAULT_QUEUE_CAPACITY = 32
_TIME_TOLERANCE_S = 1e-12


class ObservationPublisher(Protocol):
    """Receive immutable load-model snapshots without blocking generation."""

    def publish(self, snapshot: ScenarioModelSnapshot) -> bool:
        """Queue a snapshot when its cadence and bounded-recording policy allow it."""
        ...


class JsonLinesObservationRecorder:
    """Write bounded version 1 observation records from a background task."""

    __slots__ = (
        "_accepted_count",
        "_closed",
        "_dropped_count",
        "_environment_id",
        "_error",
        "_fingerprint",
        "_input_queue",
        "_interval_s",
        "_last_elapsed_s",
        "_max_records",
        "_next_sample_s",
        "_output_path",
        "_recorded_count",
        "_run_id",
        "_seed",
        "_task",
    )

    def __init__(
        self,
        *,
        output_path: Path,
        environment_id: str,
        run_id: str,
        input_fingerprint_sha256: str,
        seed: int,
        interval_s: float = DEFAULT_OBSERVATION_INTERVAL_S,
        max_records: int = DEFAULT_OBSERVATION_MAX_RECORDS,
        queue_capacity: int = _DEFAULT_QUEUE_CAPACITY,
    ) -> None:
        if not math.isfinite(interval_s) or not 0.0 < interval_s <= MAX_OBSERVATION_INTERVAL_S:
            raise ValueError("observation interval must be finite and between 0 and 86400 seconds")
        if (
            isinstance(max_records, bool)
            or not isinstance(max_records, int)
            or not 0 < max_records <= MAX_OBSERVATION_RECORDS
        ):
            raise ValueError("observation max records must be between 1 and 10000")
        if (
            isinstance(queue_capacity, bool)
            or not isinstance(queue_capacity, int)
            or queue_capacity <= 0
        ):
            raise ValueError("observation queue capacity must be a positive integer")
        if not isinstance(output_path, Path):
            output_path = Path(output_path)

        self._output_path = output_path
        self._environment_id = environment_id
        self._run_id = run_id
        self._fingerprint = input_fingerprint_sha256
        self._seed = seed
        self._interval_s = interval_s
        self._max_records = max_records
        self._input_queue: asyncio.Queue[ObservationRecord | None] = asyncio.Queue(
            min(queue_capacity, max_records)
        )
        self._last_elapsed_s: float | None = None
        self._next_sample_s = 0.0
        self._recorded_count = 0
        self._accepted_count = 0
        self._dropped_count = 0
        self._error: str | None = None
        self._closed = False
        self._task: asyncio.Task[None] | None = None

    @property
    def output_path(self) -> Path:
        """Return the requested JSON Lines path."""
        return self._output_path

    @property
    def recorded_count(self) -> int:
        """Return the number of records flushed successfully."""
        return self._recorded_count

    @property
    def dropped_count(self) -> int:
        """Return records discarded by the queue or retention cap."""
        return self._dropped_count

    @property
    def error(self) -> str | None:
        """Return the isolated writer error, if one occurred."""
        return self._error

    async def start(self) -> None:
        """Start the asynchronous writer task."""
        if self._closed:
            raise RuntimeError("observation recorder is closed")
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    def publish(self, snapshot: ScenarioModelSnapshot) -> bool:
        """Queue the latest due snapshot without waiting for disk I/O."""
        if self._closed or self._error is not None:
            return False
        if self._accepted_count >= self._max_records:
            self._dropped_count += 1
            return False
        elapsed_s = snapshot.state.elapsed_s
        if not math.isfinite(elapsed_s):
            self._error = "ValueError: observation simulation time must be finite"
            return False
        if (
            self._last_elapsed_s is not None
            and elapsed_s + _TIME_TOLERANCE_S < self._last_elapsed_s
        ):
            self._error = "ValueError: observation simulation time must be monotonic"
            return False
        self._last_elapsed_s = elapsed_s
        if elapsed_s + _TIME_TOLERANCE_S < self._next_sample_s:
            return False
        self._next_sample_s = (
            math.floor(elapsed_s / self._interval_s + _TIME_TOLERANCE_S) * self._interval_s
        )
        self._next_sample_s += self._interval_s
        try:
            record = ObservationRecord.from_snapshot(
                snapshot,
                environment_id=self._environment_id,
                run_id=self._run_id,
                input_fingerprint_sha256=self._fingerprint,
                seed=self._seed,
            )
        except Exception as error:
            self._error = f"{type(error).__name__}: {error}"
            return False
        if self._input_queue.full():
            try:
                self._input_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            else:
                self._dropped_count += 1
        try:
            self._input_queue.put_nowait(record)
        except asyncio.QueueFull:
            self._dropped_count += 1
            return False
        self._accepted_count += 1
        return True

    async def close(self) -> None:
        """Stop the writer after flushing queued records."""
        if self._closed:
            if self._task is not None and not self._task.done():
                await self._task
            return
        self._closed = True
        if self._task is None:
            return
        while not self._task.done():
            try:
                self._input_queue.put_nowait(None)
                break
            except asyncio.QueueFull:
                await asyncio.sleep(0)
        await self._task

    async def _run(self) -> None:
        stream: TextIO | None = None
        try:
            stream = _open_exclusive(self._output_path)
            while True:
                record = await self._input_queue.get()
                if record is None:
                    break
                line = encode_observation_line(record)
                await asyncio.to_thread(_write_line, stream, line)
                self._recorded_count += 1
        except Exception as error:
            self._error = f"{type(error).__name__}: {error}"
        finally:
            if stream is not None:
                stream.close()


def _open_exclusive(path: Path) -> TextIO:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")


def _write_line(stream: TextIO, line: str) -> None:
    stream.write(line)
    stream.write("\n")
    stream.flush()
