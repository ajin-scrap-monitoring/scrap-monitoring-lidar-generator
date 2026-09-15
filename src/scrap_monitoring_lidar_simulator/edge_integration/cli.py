"""Command-line exporter for the ajin edge synthetic processing fixture."""

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

from scrap_monitoring_lidar_simulator.configuration import ConfigurationError, load_generator_inputs
from scrap_monitoring_lidar_simulator.edge_integration.synthetic_processing_config import (
    ProcessingConfigError,
    build_synthetic_processing_config,
    write_synthetic_processing_config,
)


def build_parser() -> argparse.ArgumentParser:
    """Create the processing configuration exporter parser."""
    parser = argparse.ArgumentParser(
        description="Export the synthetic demo environment for lidar-processing."
    )
    parser.add_argument("--generator-config")
    parser.add_argument("--output", required=True)
    parser.add_argument("--socket-dir", default="/sockets")
    parser.add_argument("--site-id")
    parser.add_argument("--edge-id")
    parser.add_argument("--config-revision")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> int:
    """Write one processing configuration and return a process exit code."""
    env = os.environ if environment is None else environment
    args = build_parser().parse_args(argv)
    try:
        generator_config = _required(
            args.generator_config or env.get("SCRAP_LIDAR_GENERATOR_CONFIG"),
            "--generator-config or SCRAP_LIDAR_GENERATOR_CONFIG",
        )
        site_id = _required(args.site_id or env.get("SITE_ID"), "--site-id or SITE_ID")
        edge_id = _required(args.edge_id or env.get("EDGE_ID"), "--edge-id or EDGE_ID")
        config_revision = _required(
            args.config_revision or env.get("CONFIG_REVISION"),
            "--config-revision or CONFIG_REVISION",
        )
        config = build_synthetic_processing_config(
            load_generator_inputs(Path(generator_config)),
            socket_directory=PurePosixPath(args.socket_dir),
            site_id=site_id,
            edge_id=edge_id,
            config_revision=config_revision,
        )
        write_synthetic_processing_config(Path(args.output), config)
        return 0
    except (ConfigurationError, OSError, ProcessingConfigError, ValueError) as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2


def _required(value: str | None, source: str) -> str:
    if value is None or not value:
        raise ProcessingConfigError(f"set {source}")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
