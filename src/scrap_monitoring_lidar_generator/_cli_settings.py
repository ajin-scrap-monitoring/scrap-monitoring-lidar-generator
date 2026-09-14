"""Resolve deployment settings from CLI values and environment variables."""

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from scrap_monitoring_lidar_generator.observation import (
    DEFAULT_OBSERVATION_INTERVAL_S,
    MAX_OBSERVATION_INTERVAL_S,
)

CONFIG_ENVIRONMENT_VARIABLE = "SCRAP_LIDAR_GENERATOR_CONFIG"
MEAN_FILL_DURATION_ENVIRONMENT_VARIABLE = "SCRAP_LIDAR_GENERATOR_MEAN_FILL_DURATION_S"
COLLECTION_THRESHOLD_CENTER_ENVIRONMENT_VARIABLE = (
    "SCRAP_LIDAR_GENERATOR_COLLECTION_THRESHOLD_CENTER_RATIO"
)
GRPC_SOCKET_DIR_ENVIRONMENT_VARIABLE = "SCRAP_LIDAR_GENERATOR_GRPC_SOCKET_DIR"
STATUS_DIR_ENVIRONMENT_VARIABLE = "SCRAP_LIDAR_GENERATOR_STATUS_DIR"
SITE_ID_ENVIRONMENT_VARIABLE = "SITE_ID"
EDGE_ID_ENVIRONMENT_VARIABLE = "EDGE_ID"
CONFIG_REVISION_ENVIRONMENT_VARIABLE = "CONFIG_REVISION"
DEPLOYMENT_REVISION_ENVIRONMENT_VARIABLE = "DEPLOYMENT_REVISION"
OBSERVATION_HOST_ENVIRONMENT_VARIABLE = "SCRAP_LIDAR_GENERATOR_OBSERVATION_HOST"
OBSERVATION_PORT_ENVIRONMENT_VARIABLE = "SCRAP_LIDAR_GENERATOR_OBSERVATION_PORT"
OBSERVATION_INTERVAL_ENVIRONMENT_VARIABLE = "SCRAP_LIDAR_GENERATOR_OBSERVATION_INTERVAL_S"
DIAGNOSTICS_ENABLED_ENVIRONMENT_VARIABLE = "SCRAP_LIDAR_GENERATOR_DIAGNOSTICS_ENABLED"
DIAGNOSTICS_OUTPUT_PATH_ENVIRONMENT_VARIABLE = "SCRAP_LIDAR_GENERATOR_DIAGNOSTICS_OUTPUT_PATH"

COLLECTION_THRESHOLD_HALF_RANGE = 0.05
MIN_COLLECTION_THRESHOLD_CENTER_RATIO = COLLECTION_THRESHOLD_HALF_RANGE
MAX_COLLECTION_THRESHOLD_CENTER_RATIO = 1.0 - COLLECTION_THRESHOLD_HALF_RANGE
_IDENTITY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_DRIVER_IDENTITY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


class RuntimeSettingsError(ValueError):
    """An invalid or missing runtime deployment setting."""


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    """Resolved runtime values after applying CLI and environment precedence."""

    config_path: Path
    grpc_socket_dir: Path
    status_dir: Path
    site_id: str
    edge_id: str
    config_revision: str
    deployment_revision: str
    observation_host: str
    observation_port: int
    observation_interval_s: float
    diagnostics_enabled: bool | None
    diagnostics_output_path: Path | None
    mean_fill_duration_s: float | None
    collection_threshold_center_ratio: float | None


@dataclass(frozen=True, slots=True)
class RuntimeSettingOverrides:
    """Unresolved command-line values that may override the environment."""

    config_path: Path | None = None
    grpc_socket_dir: Path | None = None
    status_dir: Path | None = None
    site_id: str | None = None
    edge_id: str | None = None
    config_revision: str | None = None
    deployment_revision: str | None = None
    observation_host: str | None = None
    observation_port: int | None = None
    observation_interval_s: float | None = None
    diagnostics_enabled: bool | None = None
    diagnostics_output_path: Path | None = None
    mean_fill_duration_s: float | None = None
    collection_threshold_center_ratio: float | None = None


