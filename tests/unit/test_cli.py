"""Tests for the command-line application."""

import asyncio
from pathlib import Path

import pytest

import scrap_monitoring_lidar_generator.cli as cli
from scrap_monitoring_lidar_generator._cli_settings import RuntimeSettings
from scrap_monitoring_lidar_generator.configuration import load_generator_inputs
from scrap_monitoring_lidar_generator.observation import ObservationPublisherStats
from scrap_monitoring_lidar_generator.runtime import GeneratorRunSummary
from scrap_monitoring_lidar_generator.scan_stream import ScanServerStats

_ROOT = Path(__file__).parents[2]
_CONFIG = _ROOT / "examples" / "generator.v2.json"


def _settings(**overrides: object) -> RuntimeSettings:
    values: dict[str, object] = {
        "config_path": _CONFIG,
        "grpc_socket_dir": Path("/run/lidar"),
        "status_dir": Path("/status"),
        "site_id": "site-a",
        "edge_id": "edge-a",
        "config_revision": "config-r1",
        "deployment_revision": "deployment-r1",
        "observation_host": "visualizer",
        "observation_port": 17_000,
        "observation_interval_s": 1.0,
        "diagnostics_enabled": None,
        "diagnostics_output_path": None,
        "mean_fill_duration_s": None,
        "collection_threshold_center_ratio": None,
    }
    values.update(overrides)
    return RuntimeSettings(**values)  # type: ignore[arg-type]


def _environment() -> dict[str, str]:
    return {
        "SCRAP_LIDAR_GENERATOR_CONFIG": str(_CONFIG),
        "SCRAP_LIDAR_GENERATOR_GRPC_SOCKET_DIR": "/run/lidar",
        "SCRAP_LIDAR_GENERATOR_STATUS_DIR": "/status",
        "SITE_ID": "site-a",
        "EDGE_ID": "edge-a",
        "CONFIG_REVISION": "config-r1",
        "DEPLOYMENT_REVISION": "deployment-r1",
        "SCRAP_LIDAR_GENERATOR_OBSERVATION_HOST": "visualizer",
        "SCRAP_LIDAR_GENERATOR_OBSERVATION_PORT": "17000",
    }


def test_main_reads_complete_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[RuntimeSettings] = []

    async def run_config(settings: RuntimeSettings) -> int:
        received.append(settings)
        return 0

    monkeypatch.setattr(cli, "_run_config", run_config)

    assert cli.main([], environment=_environment()) == 0
    assert received == [_settings()]


def test_main_cli_values_override_invalid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[RuntimeSettings] = []

    async def run_config(settings: RuntimeSettings) -> int:
        received.append(settings)
        return 0

    monkeypatch.setattr(cli, "_run_config", run_config)
    arguments = [
        "--config",
        str(_CONFIG),
        "--grpc-socket-dir",
        "/run/lidar",
        "--status-dir",
        "/status",
        "--site-id",
        "site-a",
        "--edge-id",
        "edge-a",
        "--config-revision",
        "config-r1",
        "--deployment-revision",
        "deployment-r1",
        "--observation-host",
        "visualizer",
        "--observation-port",
        "17000",
    ]

    assert cli.main(arguments, environment={name: "invalid" for name in _environment()}) == 0
    assert received == [_settings()]


def test_main_reports_missing_deployment_setting(capsys: pytest.CaptureFixture[str]) -> None:
    environment = _environment()
    del environment["EDGE_ID"]

    assert cli.main([], environment=environment) == 2
    assert "EDGE_ID" in capsys.readouterr().err


def test_runtime_overrides_only_selected_model_values() -> None:
    inputs = load_generator_inputs(_CONFIG)

    updated = cli._apply_runtime_overrides(
        inputs,
        _settings(
            mean_fill_duration_s=43_200.0,
            collection_threshold_center_ratio=0.8,
            diagnostics_enabled=False,
            diagnostics_output_path=Path("recordings"),
        ),
    )

    assert updated.generator.scenario.mean_fill_duration_s == 43_200.0
    assert updated.generator.scenario.collection_threshold_range == pytest.approx((0.75, 0.85))
    assert updated.generator.diagnostics.enabled is False
    assert updated.generator.diagnostics.output_path == _CONFIG.parent / "recordings"
    assert inputs.generator.scenario.mean_fill_duration_s == 86_400.0


def test_run_config_passes_contract_identity_and_prints_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    received: dict[str, object] = {}

    async def run_application(inputs: object, **kwargs: object) -> GeneratorRunSummary:
        received.update(kwargs)
        stop_event = kwargs["stop_event"]
        assert isinstance(stop_event, asyncio.Event)
        stop_event.set()
        return GeneratorRunSummary(
            run_id="run-a",
            run_started_at_utc_us=123,
            generated_scans=4,
            scan_server_stats=ScanServerStats(
                published_frames=2,
                frame_loss=0,
                subscribers=1,
            ),
            observation_endpoint="visualizer:17000",
            observation_stats=ObservationPublisherStats(
                accepted_records=1,
                sent_records=1,
                dropped_records=0,
                connection_failures=0,
            ),
        )

    monkeypatch.setattr(cli, "run_generator_application", run_application)

    assert asyncio.run(cli._run_config(_settings())) == 0
    assert received["grpc_socket_dir"] == Path("/run/lidar")
    assert received["edge_id"] == "edge-a"
    assert received["config_revision"] == "config-r1"
    assert "scan_stream published=2" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("--grpc-socket-dir", "relative"),
        ("--status-dir", "relative"),
        ("--edge-id", "bad value"),
        ("--edge-id", "x" * 65),
        ("--config-revision", "x" * 65),
        ("--observation-port", "0"),
        ("--mean-fill-duration-s", "0"),
        ("--collection-threshold-center-ratio", "0.05"),
    ],
)
def test_main_rejects_invalid_runtime_arguments(argument: str, value: str) -> None:
    with pytest.raises(SystemExit) as captured:
        cli.main([argument, value], environment=_environment())
    assert captured.value.code == 2
