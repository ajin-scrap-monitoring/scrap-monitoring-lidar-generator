"""Command-line application composition."""

import argparse
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    return argparse.ArgumentParser(
        prog="scrap-monitoring-lidar-generator",
        description="Generate synthetic LiDAR scan data for scrap monitoring.",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line application."""
    build_parser().parse_args(argv)
    return 0
