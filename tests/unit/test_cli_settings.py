"""Tests for CLI and environment deployment setting resolution."""

from pathlib import Path

import pytest

from scrap_monitoring_lidar_generator._cli_settings import (
    CONFIG_ENVIRONMENT_VARIABLE,
    DIAGNOSTICS_ENABLED_ENVIRONMENT_VARIABLE,
    DIAGNOSTICS_OUTPUT_PATH_ENVIRONMENT_VARIABLE,
    OBSERVATION_HOST_ENVIRONMENT_VARIABLE,
    OBSERVATION_INTERVAL_ENVIRONMENT_VARIABLE,
    OBSERVATION_PORT_ENVIRONMENT_VARIABLE,
    SCAN_HOST_ENVIRONMENT_VARIABLE,
    SCAN_PORT_ENVIRONMENT_VARIABLE,
    RuntimeSettings,
    RuntimeSettingsError,
    resolve_runtime_settings,
)

_ROOT = Path(__file__).parents[2]
_ENVIRONMENT_VARIABLES = (
    CONFIG_ENVIRONMENT_VARIABLE,
    SCAN_HOST_ENVIRONMENT_VARIABLE,
    SCAN_PORT_ENVIRONMENT_VARIABLE,
    OBSERVATION_HOST_ENVIRONMENT_VARIABLE,
    OBSERVATION_PORT_ENVIRONMENT_VARIABLE,
    OBSERVATION_INTERVAL_ENVIRONMENT_VARIABLE,
    DIAGNOSTICS_ENABLED_ENVIRONMENT_VARIABLE,
    DIAGNOSTICS_OUTPUT_PATH_ENVIRONMENT_VARIABLE,
)


def _resolve(
    environment: dict[str, str],
    *,
    config_path: Path | None = None,
    scan_host: str | None = None,
    scan_port: int | None = None,
    observation_host: str | None = None,
    observation_port: int | None = None,
    observation_interval_s: float | None = None,
    diagnostics_enabled: bool | None = None,
    diagnostics_output_path: Path | None = None,
) -> RuntimeSettings:
    return resolve_runtime_settings(
        environment=environment,
        config_path=config_path,
        scan_host=scan_host,
        scan_port=scan_port,
        observation_host=observation_host,
        observation_port=observation_port,
        observation_interval_s=observation_interval_s,
        diagnostics_enabled=diagnostics_enabled,
        diagnostics_output_path=diagnostics_output_path,
    )


def test_resolves_all_environment_settings() -> None:
    settings = _resolve(
        {
            CONFIG_ENVIRONMENT_VARIABLE: "/config/generator.v1.json",
            SCAN_HOST_ENVIRONMENT_VARIABLE: "height-calculation",
            SCAN_PORT_ENVIRONMENT_VARIABLE: "9001",
            OBSERVATION_HOST_ENVIRONMENT_VARIABLE: "visualizer",
            OBSERVATION_PORT_ENVIRONMENT_VARIABLE: "9101",
            OBSERVATION_INTERVAL_ENVIRONMENT_VARIABLE: "2.5",
            DIAGNOSTICS_ENABLED_ENVIRONMENT_VARIABLE: "true",
            DIAGNOSTICS_OUTPUT_PATH_ENVIRONMENT_VARIABLE: "/data/diagnostics",
        }
    )

    assert settings.config_path == Path("/config/generator.v1.json")
    assert settings.scan_host == "height-calculation"
    assert settings.scan_port == 9001
    assert settings.observation_host == "visualizer"
    assert settings.observation_port == 9101
    assert settings.observation_interval_s == 2.5
    assert settings.diagnostics_enabled is True
    assert settings.diagnostics_output_path == Path("/data/diagnostics")


