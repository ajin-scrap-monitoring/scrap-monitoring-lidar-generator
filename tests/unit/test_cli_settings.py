"""Tests for CLI and environment deployment setting resolution."""

from pathlib import Path

import pytest

from scrap_monitoring_lidar_generator._cli_settings import (
    COLLECTION_THRESHOLD_CENTER_ENVIRONMENT_VARIABLE,
    CONFIG_ENVIRONMENT_VARIABLE,
    CONFIG_REVISION_ENVIRONMENT_VARIABLE,
    DEPLOYMENT_REVISION_ENVIRONMENT_VARIABLE,
    DIAGNOSTICS_ENABLED_ENVIRONMENT_VARIABLE,
    DIAGNOSTICS_OUTPUT_PATH_ENVIRONMENT_VARIABLE,
    EDGE_ID_ENVIRONMENT_VARIABLE,
    GRPC_SOCKET_DIR_ENVIRONMENT_VARIABLE,
    MEAN_FILL_DURATION_ENVIRONMENT_VARIABLE,
    OBSERVATION_HOST_ENVIRONMENT_VARIABLE,
    OBSERVATION_INTERVAL_ENVIRONMENT_VARIABLE,
    OBSERVATION_PORT_ENVIRONMENT_VARIABLE,
    SITE_ID_ENVIRONMENT_VARIABLE,
    STATUS_DIR_ENVIRONMENT_VARIABLE,
    RuntimeSettingOverrides,
    RuntimeSettingsError,
    resolve_runtime_settings,
)

_ROOT = Path(__file__).parents[2]
_REQUIRED = {
    CONFIG_ENVIRONMENT_VARIABLE: "/config/generator.v2.json",
    GRPC_SOCKET_DIR_ENVIRONMENT_VARIABLE: "/run/lidar",
    STATUS_DIR_ENVIRONMENT_VARIABLE: "/status",
    SITE_ID_ENVIRONMENT_VARIABLE: "site-a",
    EDGE_ID_ENVIRONMENT_VARIABLE: "edge-a",
    CONFIG_REVISION_ENVIRONMENT_VARIABLE: "config-r1",
    DEPLOYMENT_REVISION_ENVIRONMENT_VARIABLE: "deployment-r1",
    OBSERVATION_HOST_ENVIRONMENT_VARIABLE: "visualizer",
    OBSERVATION_PORT_ENVIRONMENT_VARIABLE: "17000",
}
_ENVIRONMENT_VARIABLES = (
    CONFIG_ENVIRONMENT_VARIABLE,
    GRPC_SOCKET_DIR_ENVIRONMENT_VARIABLE,
    STATUS_DIR_ENVIRONMENT_VARIABLE,
    SITE_ID_ENVIRONMENT_VARIABLE,
    EDGE_ID_ENVIRONMENT_VARIABLE,
    CONFIG_REVISION_ENVIRONMENT_VARIABLE,
    DEPLOYMENT_REVISION_ENVIRONMENT_VARIABLE,
    MEAN_FILL_DURATION_ENVIRONMENT_VARIABLE,
    COLLECTION_THRESHOLD_CENTER_ENVIRONMENT_VARIABLE,
    OBSERVATION_HOST_ENVIRONMENT_VARIABLE,
    OBSERVATION_PORT_ENVIRONMENT_VARIABLE,
    OBSERVATION_INTERVAL_ENVIRONMENT_VARIABLE,
    DIAGNOSTICS_ENABLED_ENVIRONMENT_VARIABLE,
    DIAGNOSTICS_OUTPUT_PATH_ENVIRONMENT_VARIABLE,
)


def test_resolves_all_environment_settings() -> None:
    settings = resolve_runtime_settings(
        environment={
            **_REQUIRED,
            MEAN_FILL_DURATION_ENVIRONMENT_VARIABLE: "43200",
            COLLECTION_THRESHOLD_CENTER_ENVIRONMENT_VARIABLE: "0.8",
            OBSERVATION_INTERVAL_ENVIRONMENT_VARIABLE: "2.5",
            DIAGNOSTICS_ENABLED_ENVIRONMENT_VARIABLE: "true",
            DIAGNOSTICS_OUTPUT_PATH_ENVIRONMENT_VARIABLE: "/data/diagnostics",
        }
    )

    assert settings.config_path == Path("/config/generator.v2.json")
    assert settings.grpc_socket_dir == Path("/run/lidar")
    assert settings.status_dir == Path("/status")
    assert settings.site_id == "site-a"
    assert settings.edge_id == "edge-a"
    assert settings.config_revision == "config-r1"
    assert settings.deployment_revision == "deployment-r1"
    assert settings.mean_fill_duration_s == 43_200.0
    assert settings.collection_threshold_center_ratio == 0.8
    assert settings.observation_host == "visualizer"
    assert settings.observation_port == 17_000
    assert settings.observation_interval_s == 2.5
    assert settings.diagnostics_enabled is True
    assert settings.diagnostics_output_path == Path("/data/diagnostics")