def resolve_runtime_settings(
    *,
    environment: Mapping[str, str],
    overrides: RuntimeSettingOverrides | None = None,
) -> RuntimeSettings:
    """Resolve CLI values before environment values and code defaults."""
    if overrides is None:
        overrides = RuntimeSettingOverrides()
    resolved_config_path = _resolve(
        overrides.config_path,
        environment,
        CONFIG_ENVIRONMENT_VARIABLE,
        _parse_path,
    )
    resolved_observation_host = _resolve(
        overrides.observation_host,
        environment,
        OBSERVATION_HOST_ENVIRONMENT_VARIABLE,
        _parse_host,
    )
    resolved_observation_port = _resolve(
        overrides.observation_port,
        environment,
        OBSERVATION_PORT_ENVIRONMENT_VARIABLE,
        _parse_port,
    )
    resolved_observation_interval = _resolve(
        overrides.observation_interval_s,
        environment,
        OBSERVATION_INTERVAL_ENVIRONMENT_VARIABLE,
        _parse_observation_interval,
    )
    return RuntimeSettings(
        config_path=_require(
            resolved_config_path,
            option="--config",
            environment_variable=CONFIG_ENVIRONMENT_VARIABLE,
        ),
        grpc_socket_dir=_require(
            _resolve(
                overrides.grpc_socket_dir,
                environment,
                GRPC_SOCKET_DIR_ENVIRONMENT_VARIABLE,
                _parse_absolute_path,
            ),
            option="--grpc-socket-dir",
            environment_variable=GRPC_SOCKET_DIR_ENVIRONMENT_VARIABLE,
        ),
        status_dir=_require(
            _resolve(
                overrides.status_dir,
                environment,
                STATUS_DIR_ENVIRONMENT_VARIABLE,
                _parse_absolute_path,
            ),
            option="--status-dir",
            environment_variable=STATUS_DIR_ENVIRONMENT_VARIABLE,
        ),
        site_id=_require(
            _resolve(overrides.site_id, environment, SITE_ID_ENVIRONMENT_VARIABLE, _parse_identity),
            option="--site-id",
            environment_variable=SITE_ID_ENVIRONMENT_VARIABLE,
        ),
        edge_id=_require(
            _resolve(
                overrides.edge_id,
                environment,
                EDGE_ID_ENVIRONMENT_VARIABLE,
                _parse_driver_identity,
            ),
            option="--edge-id",
            environment_variable=EDGE_ID_ENVIRONMENT_VARIABLE,
        ),
        config_revision=_require(
            _resolve(
                overrides.config_revision,
                environment,
                CONFIG_REVISION_ENVIRONMENT_VARIABLE,
                _parse_driver_identity,
            ),
            option="--config-revision",
            environment_variable=CONFIG_REVISION_ENVIRONMENT_VARIABLE,
        ),
        deployment_revision=_require(
            _resolve(
                overrides.deployment_revision,
                environment,
                DEPLOYMENT_REVISION_ENVIRONMENT_VARIABLE,
                _parse_identity,
            ),
            option="--deployment-revision",
            environment_variable=DEPLOYMENT_REVISION_ENVIRONMENT_VARIABLE,
        ),
        observation_host=_require(
            resolved_observation_host,
            option="--observation-host",
            environment_variable=OBSERVATION_HOST_ENVIRONMENT_VARIABLE,
        ),
        observation_port=_require(
            resolved_observation_port,
            option="--observation-port",
            environment_variable=OBSERVATION_PORT_ENVIRONMENT_VARIABLE,
        ),
        observation_interval_s=(
            DEFAULT_OBSERVATION_INTERVAL_S
            if resolved_observation_interval is None
            else resolved_observation_interval
        ),
        diagnostics_enabled=_resolve(
            overrides.diagnostics_enabled,
            environment,
            DIAGNOSTICS_ENABLED_ENVIRONMENT_VARIABLE,
            _parse_boolean,
        ),
        diagnostics_output_path=_resolve(
            overrides.diagnostics_output_path,
            environment,
            DIAGNOSTICS_OUTPUT_PATH_ENVIRONMENT_VARIABLE,
            _parse_path,
        ),
        mean_fill_duration_s=_resolve(
            overrides.mean_fill_duration_s,
            environment,
            MEAN_FILL_DURATION_ENVIRONMENT_VARIABLE,
            _parse_positive_number,
        ),
        collection_threshold_center_ratio=_resolve(
            overrides.collection_threshold_center_ratio,
            environment,
            COLLECTION_THRESHOLD_CENTER_ENVIRONMENT_VARIABLE,
            _parse_collection_threshold_center_ratio,
        ),
    )


