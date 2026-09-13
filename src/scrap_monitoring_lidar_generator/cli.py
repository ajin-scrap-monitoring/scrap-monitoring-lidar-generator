"""Command-line application composition."""

import argparse
import asyncio
import math
import os
import signal
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from scrap_monitoring_lidar_generator._cli_settings import (
    CONFIG_ENVIRONMENT_VARIABLE,
    DIAGNOSTICS_ENABLED_ENVIRONMENT_VARIABLE,
    DIAGNOSTICS_OUTPUT_PATH_ENVIRONMENT_VARIABLE,
    MEAN_FILL_DURATION_ENVIRONMENT_VARIABLE,
    OBSERVATION_HOST_ENVIRONMENT_VARIABLE,
    OBSERVATION_INTERVAL_ENVIRONMENT_VARIABLE,
    OBSERVATION_PORT_ENVIRONMENT_VARIABLE,
    SCAN_HOST_ENVIRONMENT_VARIABLE,
    SCAN_PORT_ENVIRONMENT_VARIABLE,
    RuntimeSettingOverrides,
    RuntimeSettings,
    RuntimeSettingsError,
    resolve_runtime_settings,
)
from scrap_monitoring_lidar_generator.configuration import (
    ConfigurationError,
    GeneratorInputs,
    load_generator_inputs,
)
from scrap_monitoring_lidar_generator.observation import (
    DEFAULT_OBSERVATION_INTERVAL_S,
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
        type=_path,
        help=f"path to the generator v1 JSON configuration; env: {CONFIG_ENVIRONMENT_VARIABLE}",
    )
    parser.add_argument(
        "--mean-fill-duration-s",
        type=_positive_float,
        help=(
            "override the mean fill duration in seconds; "
            f"env: {MEAN_FILL_DURATION_ENVIRONMENT_VARIABLE}"
        ),
    )
    parser.add_argument(
        "--scan-host",
        type=_host,
        help=f"override the scan receiver TCP host; env: {SCAN_HOST_ENVIRONMENT_VARIABLE}",
    )
    parser.add_argument(
        "--scan-port",
        type=_port,
        help=f"override the scan receiver TCP port; env: {SCAN_PORT_ENVIRONMENT_VARIABLE}",
    )
    parser.add_argument(
        "--observation-host",
        type=_host,
        help=(
            "TCP host that receives the continuous observation stream; "
            f"env: {OBSERVATION_HOST_ENVIRONMENT_VARIABLE}"
        ),
    )
    parser.add_argument(
        "--observation-port",
        type=_port,
        help=(
            "TCP port that receives the continuous observation stream; "
            f"env: {OBSERVATION_PORT_ENVIRONMENT_VARIABLE}"
        ),
    )
    parser.add_argument(
        "--observation-interval-s",
        type=_bounded_observation_interval,
        help=(
            "simulation seconds between observation stream records; "
            f"env: {OBSERVATION_INTERVAL_ENVIRONMENT_VARIABLE}; "
            f"default: {DEFAULT_OBSERVATION_INTERVAL_S:g}"
        ),
    )
    parser.add_argument(
        "--diagnostics-enabled",
        type=_boolean,
        help=(
            "override diagnostic recording with true or false; "
            f"env: {DIAGNOSTICS_ENABLED_ENVIRONMENT_VARIABLE}"
        ),
    )
    parser.add_argument(
        "--diagnostics-output-path",
        type=_path,
        help=(
            "override the diagnostic output path; "
            f"env: {DIAGNOSTICS_OUTPUT_PATH_ENVIRONMENT_VARIABLE}"
        ),
    )
    return parser


async def _run_config(settings: RuntimeSettings) -> int:
    inputs = load_generator_inputs(settings.config_path)
    inputs = _apply_runtime_overrides(inputs, settings)
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
            observation_host=settings.observation_host,
            observation_port=settings.observation_port,
            observation_interval_s=settings.observation_interval_s,
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
        "observation=active "
        f"sent={summary.observation_stats.sent_records} "
        f"dropped={summary.observation_stats.dropped_records} "
        f"connection_failures={summary.observation_stats.connection_failures}"
    )
    if summary.sender_halt is not None:
        if summary.sender_halt != reported_halt:
            report_sender_halt(summary.sender_halt)
        return 1
    return 0


def _apply_runtime_overrides(
    inputs: GeneratorInputs,
    settings: RuntimeSettings,
) -> GeneratorInputs:
    generator = inputs.generator
    if settings.mean_fill_duration_s is not None:
        generator = replace(
            generator,
            scenario=replace(
                generator.scenario,
                mean_fill_duration_s=settings.mean_fill_duration_s,
            ),
        )
    if settings.scan_host is not None or settings.scan_port is not None:
        transport = replace(
            generator.transport,
            host=(generator.transport.host if settings.scan_host is None else settings.scan_host),
            port=(generator.transport.port if settings.scan_port is None else settings.scan_port),
        )
        generator = replace(generator, transport=transport)
    if settings.diagnostics_enabled is not None or settings.diagnostics_output_path is not None:
        output_path = settings.diagnostics_output_path
        if output_path is not None and not output_path.is_absolute():
            output_path = settings.config_path.parent / output_path
        diagnostics = replace(
            generator.diagnostics,
            enabled=(
                generator.diagnostics.enabled
                if settings.diagnostics_enabled is None
                else settings.diagnostics_enabled
            ),
            output_path=(generator.diagnostics.output_path if output_path is None else output_path),
        )
        generator = replace(generator, diagnostics=diagnostics)
    if generator is not inputs.generator:
        return replace(inputs, generator=generator)
    return inputs


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> int:
    """Run the command-line application."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        settings = resolve_runtime_settings(
            environment=os.environ if environment is None else environment,
            overrides=RuntimeSettingOverrides(
                config_path=arguments.config,
                scan_host=arguments.scan_host,
                scan_port=arguments.scan_port,
                observation_host=arguments.observation_host,
                observation_port=arguments.observation_port,
                observation_interval_s=arguments.observation_interval_s,
                diagnostics_enabled=arguments.diagnostics_enabled,
                diagnostics_output_path=arguments.diagnostics_output_path,
                mean_fill_duration_s=arguments.mean_fill_duration_s,
            ),
        )
        return asyncio.run(_run_config(settings))
    except (ConfigurationError, OSError, RuntimeSettingsError, ValueError) as error:
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


def _path(value: str) -> Path:
    if not value:
        raise argparse.ArgumentTypeError("must be a non-empty path")
    return Path(value)


def _host(value: str) -> str:
    if not value:
        raise argparse.ArgumentTypeError("must be a non-empty host")
    return value


def _boolean(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise argparse.ArgumentTypeError("must be true or false")


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
