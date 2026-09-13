"""Tests for the command-line application."""

import asyncio
from pathlib import Path
from typing import cast

import pytest

import scrap_monitoring_lidar_generator.cli as cli
from scrap_monitoring_lidar_generator._cli_settings import RuntimeSettings
from scrap_monitoring_lidar_generator.configuration import GeneratorInputs, load_generator_inputs
from scrap_monitoring_lidar_generator.observation import ObservationPublisherStats
from scrap_monitoring_lidar_generator.runtime import (
    GeneratorRunSummary,
    generator_input_fingerprint,
)
from scrap_monitoring_lidar_generator.transport import (
    SenderHalt,
    SenderHaltCallback,
    SenderHaltCode,
    SenderStats,
)

_ROOT = Path(__file__).parents[2]


def _settings(
    *,
    config_path: Path | None = None,
    scan_host: str | None = None,
    scan_port: int | None = None,
    observation_host: str = "127.0.0.1",
    observation_port: int = 9100,
    observation_interval_s: float = 1.0,
    diagnostics_enabled: bool | None = None,
    diagnostics_output_path: Path | None = None,
    mean_fill_duration_s: float | None = None,
    collection_threshold_center_ratio: float | None = None,
) -> RuntimeSettings:
    return RuntimeSettings(
        config_path=config_path or _ROOT / "examples" / "generator.v1.json",
        scan_host=scan_host,
        scan_port=scan_port,
        observation_host=observation_host,
        observation_port=observation_port,
        observation_interval_s=observation_interval_s,
        diagnostics_enabled=diagnostics_enabled,
        diagnostics_output_path=diagnostics_output_path,
        mean_fill_duration_s=mean_fill_duration_s,
        collection_threshold_center_ratio=collection_threshold_center_ratio,
    )


def _summary(sender_halt: SenderHalt | None = None) -> GeneratorRunSummary:
    return GeneratorRunSummary(
        run_id="run-a",
        run_started_at_utc_us=123,
        generated_scans=2,
        pending_frames=1,
        pending_bytes=1234,
        sender_stats=SenderStats(
            enqueued_frames=2,
            sent_frames=1,
            acknowledged_frames=1,
            rejected_frames=0,
            expired_frames=0,
            capacity_discarded_frames=0,
            oversized_frames=0,
            connection_failures=0,
        ),
        sender_halt=sender_halt,
        observation_endpoint="127.0.0.1:9100",
        observation_stats=ObservationPublisherStats(
            accepted_records=2,
            sent_records=1,
            dropped_records=1,
            connection_failures=0,
        ),
    )


def test_main_runs_requested_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[RuntimeSettings] = []

    async def run_config(settings: RuntimeSettings) -> int:
        received.append(settings)
        return 0

    monkeypatch.setattr(cli, "_run_config", run_config)

    assert (
        cli.main(
            [
                "--config",
                "generator.json",
                "--observation-host",
                "127.0.0.1",
                "--observation-port",
                "9100",
            ],
            environment={},
        )
        == 0
    )
    assert received == [_settings(config_path=Path("generator.json"))]


def test_main_reads_environment_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[RuntimeSettings] = []

    async def run_config(settings: RuntimeSettings) -> int:
        received.append(settings)
        return 0

    monkeypatch.setattr(cli, "_run_config", run_config)

    assert (
        cli.main(
            [],
            environment={
                "SCRAP_LIDAR_GENERATOR_CONFIG": "generator.json",
                "SCRAP_LIDAR_GENERATOR_MEAN_FILL_DURATION_S": "43200",
                "SCRAP_LIDAR_GENERATOR_COLLECTION_THRESHOLD_CENTER_RATIO": "0.8",
                "SCRAP_LIDAR_GENERATOR_SCAN_HOST": "height-calculation",
                "SCRAP_LIDAR_GENERATOR_SCAN_PORT": "9001",
                "SCRAP_LIDAR_GENERATOR_OBSERVATION_HOST": "visualizer",
                "SCRAP_LIDAR_GENERATOR_OBSERVATION_PORT": "9101",
                "SCRAP_LIDAR_GENERATOR_OBSERVATION_INTERVAL_S": "2",
                "SCRAP_LIDAR_GENERATOR_DIAGNOSTICS_ENABLED": "true",
                "SCRAP_LIDAR_GENERATOR_DIAGNOSTICS_OUTPUT_PATH": "/data/diagnostics",
            },
        )
        == 0
    )
    assert received == [
        _settings(
            config_path=Path("generator.json"),
            scan_host="height-calculation",
            scan_port=9001,
            mean_fill_duration_s=43_200.0,
            collection_threshold_center_ratio=0.8,
            observation_host="visualizer",
            observation_port=9101,
            observation_interval_s=2.0,
            diagnostics_enabled=True,
            diagnostics_output_path=Path("/data/diagnostics"),
        )
    ]


