"""Measure generation stages and loopback transport without real-time pacing."""

import argparse
import asyncio
import json
import multiprocessing
import os
import platform
import resource
import socket
import sys
import threading
import time
from collections.abc import Sequence
from multiprocessing.connection import Connection
from multiprocessing.context import SpawnProcess
from pathlib import Path
from typing import Any

from scrap_monitoring_lidar_generator.configuration import load_generator_inputs
from scrap_monitoring_lidar_generator.runtime import (
    PerformanceRecorder,
    PerformanceStage,
    build_measurement_generation_runtime,
)
from scrap_monitoring_lidar_generator.transport import (
    AckMessage,
    AsyncFramedTcpConnection,
    ScanIdentity,
    ScanMessageFactory,
    decode_response_message,
    decode_scan_message,
    encode_frame,
    encode_response_message,
    encode_scan_message,
)

_RUN_STARTED_AT_UTC_US = 1_800_000_000_000_000


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure unpaced generation and loopback transport stages."
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--scans-per-sensor", type=_positive_integer, default=100)
    return parser


def _receive_exact(connection: socket.socket, byte_count: int) -> bytes | None:
    received = bytearray()
    while len(received) < byte_count:
        chunk = connection.recv(byte_count - len(received))
        if not chunk:
            if received:
                raise ConnectionError("loopback receiver observed a partial frame")
            return None
        received.extend(chunk)
    return bytes(received)


def _serve_loopback_connection(connection: socket.socket, max_body_bytes: int) -> None:
    with connection:
        while True:
            prefix = _receive_exact(connection, 4)
            if prefix is None:
                return
            body_size = int.from_bytes(prefix, "big")
            if body_size > max_body_bytes:
                raise ValueError("loopback receiver frame exceeds configured maximum")
            body = _receive_exact(connection, body_size)
            if body is None:
                raise ConnectionError("loopback receiver frame body is missing")
            message = decode_scan_message(body)
            response = AckMessage(
                identity=ScanIdentity(
                    run_id=message.run_id,
                    sensor_id=message.sensor_id,
                    scan_id=message.scan_id,
                )
            )
            connection.sendall(encode_frame(encode_response_message(response)))


def _serve_loopback(
    listener: socket.socket,
    ready: Connection,
    max_body_bytes: int,
    connection_count: int,
) -> None:
    failures: list[BaseException] = []

    def serve(connection: socket.socket) -> None:
        try:
            _serve_loopback_connection(connection, max_body_bytes)
        except BaseException as error:
            failures.append(error)

    with listener:
        ready.send(None)
        ready.close()
        threads = []
        for index in range(connection_count):
            connection, _ = listener.accept()
            thread = threading.Thread(
                target=serve,
                args=(connection,),
                name=f"benchmark-loopback-connection-{index + 1}",
            )
            thread.start()
            threads.append(thread)
        for thread in threads:
            thread.join()
    if failures:
        raise RuntimeError("loopback receiver connection failed") from failures[0]


def _start_loopback_receiver(
    max_body_bytes: int,
    connection_count: int,
) -> tuple[SpawnProcess, str, int]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(connection_count)
    host, port = listener.getsockname()[:2]
    context = multiprocessing.get_context("spawn")
    ready_reader, ready_writer = context.Pipe(duplex=False)
    process = context.Process(
        target=_serve_loopback,
        args=(listener, ready_writer, max_body_bytes, connection_count),
        name="benchmark-loopback-receiver",
    )
    process.start()
    ready_writer.close()
    try:
        if not ready_reader.poll(5.0):
            process.terminate()
            process.join(timeout=5.0)
            raise TimeoutError("loopback receiver did not become ready")
        ready_reader.recv()
    finally:
        ready_reader.close()
        listener.close()
    return process, str(host), int(port)


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
    message_factory = ScanMessageFactory(
        environment_id=inputs.environment.environment_id,
        run_id="benchmark-run",
        run_started_at_utc_us=_RUN_STARTED_AT_UTC_US,
    )
    max_body_bytes = inputs.generator.transport.max_message_body_bytes
    receiver, host, port = _start_loopback_receiver(max_body_bytes, len(runtime.sensor_ids))
    connections: dict[str, AsyncFramedTcpConnection] = {}
    generated_scans = 0
    generated_points = 0
    scan_wire_bytes = 0
    per_sensor = dict.fromkeys(runtime.sensor_ids, 0)
    process_started_s = time.process_time()
    wall_started_ns = time.perf_counter_ns()
    try:
        for sensor_id in runtime.sensor_ids:
            started_ns = time.perf_counter_ns()
            connections[sensor_id] = await AsyncFramedTcpConnection.connect(
                host=host,
                port=port,
                timeout_s=inputs.generator.transport.connect_timeout_s,
                max_body_bytes=max_body_bytes,
            )
            recorder.record(
                PerformanceStage.TRANSPORT_WAIT,
                time.perf_counter_ns() - started_ns,
            )

        while min(per_sensor.values()) < scans_per_sensor:
            for result in runtime.next_completed_scans():
                if per_sensor[result.sensor_id] >= scans_per_sensor:
                    continue
                message = message_factory.build(result)
                started_ns = time.perf_counter_ns()
                frame = encode_frame(
                    encode_scan_message(message),
                    max_body_bytes=max_body_bytes,
                )
                recorder.record(
                    PerformanceStage.SERIALIZATION,
                    time.perf_counter_ns() - started_ns,
                )

                connection = connections[result.sensor_id]
                started_ns = time.perf_counter_ns()
                await connection.send_frame(
                    frame,
                    timeout_s=inputs.generator.transport.send_timeout_s,
                )
                response = decode_response_message(
                    await connection.receive_body(
                        timeout_s=inputs.generator.transport.ack_timeout_s
                    )
                )
                recorder.record(
                    PerformanceStage.TRANSPORT_WAIT,
                    time.perf_counter_ns() - started_ns,
                )
                expected = ScanIdentity(
                    run_id=message.run_id,
                    sensor_id=message.sensor_id,
                    scan_id=message.scan_id,
                )
                if not isinstance(response, AckMessage) or response.identity != expected:
                    raise RuntimeError("loopback receiver returned an unexpected response")

                per_sensor[result.sensor_id] += 1
                generated_scans += 1
                generated_points += len(message.measured_scan.angles_deg)
                scan_wire_bytes += len(frame)
    finally:
        for connection in connections.values():
            await connection.close()
        receiver.join(timeout=5.0)
        if receiver.is_alive():
            receiver.terminate()
            receiver.join(timeout=5.0)

    wall_time_s = _recorded_seconds(time.perf_counter_ns() - wall_started_ns)
    process_cpu_s = time.process_time() - process_started_s
    if receiver.exitcode != 0:
        raise RuntimeError(f"loopback receiver exited with status {receiver.exitcode}")
    simulated_duration_s = runtime.scenario.elapsed_s
    maximum_rss_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    return {
        "schema_version": 1,
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
            "generated_points_per_simulated_second": (generated_points / simulated_duration_s),
            "scan_wire_bytes_per_simulated_second": (scan_wire_bytes / simulated_duration_s),
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
