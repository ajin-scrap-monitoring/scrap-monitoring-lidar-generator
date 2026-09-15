"""Bounded server-streaming gRPC endpoints for generated LiDAR scans."""

import asyncio
import json
import os
import re
import stat
import tempfile
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import grpc

from scrap_monitoring_lidar_simulator.wire import lidar_pb2, lidar_pb2_grpc

_MAX_MESSAGE_BYTES = 4 * 1024 * 1024
_MAX_SUBSCRIBERS_PER_SENSOR = 8
_STATUS_INTERVAL_S = 2.0
_DRIVER_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_DEPLOYMENT_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


@dataclass(frozen=True, slots=True)
class ScanServerStats:
    """Cumulative gRPC scan source counters."""

    published_frames: int
    frame_loss: int
    subscribers: int


class _LatestTwo:
    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._frames: deque[tuple[int, lidar_pb2.ScanFrame]] = deque(maxlen=2)
        self._serial = 0
        self._closed = False

    async def publish(self, frame: lidar_pb2.ScanFrame) -> None:
        async with self._condition:
            if self._closed:
                raise RuntimeError("scan stream has stopped")
            self._serial += 1
            self._frames.append((self._serial, frame))
            self._condition.notify_all()

    async def next_after(
        self,
        cursor: int,
    ) -> tuple[int, lidar_pb2.ScanFrame | None, int]:
        async with self._condition:
            await self._condition.wait_for(
                lambda: self._closed or bool(self._frames and self._frames[-1][0] > cursor)
            )
            if not self._frames or (self._closed and self._frames[-1][0] <= cursor):
                return cursor, None, 0
            first_serial = self._frames[0][0]
            lost = max(0, first_serial - cursor - 1)
            for serial, frame in self._frames:
                if serial > cursor:
                    return serial, frame, lost
            raise RuntimeError("scan buffer cursor did not advance")

    async def close(self) -> None:
        async with self._condition:
            self._closed = True
            self._condition.notify_all()


class _SensorService(lidar_pb2_grpc.LidarScanSourceServicer):
    def __init__(self, lane: _LatestTwo) -> None:
        self._lane = lane
        self.subscribers = 0
        self.frame_loss = 0

    async def SubscribeScans(
        self,
        request: lidar_pb2.SubscribeRequest,
        context: grpc.aio.ServicerContext[lidar_pb2.SubscribeRequest, lidar_pb2.ScanFrame],
    ) -> AsyncIterator[lidar_pb2.ScanFrame]:
        consumer_size = len(request.consumer_id.encode("utf-8"))
        if consumer_size == 0 or consumer_size > 128:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "consumer_id required")
        if self.subscribers >= _MAX_SUBSCRIBERS_PER_SENSOR:
            await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "subscriber limit")
        self.subscribers += 1
        cursor = 0
        try:
            while True:
                cursor, frame, lost = await self._lane.next_after(cursor)
                self.frame_loss += lost
                if frame is None:
                    return
                yield frame
        finally:
            self.subscribers -= 1


@dataclass(slots=True)
class _SensorLane:
    sensor_id: str
    service_name: str
    instance_id: str
    socket_path: Path
    status_path: Path
    buffer: _LatestTwo
    service: _SensorService
    server: grpc.aio.Server
    sequence: int = 0
    last_scan_unix_ms: int = 0
    last_progress_monotonic_ns: int = 0


