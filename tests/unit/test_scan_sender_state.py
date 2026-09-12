"""Unit tests for scan sender lifecycle and delivery counters."""

import asyncio
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from scrap_monitoring_lidar_generator.configuration import load_generator_inputs
from scrap_monitoring_lidar_generator.measurement import MeasuredScan
from scrap_monitoring_lidar_generator.runtime import build_scan_sender
from scrap_monitoring_lidar_generator.transport import (
    AsyncMultiSensorScanSender,
    AsyncScanSender,
    ScanMessage,
    encode_frame,
    encode_scan_message,
)

_ROOT = Path(__file__).parents[2]


def _message(scan_id: int, *, sensor_id: str = "sensor-a") -> ScanMessage:
    return ScanMessage(
        environment_id="environment-a",
        run_id="run-a",
        scan_id=scan_id,
        captured_at=scan_id,
        measured_scan=MeasuredScan(
            sensor_id=sensor_id,
            angles_deg=np.asarray([0.0], dtype=np.float64),
            distances_m=np.asarray([1.0], dtype=np.float64),
            qualities=np.asarray([1], dtype=np.uint8),
        ),
    )


def _sender(
    *,
    max_age_s: float = 1.0,
    max_bytes: int = 10_000,
    clock: Callable[[], float] = time.monotonic,
) -> AsyncScanSender:
    return AsyncScanSender(
        host="receiver",
        port=9000,
        max_body_bytes=1_048_576,
        buffer_max_age_s=max_age_s,
        buffer_max_bytes=max_bytes,
        connect_timeout_s=1.0,
        send_timeout_s=1.0,
        ack_timeout_s=1.0,
        reconnect_initial_delay_s=0.5,
        reconnect_max_delay_s=5.0,
        seed=1,
        clock=clock,
    )


def test_enqueue_updates_expiry_and_oversized_counters() -> None:
    now_s = 0.0
    sender = _sender(clock=lambda: now_s)
    sender.enqueue_scan(_message(1))
    now_s = 1.0

    result = sender.enqueue_scan(_message(2))

    assert result.accepted is True
    assert sender.pending_frames == 1
    assert sender.stats.enqueued_frames == 2
    assert sender.stats.expired_frames == 1

    oversized_sender = _sender(max_bytes=1)
    oversized = oversized_sender.enqueue_scan(_message(1))

    assert oversized.accepted is False
    assert oversized_sender.pending_frames == 0
    assert oversized_sender.stats.enqueued_frames == 0
    assert oversized_sender.stats.oversized_frames == 1


def test_sender_rejects_enqueue_after_stop() -> None:
    sender = _sender()
    sender.request_stop()

    with pytest.raises(RuntimeError, match="stopped"):
        sender.enqueue_scan(_message(1))


def test_runtime_builder_applies_validated_transport_limits() -> None:
    inputs = load_generator_inputs(_ROOT / "examples" / "generator.v1.json")
    limited = replace(
        inputs,
        generator=replace(
            inputs.generator,
            transport=replace(inputs.generator.transport, buffer_max_bytes=2),
        ),
    )

    sender = build_scan_sender(limited)
    result = sender.enqueue_scan(_message(1, sensor_id="lidar_1"))

    assert result.accepted is False
    assert sender.stats.oversized_frames == 1


def test_multi_sensor_sender_routes_scans_and_aggregates_counters() -> None:
    sender = AsyncMultiSensorScanSender(
        sensor_ids=("sensor-b", "sensor-a"),
        host="receiver",
        port=9000,
        max_body_bytes=1_048_576,
        buffer_max_age_s=1.0,
        buffer_max_bytes=20_000,
        connect_timeout_s=1.0,
        send_timeout_s=1.0,
        ack_timeout_s=1.0,
        reconnect_initial_delay_s=0.5,
        reconnect_max_delay_s=5.0,
        seed=1,
        clock=lambda: 0.0,
    )

    assert sender.enqueue_scan(_message(1, sensor_id="sensor-a")).accepted
    assert sender.enqueue_scan(_message(1, sensor_id="sensor-b")).accepted
    assert sender.pending_frames == 2
    assert sender.stats.enqueued_frames == 2

    with pytest.raises(ValueError, match="not configured"):
        sender.enqueue_scan(_message(1, sensor_id="sensor-c"))