def test_cli_settings_take_precedence_without_parsing_environment_values() -> None:
    settings = resolve_runtime_settings(
        environment={name: "invalid" for name in _ENVIRONMENT_VARIABLES},
        overrides=RuntimeSettingOverrides(
            config_path=Path("cli.json"),
            grpc_socket_dir=Path("/cli/sockets"),
            status_dir=Path("/cli/status"),
            site_id="cli-site",
            edge_id="cli-edge",
            config_revision="cli-config",
            deployment_revision="cli-deployment",
            observation_host="observation-cli",
            observation_port=9_102,
            observation_interval_s=3.0,
            diagnostics_enabled=False,
            diagnostics_output_path=Path("cli-diagnostics"),
            mean_fill_duration_s=21_600.0,
            collection_threshold_center_ratio=0.75,
        ),
    )

    assert settings.grpc_socket_dir == Path("/cli/sockets")
    assert settings.edge_id == "cli-edge"
    assert settings.observation_host == "observation-cli"
    assert settings.mean_fill_duration_s == 21_600.0


def test_uses_observation_interval_default_and_optional_overrides() -> None:
    settings = resolve_runtime_settings(environment=_REQUIRED)

    assert settings.observation_interval_s == 1.0
    assert settings.diagnostics_enabled is None
    assert settings.diagnostics_output_path is None
    assert settings.mean_fill_duration_s is None
    assert settings.collection_threshold_center_ratio is None


@pytest.mark.parametrize("missing", _REQUIRED)
def test_rejects_missing_required_settings(missing: str) -> None:
    environment = {name: value for name, value in _REQUIRED.items() if name != missing}

    with pytest.raises(RuntimeSettingsError, match=missing):
        resolve_runtime_settings(environment=environment)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (CONFIG_ENVIRONMENT_VARIABLE, ""),
        (GRPC_SOCKET_DIR_ENVIRONMENT_VARIABLE, "relative"),
        (STATUS_DIR_ENVIRONMENT_VARIABLE, "relative"),
        (SITE_ID_ENVIRONMENT_VARIABLE, "bad value"),
        (EDGE_ID_ENVIRONMENT_VARIABLE, ""),
        (EDGE_ID_ENVIRONMENT_VARIABLE, "x" * 65),
        (CONFIG_REVISION_ENVIRONMENT_VARIABLE, "/bad"),
        (CONFIG_REVISION_ENVIRONMENT_VARIABLE, "x" * 65),
        (DEPLOYMENT_REVISION_ENVIRONMENT_VARIABLE, "bad value"),
        (MEAN_FILL_DURATION_ENVIRONMENT_VARIABLE, "0"),
        (COLLECTION_THRESHOLD_CENTER_ENVIRONMENT_VARIABLE, "0.05"),
        (OBSERVATION_HOST_ENVIRONMENT_VARIABLE, ""),
        (OBSERVATION_PORT_ENVIRONMENT_VARIABLE, "65536"),
        (OBSERVATION_INTERVAL_ENVIRONMENT_VARIABLE, "86400.1"),
        (DIAGNOSTICS_ENABLED_ENVIRONMENT_VARIABLE, "TRUE"),
        (DIAGNOSTICS_OUTPUT_PATH_ENVIRONMENT_VARIABLE, ""),
    ],
)
def test_rejects_invalid_environment_values(name: str, value: str) -> None:
    with pytest.raises(RuntimeSettingsError, match=name):
        resolve_runtime_settings(environment={**_REQUIRED, name: value})


@pytest.mark.parametrize("name", _ENVIRONMENT_VARIABLES)
def test_environment_setting_is_documented_in_root_readme(name: str) -> None:
    assert f"`{name}`" in (_ROOT / "README.md").read_text(encoding="utf-8")


def test_env_example_uses_uds_and_deployment_identity() -> None:
    env_example = (_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "SCRAP_LIDAR_GENERATOR_GRPC_SOCKET_DIR=/run/lidar" in env_example
    assert "SITE_ID=<site-id>" in env_example
    assert "EDGE_ID=<edge-id>" in env_example
    assert "SCRAP_LIDAR_GENERATOR_COLLECTION_THRESHOLD_CENTER_RATIO=0.90" in env_example
