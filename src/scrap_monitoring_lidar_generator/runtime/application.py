"""Long-running generation and delivery lifecycle."""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol

from scrap_monitoring_lidar_generator.configuration import GeneratorInputs
from scrap_monitoring_lidar_generator.runtime.diagnostics import (
    JsonLinesDiagnosticsWriter,
    build_diagnostics_writer,
)
from scrap_monitoring_lidar_generator.runtime.generation import (
    MeasurementGenerationRuntime,
    build_measurement_generation_runtime,
)
from scrap_monitoring_lidar_generator.runtime.transport import build_scan_sender
from scrap_monitoring_lidar_generator.transport import (
    BufferEnqueueResult,
    ScanMessage,
    ScanMessageFactory,
    SenderHalt,
    SenderStats,
)

type DeadlineWaiter = Callable[[float, asyncio.Event], Awaitable[bool]]


class ScanMessageSink(Protocol):
    """Accept generated scan messages without blocking on delivery."""

    def enqueue_scan(self, message: ScanMessage) -> BufferEnqueueResult:
        """Retain one generated scan for delivery."""
        ...


@dataclass(frozen=True, slots=True)
class GeneratorRunSummary:
    """Final identity and counters for one process run."""

    run_id: str
    run_started_at_utc_us: int
    generated_scans: int
    pending_frames: int
    sender_stats: SenderStats
    sender_halt: SenderHalt | None


async def run_scan_generation(
    *,
    runtime: MeasurementGenerationRuntime,
    message_factory: ScanMessageFactory,
    sink: ScanMessageSink,
    stop_event: asyncio.Event,
    run_started_at_monotonic_s: float,
    wait_until: DeadlineWaiter | None = None,
) -> int:
    """Pace completed rotations against one absolute monotonic run start."""
    waiter = wait_until or _wait_until
    generated_scans = 0
    while not stop_event.is_set():
        deadline_s = run_started_at_monotonic_s + runtime.next_completion_elapsed_s
        if await waiter(deadline_s, stop_event) or stop_event.is_set():
            break
        results = runtime.next_completed_scans()
        for result in results:
            sink.enqueue_scan(message_factory.build(result))
        generated_scans += len(results)
    return generated_scans


async def run_generator_application(
    inputs: GeneratorInputs,
    *,
    stop_event: asyncio.Event,
    run_id: str | None = None,
    run_started_at_utc_us: int | None = None,
    wait_until: DeadlineWaiter | None = None,
) -> GeneratorRunSummary:
    """Build and run measurement, diagnostics and delivery until stopped."""
    effective_run_id = str(uuid.uuid4()) if run_id is None else run_id
    diagnostics: JsonLinesDiagnosticsWriter | None = None
    if inputs.generator.diagnostics.enabled:
        diagnostics = build_diagnostics_writer(inputs)
    runtime = build_measurement_generation_runtime(inputs, diagnostics_sink=diagnostics)
    sender = build_scan_sender(inputs)
    effective_utc_us = (
        run_started_at_utc_us if run_started_at_utc_us is not None else time.time_ns() // 1_000
    )
    run_started_at_monotonic_s = asyncio.get_running_loop().time()
    message_factory = ScanMessageFactory(
        environment_id=inputs.environment.environment_id,
        run_id=effective_run_id,
        run_started_at_utc_us=effective_utc_us,
    )
    generated_scans = 0
    try:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(sender.run())
            try:
                generated_scans = await run_scan_generation(
                    runtime=runtime,
                    message_factory=message_factory,
                    sink=sender,
                    stop_event=stop_event,
                    run_started_at_monotonic_s=run_started_at_monotonic_s,
                    wait_until=wait_until,
                )
            finally:
                sender.request_stop()
    finally:
        sender.request_stop()
        if diagnostics is not None:
            diagnostics.close()
    return GeneratorRunSummary(
        run_id=effective_run_id,
        run_started_at_utc_us=effective_utc_us,
        generated_scans=generated_scans,
        pending_frames=sender.pending_frames,
        sender_stats=sender.stats,
        sender_halt=sender.halt,
    )


async def _wait_until(deadline_s: float, stop_event: asyncio.Event) -> bool:
    remaining_s = deadline_s - asyncio.get_running_loop().time()
    if remaining_s <= 0.0:
        return stop_event.is_set()
    with suppress(TimeoutError):
        await asyncio.wait_for(stop_event.wait(), timeout=remaining_s)
    return stop_event.is_set()