def _resolve[T](
    cli_value: T | None,
    environment: Mapping[str, str],
    environment_variable: str,
    parser: Callable[[str, str], T],
) -> T | None:
    if cli_value is not None:
        return cli_value
    value = environment.get(environment_variable)
    if value is None:
        return None
    return parser(value, environment_variable)


def _require[T](value: T | None, *, option: str, environment_variable: str) -> T:
    if value is None:
        raise RuntimeSettingsError(f"set {option} or {environment_variable}")
    return value


def _parse_path(value: str, name: str) -> Path:
    if not value:
        raise RuntimeSettingsError(f"{name} must be a non-empty path")
    return Path(value)


def _parse_absolute_path(value: str, name: str) -> Path:
    result = _parse_path(value, name)
    if not result.is_absolute():
        raise RuntimeSettingsError(f"{name} must be an absolute path")
    return result


def _parse_identity(value: str, name: str) -> str:
    if _IDENTITY_PATTERN.fullmatch(value) is None:
        raise RuntimeSettingsError(f"{name} must be a safe deployment identifier")
    return value


def _parse_driver_identity(value: str, name: str) -> str:
    if _DRIVER_IDENTITY_PATTERN.fullmatch(value) is None:
        raise RuntimeSettingsError(f"{name} must be a driver-compatible identifier")
    return value


def _parse_host(value: str, name: str) -> str:
    if not value:
        raise RuntimeSettingsError(f"{name} must be a non-empty host")
    return value


def _parse_port(value: str, name: str) -> int:
    try:
        result = int(value)
    except ValueError as error:
        raise RuntimeSettingsError(f"{name} must be an integer port") from error
    if not 1 <= result <= 65_535:
        raise RuntimeSettingsError(f"{name} must be an integer in [1, 65535]")
    return result


def _parse_observation_interval(value: str, name: str) -> float:
    result = _parse_positive_number(value, name)
    if result > MAX_OBSERVATION_INTERVAL_S:
        raise RuntimeSettingsError(f"{name} must not exceed {MAX_OBSERVATION_INTERVAL_S:g} seconds")
    return result


def _parse_positive_number(value: str, name: str) -> float:
    try:
        result = float(value)
    except ValueError as error:
        raise RuntimeSettingsError(f"{name} must be a number") from error
    if not math.isfinite(result) or result <= 0.0:
        raise RuntimeSettingsError(f"{name} must be a finite positive number")
    return result


def _parse_collection_threshold_center_ratio(value: str, name: str) -> float:
    result = _parse_positive_number(value, name)
    if (
        result <= MIN_COLLECTION_THRESHOLD_CENTER_RATIO
        or result > MAX_COLLECTION_THRESHOLD_CENTER_RATIO
    ):
        raise RuntimeSettingsError(
            f"{name} must be greater than {MIN_COLLECTION_THRESHOLD_CENTER_RATIO:g} "
            f"and at most {MAX_COLLECTION_THRESHOLD_CENTER_RATIO:g}"
        )
    return result


def _parse_boolean(value: str, name: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise RuntimeSettingsError(f"{name} must be true or false")
