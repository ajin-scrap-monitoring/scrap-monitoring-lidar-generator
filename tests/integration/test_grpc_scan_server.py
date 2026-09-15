"""Integration tests for the two-sensor Unix Domain Socket gRPC source."""

import asyncio
import json
from pathlib import Path

import grpc
import pytest

from scrap_monitoring_lidar_simulator.scan_stream import GrpcScanServer
from scrap_monitoring_lidar_simulator.wire import lidar_pb2, lidar_pb2_grpc


def _frame(
    sequence: int,
    *,
    sensor_id: str = "lidar_1",
    instance_id: str | None = None,
) -> lidar_pb2.ScanFrame:
    return lidar_pb2.ScanFrame(
        schema_version="1.0",
        edge_id="edge-a",
        sensor_id=sensor_id,
        sequence=sequence,
        acquired_at_unix_ms=1_800_000_000_000 + sequence,
        acquired_monotonic_ns=1_000_000_000 + sequence,
        sdk_status="OK",
        scan_hz=10.0,
        samples=[lidar_pb2.ScanSample(angle_mdeg=1, distance_mm=2, quality=3)],
        instance_id=instance_id or f"instance-{sensor_id}",
        config_revision="config-a",
    )


def test_server_streams_latest_two_and_writes_compatible_status(tmp_path: Path) -> None:
    async def run() -> None:
        socket_dir = tmp_path / "sockets"
        status_dir = tmp_path / "status"
        server = GrpcScanServer(
            sensor_ids=("lidar_1", "lidar_2"),
            socket_directory=socket_dir,
            status_directory=status_dir,
            edge_id="edge-a",
            config_revision="config-a",
            site_id="site-a",
            deployment_revision="deployment-a",
            service_version="0.8.0",
        )
        await server.start()
        try:
            instance_id = server.instance_ids["lidar_1"]
            await server.publish(_frame(1, instance_id=instance_id))
            await server.publish(_frame(2, instance_id=instance_id))
            await server.publish(_frame(3, instance_id=instance_id))
            async with grpc.aio.insecure_channel(f"unix:{socket_dir / 'lidar_1.sock'}") as channel:
                stub = lidar_pb2_grpc.LidarScanSourceStub(channel)  # type: ignore[no-untyped-call]
                stream = stub.SubscribeScans(lidar_pb2.SubscribeRequest(consumer_id="consumer-a"))
                first = await asyncio.wait_for(stream.read(), timeout=2)
                second = await asyncio.wait_for(stream.read(), timeout=2)
                stream.cancel()
            assert [first.sequence, second.sequence] == [2, 3]
            assert server.stats.frame_loss == 1
            await asyncio.sleep(2.1)
        finally:
            await server.close()

        status = json.loads(
            (status_dir / "lidar-driver-a" / "lidar-driver-a.json").read_text(encoding="utf-8")
        )
        assert status["service"] == "lidar-driver-a"
        assert status["sensor_id"] == "lidar_1"
        assert status["state"] == "HEALTHY"
        assert status["reason_codes"] == []
        assert status["sequence"] == 3
        assert status["frame_loss"] == 1
        assert status["sdk_errors"] == 0
        assert not (socket_dir / "lidar_1.sock").exists()

    asyncio.run(run())


@pytest.mark.parametrize("consumer_id", ["", "x" * 129])
def test_server_rejects_invalid_consumer_id(tmp_path: Path, consumer_id: str) -> None:
    async def run() -> None:
        server = GrpcScanServer(
            sensor_ids=("lidar_1", "lidar_2"),
            socket_directory=tmp_path / "sockets",
            status_directory=tmp_path / "status",
            edge_id="edge-a",
            config_revision="config-a",
            site_id="site-a",
            deployment_revision="deployment-a",
            service_version="0.8.0",
        )
        await server.start()
        try:
            endpoint = f"unix:{tmp_path / 'sockets' / 'lidar_1.sock'}"
            async with grpc.aio.insecure_channel(endpoint) as channel:
                stub = lidar_pb2_grpc.LidarScanSourceStub(channel)  # type: ignore[no-untyped-call]
                stream = stub.SubscribeScans(lidar_pb2.SubscribeRequest(consumer_id=consumer_id))
                with pytest.raises(grpc.aio.AioRpcError) as captured:
                    await asyncio.wait_for(stream.read(), timeout=2)
                assert captured.value.code() is grpc.StatusCode.INVALID_ARGUMENT
        finally:
            await server.close()

    asyncio.run(run())


def test_server_rejects_driver_identity_over_64_bytes(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="edge_id"):
        GrpcScanServer(
            sensor_ids=("lidar_1", "lidar_2"),
            socket_directory=tmp_path / "sockets",
            status_directory=tmp_path / "status",
            edge_id="x" * 65,
            config_revision="config-a",
            site_id="site-a",
            deployment_revision="deployment-a",
            service_version="0.8.0",
        )


def test_server_rejects_ninth_sensor_subscriber(tmp_path: Path) -> None:
    async def run() -> None:
        server = GrpcScanServer(
            sensor_ids=("lidar_1", "lidar_2"),
            socket_directory=tmp_path / "sockets",
            status_directory=tmp_path / "status",
            edge_id="edge-a",
            config_revision="config-a",
            site_id="site-a",
            deployment_revision="deployment-a",
            service_version="0.8.0",
        )
        await server.start()
        channel = grpc.aio.insecure_channel(f"unix:{tmp_path / 'sockets' / 'lidar_1.sock'}")
        streams = []
        reads = []
        try:
            stub = lidar_pb2_grpc.LidarScanSourceStub(channel)  # type: ignore[no-untyped-call]
            for index in range(8):
                stream = stub.SubscribeScans(
                    lidar_pb2.SubscribeRequest(consumer_id=f"consumer-{index}")
                )
                streams.append(stream)
                reads.append(asyncio.create_task(stream.read()))
            for _ in range(100):
                if server.stats.subscribers == 8:
                    break
                await asyncio.sleep(0.01)
            assert server.stats.subscribers == 8

            rejected = stub.SubscribeScans(lidar_pb2.SubscribeRequest(consumer_id="consumer-ninth"))
            with pytest.raises(grpc.aio.AioRpcError) as captured:
                await asyncio.wait_for(rejected.read(), timeout=2)
            assert captured.value.code() is grpc.StatusCode.RESOURCE_EXHAUSTED
        finally:
            for stream in streams:
                stream.cancel()
            await asyncio.gather(*reads, return_exceptions=True)
            await channel.close()
            await server.close()

    asyncio.run(run())


def test_server_does_not_replace_non_socket_endpoint(tmp_path: Path) -> None:
    socket_dir = tmp_path / "sockets"
    socket_dir.mkdir()
    endpoint = socket_dir / "lidar_1.sock"
    endpoint.write_text("keep", encoding="utf-8")

    with pytest.raises(RuntimeError, match="not a socket"):
        GrpcScanServer(
            sensor_ids=("lidar_1", "lidar_2"),
            socket_directory=socket_dir,
            status_directory=tmp_path / "status",
            edge_id="edge-a",
            config_revision="config-a",
            site_id="site-a",
            deployment_revision="deployment-a",
            service_version="0.8.0",
        )
    assert endpoint.read_text(encoding="utf-8") == "keep"
