"""Measure generation stages and local gRPC transport without real-time pacing."""

import argparse
import asyncio
import json
import os
import platform
import resource
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import grpc

from scrap_monitoring_lidar_generator.configuration import load_generator_inputs
from scrap_monitoring_lidar_generator.runtime import (
    PerformanceRecorder,
    PerformanceStage,
    build_measurement_generation_runtime,
)
from scrap_monitoring_lidar_generator.scan_stream import GrpcScanServer, ScanFrameFactory
from scrap_monitoring_lidar_generator.wire import lidar_pb2, lidar_pb2_grpc


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure unpaced generation and local gRPC transport stages."
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--scans-per-sensor", type=_positive_integer, default=100)
    return parser


def _recorded_seconds(summary_ns: int) -> float:
    return summary_ns / 1_000_000_000


def _stage_document(recorder: PerformanceRecorder) -> dict[str, dict[str, float | int]]:
    result: dict[str, dict[str, float | int]] = {}
    for stage in PerformanceStage:
        summary = recorder.summary(stage)
        result[stage.value] = {
            "samples": summary.samples,
            "total_s": _recorded_seconds(summary.total_ns),
            "mean_ms": summary.mean_ns / 1_000_000,
            "maximum_ms": summary.maximum_ns / 1_000_000,
        }
    return result


async def _run_benchmark(config_path: Path, scans_per_sensor: int) -> dict[str, Any]:
    inputs = load_generator_inputs(config_path)
    recorder = PerformanceRecorder()
    runtime = build_measurement_generation_runtime(inputs, performance=recorder)
    generated_scans = 0
    generated_points = 0
    scan_wire_bytes = 0
    per_sensor = dict.fromkeys(runtime.sensor_ids, 0)
    process_started_s = time.process_time()
    wall_started_ns = time.perf_counter_ns()

    with tempfile.TemporaryDirectory(prefix="lidar-generator-benchmark-") as directory:
        root = Path(directory)
        server = GrpcScanServer(
            sensor_ids=runtime.sensor_ids,
            socket_directory=root / "sockets",
            status_directory=root / "status",
            site_id="benchmark-site",
            edge_id="benchmark-edge",
            config_revision="benchmark-r1",
            deployment_revision="benchmark-deployment-r1",
            service_version="benchmark",
        )
        factory = ScanFrameFactory(
            sensor_ids=runtime.sensor_ids,
            edge_id="benchmark-edge",
            config_revision="benchmark-r1",
            instance_ids=server.instance_ids,
        )
        channels: dict[str, grpc.aio.Channel] = {}
        streams: dict[
            str,
            grpc.aio.UnaryStreamCall[lidar_pb2.SubscribeRequest, lidar_pb2.ScanFrame],
        ] = {}
        await server.start()
        try:
            for sensor_id in runtime.sensor_ids:
                channel = grpc.aio.insecure_channel(
                    f"unix:{root / 'sockets' / f'{sensor_id}.sock'}"
                )
                channels[sensor_id] = channel
                streams[sensor_id] = lidar_pb2_grpc.LidarScanSourceStub(  # type: ignore[no-untyped-call]
                    channel
                ).SubscribeScans(lidar_pb2.SubscribeRequest(consumer_id="benchmark"))

            while min(per_sensor.values()) < scans_per_sensor:
                for result in runtime.next_completed_scans():
                    if per_sensor[result.sensor_id] >= scans_per_sensor:
                        continue
                    started_ns = time.perf_counter_ns()
                    frame = factory.build(result)
                    if frame is None:
                        continue
                    wire = frame.SerializeToString()
                    recorder.record(
                        PerformanceStage.SERIALIZATION,
                        time.perf_counter_ns() - started_ns,
                    )

                    started_ns = time.perf_counter_ns()
                    receive = asyncio.create_task(streams[result.sensor_id].read())
                    await server.publish(frame)
                    received = cast(
                        lidar_pb2.ScanFrame,
                        await asyncio.wait_for(receive, timeout=2.0),
                    )
                    recorder.record(
                        PerformanceStage.TRANSPORT_WAIT,
                        time.perf_counter_ns() - started_ns,
                    )
                    if received.SerializeToString() != wire:
                        raise RuntimeError("gRPC subscriber received a different scan frame")

                    per_sensor[result.sensor_id] += 1
                    generated_scans += 1
                    generated_points += len(frame.samples)
                    scan_wire_bytes += len(wire)
        finally:
            for stream in streams.values():
                stream.cancel()
            await asyncio.gather(*(channel.close() for channel in channels.values()))
            await server.close()

    wall_time_s = _recorded_seconds(time.perf_counter_ns() - wall_started_ns)
    process_cpu_s = time.process_time() - process_started_s
    simulated_duration_s = runtime.scenario.elapsed_s
    maximum_rss_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1_024
    return {
        "schema_version": 2,
        "runtime": {
            "python": platform.python_version(),
            "architecture": platform.machine(),
            "logical_cpu_count": os.cpu_count(),
        },
        "workload": {
            "sensor_count": len(runtime.sensor_ids),
            "scans_per_sensor": scans_per_sensor,
            "generated_scans": generated_scans,
            "generated_points": generated_points,
            "scan_wire_bytes": scan_wire_bytes,
            "simulated_duration_s": simulated_duration_s,
            "generated_points_per_simulated_second": generated_points / simulated_duration_s,
            "scan_wire_bytes_per_simulated_second": scan_wire_bytes / simulated_duration_s,
        },
        "resources": {
            "wall_time_s": wall_time_s,
            "process_cpu_s": process_cpu_s,
            "estimated_single_core_utilization_percent": (
                process_cpu_s / simulated_duration_s * 100
            ),
            "maximum_rss_bytes": maximum_rss_bytes,
        },
        "stages": _stage_document(recorder),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Run the benchmark and write one machine-readable result to standard output."""
    arguments = _build_parser().parse_args(argv)
    result = asyncio.run(_run_benchmark(arguments.config, arguments.scans_per_sensor))
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