def test_cli_settings_take_precedence_without_parsing_environment_values() -> None:
    settings = _resolve(
        {
            CONFIG_ENVIRONMENT_VARIABLE: "",
            SCAN_HOST_ENVIRONMENT_VARIABLE: "",
            SCAN_PORT_ENVIRONMENT_VARIABLE: "invalid",
            OBSERVATION_HOST_ENVIRONMENT_VARIABLE: "",
            OBSERVATION_PORT_ENVIRONMENT_VARIABLE: "invalid",
            OBSERVATION_INTERVAL_ENVIRONMENT_VARIABLE: "invalid",
            DIAGNOSTICS_ENABLED_ENVIRONMENT_VARIABLE: "invalid",
            DIAGNOSTICS_OUTPUT_PATH_ENVIRONMENT_VARIABLE: "",
        },
        config_path=Path("cli.json"),
        scan_host="scan-cli",
        scan_port=9002,
        observation_host="observation-cli",
        observation_port=9102,
        observation_interval_s=3.0,
        diagnostics_enabled=False,
        diagnostics_output_path=Path("cli-diagnostics"),
    )

    assert settings.config_path == Path("cli.json")
    assert settings.scan_host == "scan-cli"
    assert settings.scan_port == 9002
    assert settings.observation_host == "observation-cli"
    assert settings.observation_port == 9102
    assert settings.observation_interval_s == 3.0
    assert settings.diagnostics_enabled is False
    assert settings.diagnostics_output_path == Path("cli-diagnostics")


def test_keeps_optional_scan_endpoint_unset_and_uses_observation_interval_default() -> None:
    settings = _resolve(
        {
            CONFIG_ENVIRONMENT_VARIABLE: "generator.json",
            OBSERVATION_HOST_ENVIRONMENT_VARIABLE: "visualizer",
            OBSERVATION_PORT_ENVIRONMENT_VARIABLE: "9100",
        }
    )

    assert settings.scan_host is None
    assert settings.scan_port is None
    assert settings.observation_interval_s == 1.0
    assert settings.diagnostics_enabled is None
    assert settings.diagnostics_output_path is None


@pytest.mark.parametrize(
    ("environment", "expected_name"),
    [
        ({}, CONFIG_ENVIRONMENT_VARIABLE),
        (
            {CONFIG_ENVIRONMENT_VARIABLE: "generator.json"},
            OBSERVATION_HOST_ENVIRONMENT_VARIABLE,
        ),
        (
            {
                CONFIG_ENVIRONMENT_VARIABLE: "generator.json",
                OBSERVATION_HOST_ENVIRONMENT_VARIABLE: "visualizer",
            },
            OBSERVATION_PORT_ENVIRONMENT_VARIABLE,
        ),
    ],
)
def test_rejects_missing_required_settings(environment: dict[str, str], expected_name: str) -> None:
    with pytest.raises(RuntimeSettingsError, match=expected_name):
        _resolve(environment)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (CONFIG_ENVIRONMENT_VARIABLE, ""),
        (SCAN_HOST_ENVIRONMENT_VARIABLE, ""),
        (SCAN_PORT_ENVIRONMENT_VARIABLE, "zero"),
        (SCAN_PORT_ENVIRONMENT_VARIABLE, "0"),
        (SCAN_PORT_ENVIRONMENT_VARIABLE, "65536"),
        (OBSERVATION_HOST_ENVIRONMENT_VARIABLE, ""),
        (OBSERVATION_PORT_ENVIRONMENT_VARIABLE, "zero"),
        (OBSERVATION_PORT_ENVIRONMENT_VARIABLE, "0"),
        (OBSERVATION_INTERVAL_ENVIRONMENT_VARIABLE, "zero"),
        (OBSERVATION_INTERVAL_ENVIRONMENT_VARIABLE, "0"),
        (OBSERVATION_INTERVAL_ENVIRONMENT_VARIABLE, "86400.1"),
        (DIAGNOSTICS_ENABLED_ENVIRONMENT_VARIABLE, "1"),
        (DIAGNOSTICS_ENABLED_ENVIRONMENT_VARIABLE, "TRUE"),
        (DIAGNOSTICS_OUTPUT_PATH_ENVIRONMENT_VARIABLE, ""),
    ],
)
def test_rejects_invalid_environment_values(name: str, value: str) -> None:
    environment = {
        CONFIG_ENVIRONMENT_VARIABLE: "generator.json",
        OBSERVATION_HOST_ENVIRONMENT_VARIABLE: "visualizer",
        OBSERVATION_PORT_ENVIRONMENT_VARIABLE: "9100",
        name: value,
    }

    with pytest.raises(RuntimeSettingsError, match=name):
        _resolve(environment)


@pytest.mark.parametrize("name", _ENVIRONMENT_VARIABLES)
def test_environment_setting_is_documented_in_root_readme(name: str) -> None:
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")

    assert f"`{name}`" in readme
