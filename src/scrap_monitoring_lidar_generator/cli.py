"""Command-line application composition."""

import argparse
import asyncio
import math
import signal
import sys
from collections.abc import Sequence
from pathlib import Path

from scrap_monitoring_lidar_generator.configuration import ConfigurationError, load_generator_inputs
from scrap_monitoring_lidar_generator.observation import (
    DEFAULT_OBSERVATION_HOST,
    DEFAULT_OBSERVATION_INTERVAL_S,
    DEFAULT_OBSERVATION_PORT,
    MAX_OBSERVATION_INTERVAL_S,
)
from scrap_monitoring_lidar_generator.runtime import run_generator_application
from scrap_monitoring_lidar_generator.transport import SenderHalt


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="scrap-monitoring-lidar-generator",
        description="Generate synthetic LiDAR scan data for scrap monitoring.",
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="path to the generator v1 JSON configuration",
    )
    parser.add_argument(
        "--observation-host",
        required=True,
        help="TCP host that receives the continuous observation stream",
    )
    parser.add_argument(
        "--observation-port",
        type=_port,
        required=True,
        help="TCP port that receives the continuous observation stream",
    )
    parser.add_argument(
        "--observation-interval-s",
        type=_bounded_observation_interval,
        default=DEFAULT_OBSERVATION_INTERVAL_S,
        help="simulation seconds between observation stream records",
    )
    return parser


async def _run_config(
    path: Path,
    *,
    observation_host: str = DEFAULT_OBSERVATION_HOST,
    observation_port: int = DEFAULT_OBSERVATION_PORT,
    observation_interval_s: float = DEFAULT_OBSERVATION_INTERVAL_S,
) -> int:
    inputs = load_generator_inputs(path)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    handled_signals = (signal.SIGINT, signal.SIGTERM)
    for handled_signal in handled_signals:
        loop.add_signal_handler(handled_signal, stop_event.set)
    reported_halt: SenderHalt | None = None

    def report_sender_halt(halt: SenderHalt) -> None:
        nonlocal reported_halt
        reported_halt = halt
        sensor = "" if halt.sensor_id is None else f" sensor_id={halt.sensor_id}"
        print(
            f"transport halted{sensor}: {halt.code.value}: {halt.detail}",
            file=sys.stderr,
        )

    try:
        summary = await run_generator_application(
            inputs,
            stop_event=stop_event,
            observation_host=observation_host,
            observation_port=observation_port,
            observation_interval_s=observation_interval_s,
            on_sender_halt=report_sender_halt,
        )
    finally:
        for handled_signal in handled_signals:
            loop.remove_signal_handler(handled_signal)

    print(
        f"run_id={summary.run_id} generated={summary.generated_scans} "
        f"acknowledged={summary.sender_stats.acknowledged_frames} "
        f"pending={summary.pending_frames}"
    )
    print(
        f"transport enqueued={summary.sender_stats.enqueued_frames} "
        f"sent={summary.sender_stats.sent_frames} "
        f"acknowledged={summary.sender_stats.acknowledged_frames} "
        f"rejected={summary.sender_stats.rejected_frames} "
        f"expired={summary.sender_stats.expired_frames} "
        f"capacity_discarded={summary.sender_stats.capacity_discarded_frames} "
        f"oversized={summary.sender_stats.oversized_frames} "
        f"connection_failures={summary.sender_stats.connection_failures} "
        f"pending_frames={summary.pending_frames} "
        f"pending_bytes={summary.pending_bytes}"
    )
    print(
        f"observation={summary.observation_endpoint} "
        f"sent={summary.observation_stats.sent_records} "
        f"dropped={summary.observation_stats.dropped_records} "
        f"connection_failures={summary.observation_stats.connection_failures}"
    )
    if summary.sender_halt is not None:
        if summary.sender_halt != reported_halt:
            report_sender_halt(summary.sender_halt)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line application."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        return asyncio.run(
            _run_config(
                arguments.config,
                observation_host=arguments.observation_host,
                observation_port=arguments.observation_port,
                observation_interval_s=arguments.observation_interval_s,
            )
        )
    except (ConfigurationError, OSError, ValueError) as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


def _positive_float(value: str) -> float:
    try:
        result = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number") from error
    if not math.isfinite(result) or result <= 0.0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return result


def _port(value: str) -> int:
    try:
        result = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer port") from error
    if not 1 <= result <= 65_535:
        raise argparse.ArgumentTypeError("must be an integer in [1, 65535]")
    return result


def _bounded_observation_interval(value: str) -> float:
    result = _positive_float(value)
    if result > MAX_OBSERVATION_INTERVAL_S:
        raise argparse.ArgumentTypeError(f"must not exceed {MAX_OBSERVATION_INTERVAL_S:g} seconds")
    return result
