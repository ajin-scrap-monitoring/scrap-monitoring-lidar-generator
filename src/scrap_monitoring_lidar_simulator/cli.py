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

from scrap_monitoring_lidar_simulator._cli_settings import (
    COLLECTION_THRESHOLD_CENTER_ENVIRONMENT_VARIABLE,
    COLLECTION_THRESHOLD_HALF_RANGE,
    CONFIG_ENVIRONMENT_VARIABLE,
    CONFIG_REVISION_ENVIRONMENT_VARIABLE,
    DEPLOYMENT_REVISION_ENVIRONMENT_VARIABLE,
    DIAGNOSTICS_ENABLED_ENVIRONMENT_VARIABLE,
    DIAGNOSTICS_OUTPUT_PATH_ENVIRONMENT_VARIABLE,
    EDGE_ID_ENVIRONMENT_VARIABLE,
    GRPC_SOCKET_DIR_ENVIRONMENT_VARIABLE,
    MAX_COLLECTION_THRESHOLD_CENTER_RATIO,
    MEAN_FILL_DURATION_ENVIRONMENT_VARIABLE,
    MIN_COLLECTION_THRESHOLD_CENTER_RATIO,
    OBSERVATION_HOST_ENVIRONMENT_VARIABLE,
    OBSERVATION_INTERVAL_ENVIRONMENT_VARIABLE,
    OBSERVATION_PORT_ENVIRONMENT_VARIABLE,
    SITE_ID_ENVIRONMENT_VARIABLE,
    STATUS_DIR_ENVIRONMENT_VARIABLE,
    RuntimeSettingOverrides,
    RuntimeSettings,
    RuntimeSettingsError,
    resolve_runtime_settings,
)
from scrap_monitoring_lidar_simulator.configuration import (
    ConfigurationError,
    GeneratorInputs,
    load_generator_inputs,
)
from scrap_monitoring_lidar_simulator.observation import (
    DEFAULT_OBSERVATION_INTERVAL_S,
    MAX_OBSERVATION_INTERVAL_S,
)
from scrap_monitoring_lidar_simulator.runtime import run_generator_application


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="scrap-monitoring-lidar-simulator",
        description="Run the deterministic synthetic LiDAR simulator for scrap monitoring.",
    )
    parser.add_argument(
        "--config",
        type=_path,
        help=f"path to the generator v2 JSON configuration; env: {CONFIG_ENVIRONMENT_VARIABLE}",
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
        "--collection-threshold-center-ratio",
        type=_collection_threshold_center_ratio,
        help=(
            "override the center of the collection threshold range; "
            f"env: {COLLECTION_THRESHOLD_CENTER_ENVIRONMENT_VARIABLE}"
        ),
    )
    parser.add_argument(
        "--grpc-socket-dir",
        type=_absolute_path,
        help=f"directory for sensor UDS files; env: {GRPC_SOCKET_DIR_ENVIRONMENT_VARIABLE}",
    )
    parser.add_argument(
        "--status-dir",
        type=_absolute_path,
        help=f"directory for driver-compatible status files; env: {STATUS_DIR_ENVIRONMENT_VARIABLE}",
    )
    parser.add_argument(
        "--site-id",
        type=_identity,
        help=f"deployment site identifier; env: {SITE_ID_ENVIRONMENT_VARIABLE}",
    )
    parser.add_argument(
        "--edge-id",
        type=_driver_identity,
        help=f"edge identifier; env: {EDGE_ID_ENVIRONMENT_VARIABLE}",
    )
    parser.add_argument(
        "--config-revision",
        type=_driver_identity,
        help=f"configuration revision; env: {CONFIG_REVISION_ENVIRONMENT_VARIABLE}",
    )
    parser.add_argument(
        "--deployment-revision",
        type=_identity,
        help=f"deployment revision; env: {DEPLOYMENT_REVISION_ENVIRONMENT_VARIABLE}",
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
    try:
        summary = await run_generator_application(
            inputs,
            stop_event=stop_event,
            observation_host=settings.observation_host,
            observation_port=settings.observation_port,
            observation_interval_s=settings.observation_interval_s,
            grpc_socket_dir=settings.grpc_socket_dir,
            status_dir=settings.status_dir,
            site_id=settings.site_id,
            edge_id=settings.edge_id,
            config_revision=settings.config_revision,
            deployment_revision=settings.deployment_revision,
        )
    finally:
        for handled_signal in handled_signals:
            loop.remove_signal_handler(handled_signal)

    print(
        f"run_id={summary.run_id} generated={summary.generated_scans} "
        f"published={summary.scan_server_stats.published_frames}"
    )
    print(
        f"scan_stream published={summary.scan_server_stats.published_frames} "
        f"frame_loss={summary.scan_server_stats.frame_loss} "
        f"subscribers={summary.scan_server_stats.subscribers}"
    )
    print(
        "observation=active "
        f"sent={summary.observation_stats.sent_records} "
        f"dropped={summary.observation_stats.dropped_records} "
        f"connection_failures={summary.observation_stats.connection_failures}"
    )
    return 0


def _apply_runtime_overrides(
    inputs: GeneratorInputs,
    settings: RuntimeSettings,
) -> GeneratorInputs:
    generator = inputs.generator
    scenario = generator.scenario
    if settings.mean_fill_duration_s is not None:
        scenario = replace(
            scenario,
            mean_fill_duration_s=settings.mean_fill_duration_s,
        )
    if settings.collection_threshold_center_ratio is not None:
        center = settings.collection_threshold_center_ratio
        scenario = replace(
            scenario,
            collection_threshold_range=(
                center - COLLECTION_THRESHOLD_HALF_RANGE,
                center + COLLECTION_THRESHOLD_HALF_RANGE,
            ),
        )
    if scenario is not generator.scenario:
        generator = replace(generator, scenario=scenario)
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
                grpc_socket_dir=arguments.grpc_socket_dir,
                status_dir=arguments.status_dir,
                site_id=arguments.site_id,
                edge_id=arguments.edge_id,
                config_revision=arguments.config_revision,
                deployment_revision=arguments.deployment_revision,
                observation_host=arguments.observation_host,
                observation_port=arguments.observation_port,
                observation_interval_s=arguments.observation_interval_s,
                diagnostics_enabled=arguments.diagnostics_enabled,
                diagnostics_output_path=arguments.diagnostics_output_path,
                mean_fill_duration_s=arguments.mean_fill_duration_s,
                collection_threshold_center_ratio=(arguments.collection_threshold_center_ratio),
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


def _collection_threshold_center_ratio(value: str) -> float:
    result = _positive_float(value)
    if (
        result <= MIN_COLLECTION_THRESHOLD_CENTER_RATIO
        or result > MAX_COLLECTION_THRESHOLD_CENTER_RATIO
    ):
        raise argparse.ArgumentTypeError(
            f"must be greater than {MIN_COLLECTION_THRESHOLD_CENTER_RATIO:g} "
            f"and at most {MAX_COLLECTION_THRESHOLD_CENTER_RATIO:g}"
        )
    return result


def _path(value: str) -> Path:
    if not value:
        raise argparse.ArgumentTypeError("must be a non-empty path")
    return Path(value)


def _absolute_path(value: str) -> Path:
    result = _path(value)
    if not result.is_absolute():
        raise argparse.ArgumentTypeError("must be an absolute path")
    return result


def _identity(value: str) -> str:
    if not value or len(value) > 128 or not value[0].isalnum():
        raise argparse.ArgumentTypeError("must be a safe deployment identifier")
    if any(
        not (character.isascii() and (character.isalnum() or character in "_.-"))
        for character in value
    ):
        raise argparse.ArgumentTypeError("must be a safe deployment identifier")
    return value


def _driver_identity(value: str) -> str:
    result = _identity(value)
    if len(result) > 64:
        raise argparse.ArgumentTypeError("must be a driver-compatible identifier")
    return result


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