def test_main_reports_missing_configuration(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([], environment={}) == 2

    assert "SCRAP_LIDAR_GENERATOR_CONFIG" in capsys.readouterr().err


def test_main_reports_invalid_environment_setting(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        cli.main(
            [],
            environment={
                "SCRAP_LIDAR_GENERATOR_CONFIG": "generator.json",
                "SCRAP_LIDAR_GENERATOR_OBSERVATION_HOST": "visualizer",
                "SCRAP_LIDAR_GENERATOR_OBSERVATION_PORT": "invalid",
            },
        )
        == 2
    )

    assert "SCRAP_LIDAR_GENERATOR_OBSERVATION_PORT" in capsys.readouterr().err


@pytest.mark.parametrize("missing", ["host", "port"])
def test_main_requires_observation_endpoint(
    missing: str, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments = ["--config", "generator.json"]
    if missing != "host":
        arguments.extend(("--observation-host", "127.0.0.1"))
    if missing != "port":
        arguments.extend(("--observation-port", "9100"))

    assert cli.main(arguments, environment={}) == 2

    assert "configuration error:" in capsys.readouterr().err


def test_main_passes_observation_stream_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[RuntimeSettings] = []

    async def run_config(settings: RuntimeSettings) -> int:
        received.append(settings)
        return 0

    monkeypatch.setattr(cli, "_run_config", run_config)

    assert (
        cli.main(
            [
                "--config",
                "generator.json",
                "--observation-host",
                "visualizer",
                "--observation-port",
                "9200",
                "--observation-interval-s",
                "2",
            ],
            environment={},
        )
        == 0
    )
    assert received == [
        _settings(
            config_path=Path("generator.json"),
            observation_host="visualizer",
            observation_port=9200,
            observation_interval_s=2.0,
        )
    ]


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("--scan-host", ""),
        ("--scan-port", "0"),
        ("--scan-port", "65536"),
        ("--mean-fill-duration-s", "0"),
        ("--collection-threshold-center-ratio", "0.05"),
        ("--collection-threshold-center-ratio", "0.951"),
        ("--observation-port", "0"),
        ("--observation-port", "65536"),
        ("--observation-interval-s", "86400.1"),
        ("--diagnostics-enabled", "yes"),
        ("--diagnostics-output-path", ""),
    ],
)
def test_main_rejects_invalid_runtime_arguments(argument: str, value: str) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(
            [
                "--config",
                "generator.json",
                "--observation-host",
                "127.0.0.1",
                "--observation-port",
                "9100",
                argument,
                value,
            ],
            environment={},
        )

    assert exit_info.value.code == 2


def test_main_reports_configuration_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        cli.main(
            [
                "--config",
                "missing.json",
                "--observation-host",
                "127.0.0.1",
                "--observation-port",
                "9100",
            ],
            environment={},
        )
        == 2
    )
    assert "configuration error:" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("halt", "expected_code"),
    [
        (None, 0),
        (
            SenderHalt(
                code=SenderHaltCode.ENVIRONMENT_MISMATCH,
                detail="wrong environment",
                sensor_id="sensor-b",
            ),
            1,
        ),
    ],
)
def test_run_config_reports_final_delivery_state(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    halt: SenderHalt | None,
    expected_code: int,
) -> None:
    async def run_application(
        inputs: GeneratorInputs, *, stop_event: asyncio.Event, **kwargs: object
    ) -> GeneratorRunSummary:
        assert inputs.environment.environment_id == "synthetic-scrap-pit-v1"
        assert inputs.generator.transport.host == "receiver"
        assert inputs.generator.transport.port == 9000
        callback = cast(SenderHaltCallback | None, kwargs.get("on_sender_halt"))
        assert callback is None or callable(callback)
        if halt is not None and callback is not None:
            callback(halt)
        stop_event.set()
        return _summary(halt)

    monkeypatch.setattr(cli, "run_generator_application", run_application)

    code = asyncio.run(cli._run_config(_settings()))
    output = capsys.readouterr()

    assert code == expected_code
    assert "run_id=run-a generated=2 acknowledged=1 pending=1" in output.out
    assert (
        "transport enqueued=2 sent=1 acknowledged=1 rejected=0 expired=0 "
        "capacity_discarded=0 oversized=0 connection_failures=0 "
        "pending_frames=1 pending_bytes=1234"
    ) in output.out
    assert "observation=active sent=1 dropped=1" in output.out
    assert "127.0.0.1:9100" not in output.out
    assert ("transport halted" in output.err) is (halt is not None)
    assert output.err.count("transport halted") == int(halt is not None)
    assert ("sensor_id=sensor-b" in output.err) is (halt is not None)


