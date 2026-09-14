"""Tests for paced generation and the application lifecycle."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import ClassVar, TypedDict

import pytest

from scrap_monitoring_lidar_generator.configuration import GeneratorInputs, load_generator_inputs
from scrap_monitoring_lidar_generator.observation import ObservationPublisherStats
from scrap_monitoring_lidar_generator.runtime import (
    build_measurement_generation_runtime,
    run_generator_application,
    run_scan_generation,
)
from scrap_monitoring_lidar_generator.scan_stream import ScanFrameFactory, ScanServerStats
from scrap_monitoring_lidar_generator.wire import lidar_pb2

_ROOT = Path(__file__).parents[2]


class _RecordingSink:
    def __init__(self) -> None:
        self.frames: list[lidar_pb2.ScanFrame] = []

    async def publish(self, frame: lidar_pb2.ScanFrame) -> None:
        self.frames.append(frame)


class _ScanServer(_RecordingSink):
    instance_ids: ClassVar[dict[str, str]] = {
        "lidar_1": "instance-1",
        "lidar_2": "instance-2",
    }

    def __init__(self) -> None:
        super().__init__()
        self.started = False
        self.closed = False

    @property
    def stats(self) -> ScanServerStats:
        return ScanServerStats(
            published_frames=len(self.frames),
            frame_loss=0,
            subscribers=0,
        )

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True


class _ObservationPublisher:
    endpoint = "127.0.0.1:17000"

    def __init__(self, *, raises: bool = False) -> None:
        self.snapshots: list[object] = []
        self.started = False
        self.closed = False
        self.raises = raises

    @property
    def stats(self) -> ObservationPublisherStats:
        return ObservationPublisherStats(
            accepted_records=len(self.snapshots),
            sent_records=0,
            dropped_records=0,
            connection_failures=0,
        )

    def is_due(self, elapsed_s: float) -> bool:
        return elapsed_s >= 0.0

    def publish(self, snapshot: object) -> bool:
        if self.raises:
            raise OSError("observation unavailable")
        self.snapshots.append(snapshot)
        return True

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True


def _inputs_with_diagnostics_disabled() -> GeneratorInputs:
    inputs = load_generator_inputs(_ROOT / "examples" / "generator.v2.json")
    return replace(
        inputs,
        generator=replace(
            inputs.generator,
            diagnostics=replace(inputs.generator.diagnostics, enabled=False),
        ),
    )


class _ApplicationArguments(TypedDict):
    grpc_socket_dir: Path
    status_dir: Path
    site_id: str
    edge_id: str
    config_revision: str
    deployment_revision: str
    scan_server: _ScanServer


def _application_arguments(server: _ScanServer) -> _ApplicationArguments:
    return {
        "grpc_socket_dir": Path("/tmp/test-sockets"),
        "status_dir": Path("/tmp/test-status"),
        "site_id": "site-a",
        "edge_id": "edge-a",
        "config_revision": "config-a",
        "deployment_revision": "deployment-a",
        "scan_server": server,
    }


def test_generation_uses_absolute_rotation_deadlines_and_message_identity() -> None:
    async def run() -> None:
        inputs = _inputs_with_diagnostics_disabled()
        runtime = build_measurement_generation_runtime(inputs)
        clock_values = iter((1_000_000_000, 1_000_000_001, 1_100_000_000, 1_100_000_001))
        factory = ScanFrameFactory(
            sensor_ids=("lidar_1", "lidar_2"),
            edge_id="edge-a",
            config_revision="config-a",
            instance_ids={"lidar_1": "instance-1", "lidar_2": "instance-2"},
            monotonic_ns=lambda: next(clock_values),
            unix_ms=lambda: 1_800_000_000_000,
        )
        sink = _RecordingSink()
        stop_event = asyncio.Event()
        deadlines: list[float] = []

        async def wait_until(deadline_s: float, event: asyncio.Event) -> bool:
            deadlines.append(deadline_s)
            if len(deadlines) == 3:
                event.set()
                return True
            return False

        generated = await run_scan_generation(
            runtime=runtime,
            frame_factory=factory,
            sink=sink,
            stop_event=stop_event,
            run_started_at_monotonic_s=100.0,
            wait_until=wait_until,
        )

        assert generated == 4
        assert deadlines == pytest.approx(
            [100.0 + 1.0 / 10.0, 100.0 + 2.0 / 10.0, 100.0 + 3.0 / 10.0]
        )
        assert [frame.sensor_id for frame in sink.frames] == ["lidar_1", "lidar_2"]
        assert all(frame.sequence == 1 for frame in sink.frames)
        assert all(frame.acquired_at_unix_ms == 1_800_000_000_000 for frame in sink.frames)

    asyncio.run(run())


def test_application_returns_identity_and_empty_counters_when_already_stopped() -> None:
    async def run() -> None:
        stop_event = asyncio.Event()
        stop_event.set()

        server = _ScanServer()
        summary = await run_generator_application(
            _inputs_with_diagnostics_disabled(),
            stop_event=stop_event,
            run_id="run-a",
            run_started_at_utc_us=123,
            observation_publisher=_ObservationPublisher(),
            **_application_arguments(server),
        )

        assert summary.run_id == "run-a"
        assert summary.run_started_at_utc_us == 123
        assert summary.generated_scans == 0
        assert summary.scan_server_stats.published_frames == 0
        assert server.started
        assert server.closed

    asyncio.run(run())


def test_application_composes_two_generated_scans_and_closes_diagnostics(tmp_path: Path) -> None:
    async def run() -> None:
        inputs = load_generator_inputs(_ROOT / "examples" / "generator.v2.json")
        inputs = replace(
            inputs,
            generator=replace(
                inputs.generator,
                diagnostics=replace(
                    inputs.generator.diagnostics,
                    output_path=tmp_path / "diagnostics",
                    sample_scan_limit_per_sensor=1,
                ),
            ),
        )
        stop_event = asyncio.Event()
        waits = 0

        async def wait_until(deadline_s: float, event: asyncio.Event) -> bool:
            nonlocal waits
            del deadline_s
            waits += 1
            if waits == 2:
                event.set()
                return True
            return False

        server = _ScanServer()
        summary = await run_generator_application(
            inputs,
            stop_event=stop_event,
            run_id="run-a",
            run_started_at_utc_us=123,
            wait_until=wait_until,
            observation_publisher=_ObservationPublisher(),
            **_application_arguments(server),
        )

        assert summary.generated_scans == 2
        assert summary.scan_server_stats.published_frames == 0
        diagnostic_files = list((tmp_path / "diagnostics").iterdir())
        assert len(diagnostic_files) == 1
        diagnostic_text = diagnostic_files[0].read_text(encoding="utf-8")
        assert diagnostic_text.endswith("\n")
        diagnostic = json.loads(diagnostic_text.splitlines()[0])
        assert diagnostic["run_id"] == "run-a"
        assert diagnostic["run_started_at_utc_us"] == 123
        assert diagnostic["captured_at"] == 123

    asyncio.run(run())


def test_application_publishes_observation_without_changing_generation() -> None:
    async def run() -> None:
        inputs = _inputs_with_diagnostics_disabled()
        stop_event = asyncio.Event()
        waits = 0

        async def wait_until(deadline_s: float, event: asyncio.Event) -> bool:
            nonlocal waits
            del deadline_s
            waits += 1
            if waits == 2:
                event.set()
                return True
            return False

        publisher = _ObservationPublisher()
        server = _ScanServer()
        summary = await run_generator_application(
            inputs,
            stop_event=stop_event,
            run_id="run-a",
            run_started_at_utc_us=123,
            wait_until=wait_until,
            observation_publisher=publisher,
            **_application_arguments(server),
        )

        assert summary.generated_scans == 2
        assert summary.observation_endpoint == publisher.endpoint
        assert len(publisher.snapshots) == 1
        assert publisher.started
        assert publisher.closed

    asyncio.run(run())


def test_observation_failure_does_not_stop_scan_generation() -> None:
    async def run() -> None:
        inputs = _inputs_with_diagnostics_disabled()
        stop_event = asyncio.Event()
        waits = 0

        async def wait_until(deadline_s: float, event: asyncio.Event) -> bool:
            nonlocal waits
            del deadline_s
            waits += 1
            if waits == 2:
                event.set()
                return True
            return False

        server = _ScanServer()
        summary = await run_generator_application(
            inputs,
            stop_event=stop_event,
            run_id="run-a",
            run_started_at_utc_us=123,
            wait_until=wait_until,
            observation_publisher=_ObservationPublisher(raises=True),
            **_application_arguments(server),
        )

        assert summary.generated_scans == 2

    asyncio.run(run())
