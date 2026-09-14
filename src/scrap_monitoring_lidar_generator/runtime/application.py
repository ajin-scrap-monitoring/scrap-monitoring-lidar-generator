"""Long-running generation and delivery lifecycle."""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Protocol

from scrap_monitoring_lidar_generator.configuration import GeneratorInputs
from scrap_monitoring_lidar_generator.observation import (
    DEFAULT_OBSERVATION_HOST,
    DEFAULT_OBSERVATION_INTERVAL_S,
    DEFAULT_OBSERVATION_PORT,
    ObservationPublisher,
    ObservationPublisherStats,
    ObservationScene,
    TcpObservationPublisher,
)
from scrap_monitoring_lidar_generator.runtime.diagnostics import (
    JsonLinesDiagnosticsWriter,
    build_diagnostics_writer,
    generator_input_fingerprint,
)
from scrap_monitoring_lidar_generator.runtime.generation import (
    MeasurementGenerationRuntime,
    build_measurement_generation_runtime,
)
from scrap_monitoring_lidar_generator.scan_stream import (
    GrpcScanServer,
    ScanFrameFactory,
    ScanServerStats,
)
from scrap_monitoring_lidar_generator.wire import lidar_pb2

type DeadlineWaiter = Callable[[float, asyncio.Event], Awaitable[bool]]


class ScanFrameSink(Protocol):
    """Accept generated driver-compatible scan frames."""

    async def publish(self, frame: lidar_pb2.ScanFrame) -> None:
        """Retain one generated scan for subscribed consumers."""
        ...


class ScanStreamServer(ScanFrameSink, Protocol):
    """Lifecycle and identity boundary for a multi-sensor scan source."""

    @property
    def instance_ids(self) -> dict[str, str]:
        """Return one process-instance identifier per sensor."""
        ...

    @property
    def stats(self) -> ScanServerStats:
        """Return the current server counters."""
        ...

    async def start(self) -> None:
        """Start all configured endpoints."""
        ...

    async def close(self) -> None:
        """Stop all configured endpoints."""
        ...


@dataclass(frozen=True, slots=True)
class GeneratorRunSummary:
    """Final identity and counters for one process run."""

    run_id: str
    run_started_at_utc_us: int
    generated_scans: int
    scan_server_stats: ScanServerStats
    observation_endpoint: str
    observation_stats: ObservationPublisherStats


async def run_scan_generation(
    *,
    runtime: MeasurementGenerationRuntime,
    frame_factory: ScanFrameFactory,
    sink: ScanFrameSink,
    stop_event: asyncio.Event,
    run_started_at_monotonic_s: float,
    wait_until: DeadlineWaiter | None = None,
    observation_publisher: ObservationPublisher | None = None,
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
            frame = frame_factory.build(result)
            if frame is not None:
                await sink.publish(frame)
        if results and observation_publisher is not None:
            with suppress(Exception):
                if observation_publisher.is_due(runtime.scenario.elapsed_s):
                    observation_publisher.publish(runtime.scenario.observation_snapshot())
        generated_scans += len(results)
    return generated_scans


async def run_generator_application(
    inputs: GeneratorInputs,
    *,
    stop_event: asyncio.Event,
    run_id: str | None = None,
    run_started_at_utc_us: int | None = None,
    wait_until: DeadlineWaiter | None = None,
    observation_host: str = DEFAULT_OBSERVATION_HOST,
    observation_port: int = DEFAULT_OBSERVATION_PORT,
    observation_interval_s: float = DEFAULT_OBSERVATION_INTERVAL_S,
    observation_publisher: ObservationPublisher | None = None,
    grpc_socket_dir: Path,
    status_dir: Path,
    site_id: str,
    edge_id: str,
    config_revision: str,
    deployment_revision: str,
    scan_server: ScanStreamServer | None = None,
) -> GeneratorRunSummary:
    """Build and run measurement, diagnostics and delivery until stopped."""
    effective_run_id = str(uuid.uuid4()) if run_id is None else run_id
    effective_utc_us = (
        run_started_at_utc_us if run_started_at_utc_us is not None else time.time_ns() // 1_000
    )
    run_started_at_monotonic_s = asyncio.get_running_loop().time()
    diagnostics: JsonLinesDiagnosticsWriter | None = None
    if inputs.generator.diagnostics.enabled:
        diagnostics = build_diagnostics_writer(
            inputs,
            run_id=effective_run_id,
            run_started_at_utc_us=effective_utc_us,
        )
    runtime = build_measurement_generation_runtime(inputs, diagnostics_sink=diagnostics)
    server: ScanStreamServer = scan_server or GrpcScanServer(
        sensor_ids=(sensor.sensor_id for sensor in inputs.environment.sensors),
        socket_directory=grpc_socket_dir,
        status_directory=status_dir,
        edge_id=edge_id,
        config_revision=config_revision,
        site_id=site_id,
        deployment_revision=deployment_revision,
        service_version=version("scrap-monitoring-lidar-generator"),
    )
    frame_factory = ScanFrameFactory(
        sensor_ids=(sensor.sensor_id for sensor in inputs.environment.sensors),
        edge_id=edge_id,
        config_revision=config_revision,
        instance_ids=server.instance_ids,
    )
    publisher = observation_publisher
    if publisher is None:
        publisher = TcpObservationPublisher(
            host=observation_host,
            port=observation_port,
            environment_id=inputs.environment.environment_id,
            run_id=effective_run_id,
            input_fingerprint_sha256=generator_input_fingerprint(inputs),
            seed=inputs.generator.seed,
            scene=ObservationScene.from_inputs(inputs),
            interval_s=observation_interval_s,
            connect_timeout_s=inputs.generator.observation_transport.connect_timeout_s,
            send_timeout_s=inputs.generator.observation_transport.send_timeout_s,
            reconnect_initial_delay_s=(
                inputs.generator.observation_transport.reconnect_initial_delay_s
            ),
            reconnect_max_delay_s=inputs.generator.observation_transport.reconnect_max_delay_s,
        )
    generated_scans = 0
    await server.start()
    try:
        await publisher.start()
        try:
            generated_scans = await run_scan_generation(
                runtime=runtime,
                frame_factory=frame_factory,
                sink=server,
                stop_event=stop_event,
                run_started_at_monotonic_s=run_started_at_monotonic_s,
                wait_until=wait_until,
                observation_publisher=publisher,
            )
        finally:
            await publisher.close()
    finally:
        if diagnostics is not None:
            diagnostics.close()
        await server.close()
    return GeneratorRunSummary(
        run_id=effective_run_id,
        run_started_at_utc_us=effective_utc_us,
        generated_scans=generated_scans,
        scan_server_stats=server.stats,
        observation_endpoint=publisher.endpoint,
        observation_stats=publisher.stats,
    )


async def _wait_until(deadline_s: float, stop_event: asyncio.Event) -> bool:
    remaining_s = deadline_s - asyncio.get_running_loop().time()
    if remaining_s <= 0.0:
        return stop_event.is_set()
    with suppress(TimeoutError):
        await asyncio.wait_for(stop_event.wait(), timeout=remaining_s)
    return stop_event.is_set()