def test_run_config_applies_scan_endpoint_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_application(
        inputs: GeneratorInputs, *, stop_event: asyncio.Event, **kwargs: object
    ) -> GeneratorRunSummary:
        assert inputs.generator.transport.host == "height-calculation"
        assert inputs.generator.transport.port == 9200
        assert inputs.generator.scenario.mean_fill_duration_s == 43_200.0
        assert inputs.generator.scenario.collection_threshold_range == pytest.approx((0.75, 0.85))
        assert inputs.generator.diagnostics.enabled is False
        assert inputs.generator.diagnostics.output_path == _ROOT / "examples" / "runtime-output"
        stop_event.set()
        return _summary()

    monkeypatch.setattr(cli, "run_generator_application", run_application)

    code = asyncio.run(
        cli._run_config(
            _settings(
                scan_host="height-calculation",
                scan_port=9200,
                diagnostics_enabled=False,
                diagnostics_output_path=Path("runtime-output"),
                mean_fill_duration_s=43_200.0,
                collection_threshold_center_ratio=0.8,
            )
        )
    )

    assert code == 0


def test_runtime_scan_overrides_preserve_each_json_fallback() -> None:
    inputs = load_generator_inputs(_ROOT / "examples" / "generator.v1.json")

    host_override = cli._apply_runtime_overrides(inputs, _settings(scan_host="height-calculation"))
    port_override = cli._apply_runtime_overrides(inputs, _settings(scan_port=9200))

    assert host_override.generator.transport.host == "height-calculation"
    assert host_override.generator.transport.port == 9000
    assert port_override.generator.transport.host == "receiver"
    assert port_override.generator.transport.port == 9200
    assert inputs.generator.transport.host == "receiver"
    assert inputs.generator.transport.port == 9000


def test_runtime_diagnostics_overrides_preserve_each_json_fallback() -> None:
    config_path = _ROOT / "examples" / "generator.v1.json"
    inputs = load_generator_inputs(config_path)

    enabled_override = cli._apply_runtime_overrides(
        inputs, _settings(config_path=config_path, diagnostics_enabled=False)
    )
    path_override = cli._apply_runtime_overrides(
        inputs,
        _settings(
            config_path=config_path,
            diagnostics_output_path=Path("runtime-output"),
        ),
    )

    assert enabled_override.generator.diagnostics.enabled is False
    assert enabled_override.generator.diagnostics.output_path == _ROOT / "examples" / "diagnostics"
    assert path_override.generator.diagnostics.enabled is True
    assert path_override.generator.diagnostics.output_path == _ROOT / "examples" / "runtime-output"
    assert inputs.generator.diagnostics.enabled is True
    assert inputs.generator.diagnostics.output_path == _ROOT / "examples" / "diagnostics"


def test_runtime_mean_fill_duration_override_changes_generation_fingerprint() -> None:
    config_path = _ROOT / "examples" / "generator.v1.json"
    inputs = load_generator_inputs(config_path)

    overridden = cli._apply_runtime_overrides(
        inputs,
        _settings(config_path=config_path, mean_fill_duration_s=43_200.0),
    )

    assert overridden.generator.scenario.mean_fill_duration_s == 43_200.0
    assert generator_input_fingerprint(overridden) != generator_input_fingerprint(inputs)


def test_runtime_collection_threshold_override_preserves_half_range() -> None:
    config_path = _ROOT / "examples" / "generator.v1.json"
    inputs = load_generator_inputs(config_path)

    overridden = cli._apply_runtime_overrides(
        inputs,
        _settings(
            config_path=config_path,
            collection_threshold_center_ratio=0.9,
        ),
    )

    assert overridden.generator.scenario.collection_threshold_range == pytest.approx((0.85, 0.95))
    assert inputs.generator.scenario.collection_threshold_range == (0.85, 0.95)
    assert generator_input_fingerprint(overridden) != generator_input_fingerprint(inputs)


def test_runtime_overrides_do_not_change_generation_fingerprint() -> None:
    config_path = _ROOT / "examples" / "generator.v1.json"
    inputs = load_generator_inputs(config_path)

    overridden = cli._apply_runtime_overrides(
        inputs,
        _settings(
            config_path=config_path,
            scan_host="height-calculation",
            scan_port=9200,
            diagnostics_enabled=False,
            diagnostics_output_path=Path("runtime-output"),
        ),
    )

    assert generator_input_fingerprint(overridden) == generator_input_fingerprint(inputs)


def test_help_exits_successfully(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"], environment={})

    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert "usage: scrap-monitoring-lidar-generator" in output
    assert "SCRAP_LIDAR_GENERATOR_CONFIG" in output
    assert "SCRAP_LIDAR_GENERATOR_MEAN_FILL_DURATION_S" in output
    assert "--collection-threshold-center-ratio" in output
    assert "SCRAP_LIDAR_GENERATOR_DIAGNOSTICS_OUTPUT_PATH" in output
