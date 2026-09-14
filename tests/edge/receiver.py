"""gRPC subscriber and observation receiver for edge image validation."""

import argparse
import asyncio
import json
import math
import signal
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

import grpc

from scrap_monitoring_lidar_generator.configuration import load_environment
from scrap_monitoring_lidar_generator.observation import (
    MAX_OBSERVATION_LINE_BYTES,
    ObservationStreamHeader,
    decode_observation_header_line,
    decode_observation_line,
)
from scrap_monitoring_lidar_generator.wire import lidar_pb2, lidar_pb2_grpc

_MAX_ERRORS = 20


@dataclass(slots=True)
class ReceiverState:
    """Bounded counters and identity state for one validation run."""

    environment_id: str
    expected_sensor_ids: tuple[str, ...]
    edge_id: str
    config_revision: str
    run_id: str | None = None
    scan_subscriptions: dict[str, int] = field(default_factory=dict)
    scan_received: int = 0
    scan_unique: dict[str, int] = field(default_factory=dict)
    scan_duplicates: int = 0
    scan_gaps: int = 0
    scan_points: int = 0
    last_sequences: dict[str, int] = field(default_factory=dict)
    instance_ids: dict[str, str | None] = field(default_factory=dict)
    observation_headers: int = 0
    observations: int = 0
    observation_gaps: int = 0
    last_observation_sequence: int = 0
    errors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if len(self.expected_sensor_ids) != 2 or len(set(self.expected_sensor_ids)) != 2:
            raise ValueError("edge validation requires exactly 2 unique configured sensors")
        self.scan_subscriptions = dict.fromkeys(self.expected_sensor_ids, 0)
        self.scan_unique = dict.fromkeys(self.expected_sensor_ids, 0)
        self.last_sequences = dict.fromkeys(self.expected_sensor_ids, 0)
        self.instance_ids = dict.fromkeys(self.expected_sensor_ids)

    def record_subscription(self, sensor_id: str) -> None:
        """Record one subscription that delivered at least one frame."""
        self.scan_subscriptions[sensor_id] += 1

    def record_scan(self, frame: lidar_pb2.ScanFrame, expected_sensor_id: str) -> None:
        """Validate and record one upstream-compatible scan frame."""
        if frame.schema_version != "1.0":
            raise ValueError("unexpected scan schema_version")
        if frame.edge_id != self.edge_id or frame.config_revision != self.config_revision:
            raise ValueError("scan deployment identity mismatch")
        if frame.sensor_id != expected_sensor_id or frame.sensor_id not in self.scan_unique:
            raise ValueError("scan sensor_id does not match subscription lane")
        if (
            not frame.instance_id
            or frame.sequence < 1
            or frame.acquired_at_unix_ms < 1
            or frame.acquired_monotonic_ns < 1
            or frame.sdk_status != "OK"
            or not math.isfinite(frame.scan_hz)
            or frame.scan_hz <= 0
            or not 0 < len(frame.samples) <= 32_768
        ):
            raise ValueError("invalid scan frame metadata")
        if any(
            sample.angle_mdeg >= 360_000 or sample.distance_mm > 100_000 or sample.quality > 63
            for sample in frame.samples
        ):
            raise ValueError("invalid normalized scan sample")

        instance_id = self.instance_ids[frame.sensor_id]
        if instance_id != frame.instance_id:
            self.instance_ids[frame.sensor_id] = frame.instance_id
            self.last_sequences[frame.sensor_id] = 0
        last_sequence = self.last_sequences[frame.sensor_id]
        self.scan_received += 1
        self.scan_points += len(frame.samples)
        if frame.sequence <= last_sequence:
            self.scan_duplicates += 1
            return
        self.scan_gaps += frame.sequence - last_sequence - 1
        self.last_sequences[frame.sensor_id] = frame.sequence
        self.scan_unique[frame.sensor_id] += 1

    def record_observation_header(self, header: ObservationStreamHeader) -> None:
        """Validate and record one observation connection header."""
        if header.environment_id != self.environment_id:
            raise ValueError("observation environment_id mismatch")
        if tuple(sensor.sensor_id for sensor in header.scene.sensors) != self.expected_sensor_ids:
            raise ValueError("observation sensor order mismatch")
        self._record_run_id(header.run_id)
        self.observation_headers += 1

    def record_observation(self, line: str) -> None:
        """Validate and record one dynamic observation."""
        record = decode_observation_line(line)
        self._record_run_id(record.run_id)
        if record.sequence <= self.last_observation_sequence:
            raise ValueError("observation sequence did not increase")
        self.observation_gaps += record.sequence - self.last_observation_sequence - 1
        self.last_observation_sequence = record.sequence
        self.observations += 1

    def add_error(self, error: BaseException) -> None:
        """Retain a bounded set of validation errors."""
        if len(self.errors) < _MAX_ERRORS:
            self.errors.append(f"{type(error).__name__}: {error}")

    def to_document(self) -> dict[str, object]:
        """Return a JSON-compatible final result."""
        return {
            "environment_id": self.environment_id,
            "expected_sensor_ids": list(self.expected_sensor_ids),
            "edge_id": self.edge_id,
            "config_revision": self.config_revision,
            "run_id": self.run_id,
            "scan_subscriptions": self.scan_subscriptions,
            "scan_received": self.scan_received,
            "scan_unique": self.scan_unique,
            "scan_duplicates": self.scan_duplicates,
            "scan_gaps": self.scan_gaps,
            "scan_points": self.scan_points,
            "last_sequences": self.last_sequences,
            "instance_ids": self.instance_ids,
            "observation_headers": self.observation_headers,
            "observations": self.observations,
            "observation_gaps": self.observation_gaps,
            "last_observation_sequence": self.last_observation_sequence,
            "errors": self.errors,
        }

    def _record_run_id(self, run_id: str) -> None:
        if self.run_id is None:
            self.run_id = run_id
        elif self.run_id != run_id:
            raise ValueError("multiple observation run_id values received")