def test_multi_sensor_sender_partitions_the_total_buffer_limit_by_sensor_id() -> None:
    message_a = _message(1, sensor_id="sensor-a")
    message_b = _message(1, sensor_id="sensor-b")
    frame_bytes = len(encode_frame(encode_scan_message(message_a)))
    sender = AsyncMultiSensorScanSender(
        sensor_ids=("sensor-b", "sensor-a"),
        host="receiver",
        port=9000,
        max_body_bytes=1_048_576,
        buffer_max_age_s=1.0,
        buffer_max_bytes=frame_bytes * 2 - 1,
        connect_timeout_s=1.0,
        send_timeout_s=1.0,
        ack_timeout_s=1.0,
        reconnect_initial_delay_s=0.5,
        reconnect_max_delay_s=5.0,
        seed=1,
        clock=lambda: 0.0,
    )

    assert sender.enqueue_scan(message_a).accepted
    assert not sender.enqueue_scan(message_b).accepted
    assert sender.pending_bytes == frame_bytes
    assert sender.pending_bytes <= frame_bytes * 2 - 1
    assert sender.stats.oversized_frames == 1


@pytest.mark.parametrize(
    "sensor_ids,buffer_max_bytes",
    [
        ((), 1),
        (("sensor-a", "sensor-a"), 2),
        (("sensor-a", "sensor-b"), 1),
    ],
)
def test_multi_sensor_sender_rejects_invalid_lane_configuration(
    sensor_ids: tuple[str, ...],
    buffer_max_bytes: int,
) -> None:
    with pytest.raises(ValueError):
        AsyncMultiSensorScanSender(
            sensor_ids=sensor_ids,
            host="receiver",
            port=9000,
            max_body_bytes=1_048_576,
            buffer_max_age_s=1.0,
            buffer_max_bytes=buffer_max_bytes,
            connect_timeout_s=1.0,
            send_timeout_s=1.0,
            ack_timeout_s=1.0,
            reconnect_initial_delay_s=0.5,
            reconnect_max_delay_s=5.0,
            seed=1,
        )


def test_sender_rejects_concurrent_run_calls() -> None:
    async def run() -> None:
        sender = _sender()
        first_run = asyncio.create_task(sender.run())
        await asyncio.sleep(0)

        with pytest.raises(RuntimeError, match="already running"):
            await sender.run()

        sender.request_stop()
        await asyncio.wait_for(first_run, timeout=1.0)

    asyncio.run(run())


@pytest.mark.parametrize(
    "sender",
    [
        lambda: AsyncScanSender(
            host="",
            port=9000,
            max_body_bytes=1,
            buffer_max_age_s=1.0,
            buffer_max_bytes=1,
            connect_timeout_s=1.0,
            send_timeout_s=1.0,
            ack_timeout_s=1.0,
            reconnect_initial_delay_s=0.5,
            reconnect_max_delay_s=5.0,
            seed=1,
        ),
        lambda: AsyncScanSender(
            host="receiver",
            port=9000,
            max_body_bytes=1,
            buffer_max_age_s=1.0,
            buffer_max_bytes=1,
            connect_timeout_s=1.0,
            send_timeout_s=1.0,
            ack_timeout_s=0.0,
            reconnect_initial_delay_s=0.5,
            reconnect_max_delay_s=5.0,
            seed=1,
        ),
    ],
)
def test_sender_rejects_invalid_direct_configuration(
    sender: Callable[[], AsyncScanSender],
) -> None:
    with pytest.raises(ValueError):
        sender()
