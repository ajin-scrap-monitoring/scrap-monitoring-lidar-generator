"""TCP test double for edge scan and observation validation."""

import argparse
import asyncio
import json
import signal
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from scrap_monitoring_lidar_generator.configuration import load_environment
from scrap_monitoring_lidar_generator.observation import (
    MAX_OBSERVATION_LINE_BYTES,
    ObservationStreamHeader,
    decode_observation_header_line,
    decode_observation_line,
)
from scrap_monitoring_lidar_generator.transport import (
    DEFAULT_MAX_MESSAGE_BODY_BYTES,
    AckMessage,
    ScanIdentity,
    ScanMessage,
    decode_scan_message,
    encode_frame,
    encode_response_message,
)

_MAX_ERRORS = 20


@dataclass(slots=True)
class ReceiverState:
    """Bounded counters and identity state for one validation run."""

    environment_id: str
    expected_sensor_ids: tuple[str, ...]
    run_id: str | None = None
    scan_connections: dict[str, int] = field(default_factory=dict)
    scan_received: int = 0
    scan_unique: dict[str, int] = field(default_factory=dict)
    scan_duplicates: int = 0
    scan_gaps: int = 0
    scan_points: int = 0
    last_scan_ids: dict[str, int] = field(default_factory=dict)
    observation_headers: int = 0
    observations: int = 0
    observation_gaps: int = 0
    last_observation_sequence: int = 0
    errors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if len(self.expected_sensor_ids) != 2:
            raise ValueError("edge validation requires exactly 2 configured sensors")
        if len(set(self.expected_sensor_ids)) != 2:
            raise ValueError("edge validation requires unique configured sensor IDs")
        self.scan_connections = dict.fromkeys(self.expected_sensor_ids, 0)
        self.scan_unique = dict.fromkeys(self.expected_sensor_ids, 0)
        self.last_scan_ids = dict.fromkeys(self.expected_sensor_ids, 0)

    def record_scan(self, message: ScanMessage, connection_sensor_id: str | None) -> str:
        """Validate and record one scan, returning its connection sensor identifier."""
        if message.environment_id != self.environment_id:
            raise ValueError("scan environment_id does not match validation environment")
        if message.sensor_id not in self.scan_unique:
            raise ValueError(f"unexpected scan sensor_id: {message.sensor_id}")
        if connection_sensor_id is None:
            connection_sensor_id = message.sensor_id
            self.scan_connections[message.sensor_id] += 1
        elif connection_sensor_id != message.sensor_id:
            raise ValueError("one scan connection carried multiple sensor_id values")
        self._record_run_id(message.run_id)
        self.scan_received += 1
        self.scan_points += message.measured_scan.point_count
        last_scan_id = self.last_scan_ids[message.sensor_id]
        if message.scan_id <= last_scan_id:
            self.scan_duplicates += 1
        else:
            self.scan_gaps += message.scan_id - last_scan_id - 1
            self.last_scan_ids[message.sensor_id] = message.scan_id
            self.scan_unique[message.sensor_id] += 1
        return connection_sensor_id

    def record_observation_header(self, header: ObservationStreamHeader) -> None:
        """Validate and record one connection header."""
        if header.environment_id != self.environment_id:
            raise ValueError("observation environment_id does not match validation environment")
        sensor_ids = tuple(sensor.sensor_id for sensor in header.scene.sensors)
        if sensor_ids != self.expected_sensor_ids:
            raise ValueError("observation sensor order does not match validation environment")
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

    def add_error(self, error: Exception) -> None:
        """Retain a bounded set of validation errors."""
        if len(self.errors) < _MAX_ERRORS:
            self.errors.append(f"{type(error).__name__}: {error}")

    def to_document(self) -> dict[str, object]:
        """Return a JSON-compatible final result."""
        return {
            "environment_id": self.environment_id,
            "expected_sensor_ids": list(self.expected_sensor_ids),
            "run_id": self.run_id,
            "scan_connections": self.scan_connections,
            "scan_received": self.scan_received,
            "scan_unique": self.scan_unique,
            "scan_duplicates": self.scan_duplicates,
            "scan_gaps": self.scan_gaps,
            "scan_points": self.scan_points,
            "last_scan_ids": self.last_scan_ids,
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
            raise ValueError("multiple run_id values received")


async def handle_scan_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    state: ReceiverState,
    *,
    max_body_bytes: int = DEFAULT_MAX_MESSAGE_BODY_BYTES,
) -> None:
    """Receive framed scans and acknowledge each valid message."""
    connection_sensor_id: str | None = None
    try:
        while True:
            try:
                prefix = await reader.readexactly(4)
            except asyncio.IncompleteReadError as error:
                if error.partial:
                    raise ValueError("scan connection ended within a frame prefix") from error
                break
            body_length = int.from_bytes(prefix, byteorder="big", signed=False)
            if not 1 <= body_length <= max_body_bytes:
                raise ValueError(f"scan body length is outside [1, {max_body_bytes}]")
            try:
                body = await reader.readexactly(body_length)
            except asyncio.IncompleteReadError as error:
                raise ValueError("scan connection ended within a frame body") from error
            message = decode_scan_message(body)
            connection_sensor_id = state.record_scan(message, connection_sensor_id)
            response = AckMessage(
                identity=ScanIdentity(
                    run_id=message.run_id,
                    sensor_id=message.sensor_id,
                    scan_id=message.scan_id,
                )
            )
            writer.write(encode_frame(encode_response_message(response)))
            await writer.drain()
    except Exception as error:
        state.add_error(error)
    finally:
        writer.close()
        with suppress(OSError):
            await writer.wait_closed()


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
        header = decode_observation_header_line(header_bytes[:-1].decode("utf-8"))
        state.record_observation_header(header)
        while True:
            line_bytes = await reader.readline()
            if not line_bytes:
                break
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
    scan_port: int,
    observation_port: int,
) -> None:
    """Run both validation servers until a stop is requested."""
    scan_server = await asyncio.start_server(
        lambda reader, writer: handle_scan_connection(reader, writer, state),
        host,
        scan_port,
    )
    observation_server = await asyncio.start_server(
        lambda reader, writer: handle_observation_connection(reader, writer, state),
        host,
        observation_port,
        limit=MAX_OBSERVATION_LINE_BYTES + 1,
    )
    print(
        "validation_receiver_ready="
        + json.dumps(
            {"scan_port": scan_port, "observation_port": observation_port},
            separators=(",", ":"),
        ),
        flush=True,
    )
    try:
        await stop_event.wait()
    finally:
        scan_server.close()
        observation_server.close()
        await scan_server.wait_closed()
        await observation_server.wait_closed()


def build_parser() -> argparse.ArgumentParser:
    """Build the validation receiver argument parser."""
    parser = argparse.ArgumentParser(
        description="Receive scans and observations for edge validation."
    )
    parser.add_argument("--environment", required=True, type=Path)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--scan-port", default=9000, type=int)
    parser.add_argument("--observation-port", default=9100, type=int)
    return parser


async def _run(arguments: argparse.Namespace) -> int:
    environment = load_environment(arguments.environment)
    sensor_ids = tuple(sensor.sensor_id for sensor in environment.sensors)
    state = ReceiverState(environment.environment_id, sensor_ids)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for handled_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(handled_signal, stop_event.set)
    await run_receiver(
        state,
        stop_event=stop_event,
        host=arguments.host,
        scan_port=arguments.scan_port,
        observation_port=arguments.observation_port,
    )
    print(
        "validation_receiver=" + json.dumps(state.to_document(), separators=(",", ":")),
        flush=True,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the edge validation receiver."""
    return asyncio.run(_run(build_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