async def subscribe_scans(
    state: ReceiverState,
    *,
    sensor_id: str,
    socket_directory: Path,
    stop_event: asyncio.Event,
) -> None:
    """Reconnect to one sensor UDS and validate its stream until stopped."""
    socket_path = socket_directory / f"{sensor_id}.sock"
    endpoint = f"unix:{socket_path}"
    while not stop_event.is_set():
        while not socket_path.is_socket() and not stop_event.is_set():
            with suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=0.02)
        if stop_event.is_set():
            return
        delivered = False
        try:
            async with grpc.aio.insecure_channel(
                endpoint,
                options=(("grpc.max_receive_message_length", 4 * 1024 * 1024),),
            ) as channel:
                stream = lidar_pb2_grpc.LidarScanSourceStub(  # type: ignore[no-untyped-call]
                    channel
                ).SubscribeScans(lidar_pb2.SubscribeRequest(consumer_id="edge-validation"))
                async for frame in stream:
                    if not delivered:
                        state.record_subscription(sensor_id)
                        delivered = True
                    state.record_scan(frame, sensor_id)
        except grpc.aio.AioRpcError as error:
            if error.code() not in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.CANCELLED):
                state.add_error(error)
        except Exception as error:
            state.add_error(error)
        with suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=0.1)


async def handle_observation_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    state: ReceiverState,
) -> None:
    """Receive one header followed by dynamic observation records."""
    try:
        header_bytes = await reader.readline()
        if not header_bytes or not header_bytes.endswith(b"\n"):
            raise ValueError("observation connection omitted a complete header line")
        state.record_observation_header(
            decode_observation_header_line(header_bytes[:-1].decode("utf-8"))
        )
        while line_bytes := await reader.readline():
            if not line_bytes.endswith(b"\n"):
                raise ValueError("observation connection ended within a record")
            state.record_observation(line_bytes[:-1].decode("utf-8"))
    except Exception as error:
        state.add_error(error)
    finally:
        writer.close()
        with suppress(OSError):
            await writer.wait_closed()


async def run_receiver(
    state: ReceiverState,
    *,
    stop_event: asyncio.Event,
    host: str,
    observation_port: int,
    socket_directory: Path,
) -> None:
    """Run two subscribers and the observation server until stopped."""
    observation_server = await asyncio.start_server(
        lambda reader, writer: handle_observation_connection(reader, writer, state),
        host,
        observation_port,
        limit=MAX_OBSERVATION_LINE_BYTES + 1,
    )
    tasks = [
        asyncio.create_task(
            subscribe_scans(
                state,
                sensor_id=sensor_id,
                socket_directory=socket_directory,
                stop_event=stop_event,
            )
        )
        for sensor_id in state.expected_sensor_ids
    ]
    print(
        "validation_receiver_ready="
        + json.dumps(
            {"socket_directory": str(socket_directory), "observation_port": observation_port},
            separators=(",", ":"),
        ),
        flush=True,
    )
    try:
        await stop_event.wait()
    finally:
        observation_server.close()
        await observation_server.wait_closed()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def build_parser() -> argparse.ArgumentParser:
    """Build the validation receiver argument parser."""
    parser = argparse.ArgumentParser(description="Subscribe to scans and receive observations.")
    parser.add_argument("--environment", required=True, type=Path)
    parser.add_argument("--socket-dir", required=True, type=Path)
    parser.add_argument("--edge-id", required=True)
    parser.add_argument("--config-revision", required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--observation-port", default=17_000, type=int)
    return parser


async def _run(arguments: argparse.Namespace) -> int:
    environment = load_environment(arguments.environment)
    state = ReceiverState(
        environment.environment_id,
        tuple(sensor.sensor_id for sensor in environment.sensors),
        arguments.edge_id,
        arguments.config_revision,
    )
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for handled_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(handled_signal, stop_event.set)
    await run_receiver(
        state,
        stop_event=stop_event,
        host=arguments.host,
        observation_port=arguments.observation_port,
        socket_directory=arguments.socket_dir,
    )
    print("validation_receiver=" + json.dumps(state.to_document(), separators=(",", ":")))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the edge validation receiver."""
    return asyncio.run(_run(build_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
