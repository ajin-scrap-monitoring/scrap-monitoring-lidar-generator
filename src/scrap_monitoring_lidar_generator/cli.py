"""Command-line application composition."""

import argparse
import asyncio
import signal
import sys
from collections.abc import Sequence
from pathlib import Path

from scrap_monitoring_lidar_generator.configuration import ConfigurationError, load_generator_inputs
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
    return parser


async def _run_config(path: Path) -> int:
    inputs = load_generator_inputs(path)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    handled_signals = (signal.SIGINT, signal.SIGTERM)
    for handled_signal in handled_signals:
        loop.add_signal_handler(handled_signal, stop_event.set)
    try:
        summary = await run_generator_application(inputs, stop_event=stop_event)
    finally:
        for handled_signal in handled_signals:
            loop.remove_signal_handler(handled_signal)

    print(
        f"run_id={summary.run_id} generated={summary.generated_scans} "
        f"acknowledged={summary.sender_stats.acknowledged_frames} "
        f"pending={summary.pending_frames}"
    )
    if summary.sender_halt is not None:
        print(
            f"transport halted: {summary.sender_halt.code.value}: {summary.sender_halt.detail}",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line application."""
    arguments = build_parser().parse_args(argv)
    try:
        return asyncio.run(_run_config(arguments.config))
    except (ConfigurationError, OSError) as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
