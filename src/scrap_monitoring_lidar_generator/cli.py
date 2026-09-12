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
    DEFAULT_OBSERVATION_INTERVAL_S,
    DEFAULT_OBSERVATION_MAX_RECORDS,
    MAX_OBSERVATION_INTERVAL_S,
    MAX_OBSERVATION_RECORDS,
)
from scrap_monitoring_lidar_generator.runtime import run_generator_application


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
        "--observation-path",
        type=Path,
        help="optional JSON Lines path for bounded load-model observation",
    )
    parser.add_argument(
        "--observation-interval-s",
        type=_bounded_observation_interval,
        default=DEFAULT_OBSERVATION_INTERVAL_S,
        help="simulation seconds between observation records",
    )
    parser.add_argument(
        "--observation-max-records",
        type=_bounded_observation_max_records,
        default=DEFAULT_OBSERVATION_MAX_RECORDS,
        help="maximum observation records written for one run",
    )
    return parser


async def _run_config(
    path: Path,
    *,
    observation_path: Path | None = None,
    observation_interval_s: float = DEFAULT_OBSERVATION_INTERVAL_S,
    observation_max_records: int = DEFAULT_OBSERVATION_MAX_RECORDS,
) -> int:
    inputs = load_generator_inputs(path)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    handled_signals = (signal.SIGINT, signal.SIGTERM)
    for handled_signal in handled_signals:
        loop.add_signal_handler(handled_signal, stop_event.set)
    try:
        if observation_path is None:
            summary = await run_generator_application(inputs, stop_event=stop_event)
        else:
            summary = await run_generator_application(
                inputs,
                stop_event=stop_event,
                observation_path=str(observation_path),
                observation_interval_s=observation_interval_s,
                observation_max_records=observation_max_records,
            )
    finally:
        for handled_signal in handled_signals:
            loop.remove_signal_handler(handled_signal)

    print(
        f"run_id={summary.run_id} generated={summary.generated_scans} "
        f"acknowledged={summary.sender_stats.acknowledged_frames} "
        f"pending={summary.pending_frames}"
    )
    if summary.observation_path is not None:
        print(
            f"observation={summary.observation_path} records={summary.observation_records} "
            f"dropped={summary.observation_dropped}"
        )
        if summary.observation_error is not None:
            print(f"observation error: {summary.observation_error}", file=sys.stderr)
    if summary.sender_halt is not None:
        print(
            f"transport halted: {summary.sender_halt.code.value}: {summary.sender_halt.detail}",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line application."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.observation_path is None and (
        arguments.observation_interval_s != DEFAULT_OBSERVATION_INTERVAL_S
        or arguments.observation_max_records != DEFAULT_OBSERVATION_MAX_RECORDS
    ):
        parser.error(
            "--observation-interval-s and --observation-max-records require --observation-path"
        )
    try:
        if arguments.observation_path is None:
            return asyncio.run(_run_config(arguments.config))
        return asyncio.run(
            _run_config(
                arguments.config,
                observation_path=arguments.observation_path,
                observation_interval_s=arguments.observation_interval_s,
                observation_max_records=arguments.observation_max_records,
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


def _positive_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if result <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def _bounded_observation_interval(value: str) -> float:
    result = _positive_float(value)
    if result > MAX_OBSERVATION_INTERVAL_S:
        raise argparse.ArgumentTypeError(f"must not exceed {MAX_OBSERVATION_INTERVAL_S:g} seconds")
    return result


def _bounded_observation_max_records(value: str) -> int:
    result = _positive_int(value)
    if result > MAX_OBSERVATION_RECORDS:
        raise argparse.ArgumentTypeError(f"must not exceed {MAX_OBSERVATION_RECORDS}")
    return result