class GrpcScanServer:
    """Host one driver-compatible UDS endpoint for every configured sensor."""

    def __init__(
        self,
        *,
        sensor_ids: Iterable[str],
        socket_directory: Path,
        status_directory: Path,
        edge_id: str,
        config_revision: str,
        site_id: str,
        deployment_revision: str,
        service_version: str,
    ) -> None:
        sensors = tuple(sensor_ids)
        _validate_server_inputs(
            sensors=sensors,
            socket_directory=socket_directory,
            status_directory=status_directory,
            edge_id=edge_id,
            config_revision=config_revision,
            site_id=site_id,
            deployment_revision=deployment_revision,
        )
        self._socket_directory = socket_directory
        self._edge_id = edge_id
        self._config_revision = config_revision
        self._site_id = site_id
        self._deployment_revision = deployment_revision
        self._service_version = service_version
        self._started_at = _iso(time.time_ns() // 1_000_000)
        self._lanes: dict[str, _SensorLane] = {}
        self._socket_directory.mkdir(parents=True, exist_ok=True)
        status_directory.mkdir(parents=True, exist_ok=True)
        for index, sensor_id in enumerate(sensors):
            socket_path = socket_directory / f"{sensor_id}.sock"
            endpoint = f"unix:{socket_path}"
            if len(endpoint.encode("utf-8")) > 100:
                raise ValueError(f"gRPC endpoint exceeds 100 bytes: {endpoint}")
            _remove_stale_socket(socket_path)
            buffer = _LatestTwo()
            service = _SensorService(buffer)
            server = grpc.aio.server(
                options=(("grpc.max_send_message_length", _MAX_MESSAGE_BYTES),)
            )
            lidar_pb2_grpc.add_LidarScanSourceServicer_to_server(  # type: ignore[no-untyped-call]
                service,
                server,
            )
            if server.add_insecure_port(endpoint) == 0:
                raise RuntimeError(f"gRPC UDS bind configuration failed: {endpoint}")
            service_name = f"lidar-driver-{'a' if index == 0 else 'b'}"
            service_status_directory = status_directory / service_name
            service_status_directory.mkdir(parents=True, exist_ok=True)
            self._lanes[sensor_id] = _SensorLane(
                sensor_id=sensor_id,
                service_name=service_name,
                instance_id=str(uuid.uuid4()),
                socket_path=socket_path,
                status_path=service_status_directory / f"{service_name}.json",
                buffer=buffer,
                service=service,
                server=server,
            )
        self._status_task: asyncio.Task[None] | None = None

    @property
    def instance_ids(self) -> dict[str, str]:
        """Return the process-instance identity assigned to every sensor source."""
        return {sensor_id: lane.instance_id for sensor_id, lane in self._lanes.items()}

    @property
    def stats(self) -> ScanServerStats:
        """Return aggregate server counters across both sensor lanes."""
        return ScanServerStats(
            published_frames=sum(lane.sequence for lane in self._lanes.values()),
            frame_loss=sum(lane.service.frame_loss for lane in self._lanes.values()),
            subscribers=sum(lane.service.subscribers for lane in self._lanes.values()),
        )

    async def start(self) -> None:
        """Create runtime directories and begin serving all sensor sockets."""
        for lane in self._lanes.values():
            await lane.server.start()
            lane.socket_path.chmod(0o660)
        self._write_statuses()
        self._status_task = asyncio.create_task(self._write_status_loop())

    async def publish(self, frame: lidar_pb2.ScanFrame) -> None:
        """Publish one immutable frame into its sensor latest-two buffer."""
        lane = self._lanes.get(frame.sensor_id)
        if lane is None:
            raise ValueError(f"unknown gRPC frame sensor_id: {frame.sensor_id}")
        if (
            frame.schema_version != "1.0"
            or frame.edge_id != self._edge_id
            or frame.config_revision != self._config_revision
            or frame.instance_id != lane.instance_id
            or frame.sdk_status != "OK"
        ):
            raise ValueError("gRPC frame identity does not match its sensor lane")
        await lane.buffer.publish(frame)
        lane.sequence = frame.sequence
        lane.last_scan_unix_ms = frame.acquired_at_unix_ms
        lane.last_progress_monotonic_ns = time.monotonic_ns()

    async def close(self) -> None:
        """Stop subscriptions, status output and gRPC servers."""
        for lane in self._lanes.values():
            await lane.buffer.close()
        if self._status_task is not None:
            self._status_task.cancel()
            await asyncio.gather(self._status_task, return_exceptions=True)
            self._status_task = None
        await asyncio.gather(*(lane.server.stop(2.0) for lane in self._lanes.values()))
        for lane in self._lanes.values():
            lane.socket_path.unlink(missing_ok=True)

    async def _write_status_loop(self) -> None:
        while True:
            await asyncio.sleep(_STATUS_INTERVAL_S)
            self._write_statuses()

    def _write_statuses(self) -> None:
        now_ms = time.time_ns() // 1_000_000
        now_monotonic_ns = time.monotonic_ns()
        for lane in self._lanes.values():
            progress_monotonic_ns = lane.last_progress_monotonic_ns or now_monotonic_ns
            last_progress_ms = now_ms - (now_monotonic_ns - progress_monotonic_ns) // 1_000_000
            document: dict[str, object] = {
                "schema_version": "1.0",
                "service": lane.service_name,
                "edge_id": self._edge_id,
                "sensor_id": lane.sensor_id,
                "config_revision": self._config_revision,
                "instance_id": lane.instance_id,
                "started_at": self._started_at,
                "updated_at": _iso(now_ms),
                "last_progress_at": _iso(last_progress_ms),
                "state": "HEALTHY" if lane.sequence else "STARTING",
                "reason_codes": [],
                "sequence": lane.sequence,
                "frame_loss": lane.service.frame_loss,
                "sdk_errors": 0,
                "last_scan_unix_ms": lane.last_scan_unix_ms,
                "reported_at": _iso(now_ms),
                "service_version": self._service_version,
                "site_id": self._site_id,
                "deployment_revision": self._deployment_revision,
            }
            target = lane.status_path
            _atomic_json(target, document)


def _remove_stale_socket(path: Path) -> None:
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(mode):
        raise RuntimeError(f"gRPC endpoint path exists and is not a socket: {path}")
    path.unlink()


def _atomic_json(path: Path, document: dict[str, object]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_server_inputs(
    *,
    sensors: tuple[str, ...],
    socket_directory: Path,
    status_directory: Path,
    edge_id: str,
    config_revision: str,
    site_id: str,
    deployment_revision: str,
) -> None:
    if len(sensors) != 2 or len(set(sensors)) != 2 or any(not sensor for sensor in sensors):
        raise ValueError("gRPC scan source requires exactly two unique sensor identifiers")
    if any(_DRIVER_IDENTITY.fullmatch(sensor) is None for sensor in sensors):
        raise ValueError("gRPC sensor identifiers must be driver-compatible")
    for name, value in (("edge_id", edge_id), ("config_revision", config_revision)):
        if _DRIVER_IDENTITY.fullmatch(value) is None:
            raise ValueError(f"gRPC {name} must be driver-compatible")
    for name, value in (("site_id", site_id), ("deployment_revision", deployment_revision)):
        if _DEPLOYMENT_IDENTITY.fullmatch(value) is None:
            raise ValueError(f"gRPC {name} must be a safe deployment identifier")
    if not socket_directory.is_absolute() or not status_directory.is_absolute():
        raise ValueError("gRPC socket and status directories must be absolute")


def _iso(unix_ms: int) -> str:
    return (
        datetime.fromtimestamp(unix_ms / 1_000, UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
