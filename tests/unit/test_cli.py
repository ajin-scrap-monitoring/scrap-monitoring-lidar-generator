"""Tests for the command-line application."""

import asyncio
from pathlib import Path
from typing import cast

import pytest

import scrap_monitoring_lidar_generator.cli as cli
from scrap_monitoring_lidar_generator.configuration import GeneratorInputs
from scrap_monitoring_lidar_generator.observation import ObservationPublisherStats
from scrap_monitoring_lidar_generator.runtime import GeneratorRunSummary
from scrap_monitoring_lidar_generator.transport import (
    SenderHalt,
    SenderHaltCallback,
    SenderHaltCode,
    SenderStats,
)

_ROOT = Path(__file__).parents[2]


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
    received: dict[str, object] = {}

    async def run_config(path: Path, **kwargs: object) -> int:
        received["path"] = path
        received.update(kwargs)
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
            ]
        )
        == 0
    )
    assert received == {
        "path": Path("generator.json"),
        "observation_host": "127.0.0.1",
        "observation_port": 9100,
        "observation_interval_s": 1.0,
    }


def test_main_requires_configuration() -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main([])

    assert exit_info.value.code == 2


@pytest.mark.parametrize("missing", ["host", "port"])
def test_main_requires_observation_endpoint(missing: str) -> None:
    arguments = ["--config", "generator.json"]
    if missing != "host":
        arguments.extend(("--observation-host", "127.0.0.1"))
    if missing != "port":
        arguments.extend(("--observation-port", "9100"))

    with pytest.raises(SystemExit) as exit_info:
        cli.main(arguments)

    assert exit_info.value.code == 2


def test_main_passes_observation_stream_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    received: dict[str, object] = {}

    async def run_config(path: Path, **kwargs: object) -> int:
        received["path"] = path
        received.update(kwargs)
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
            ]
        )
        == 0
    )
    assert received == {
        "path": Path("generator.json"),
        "observation_host": "visualizer",
        "observation_port": 9200,
        "observation_interval_s": 2.0,
    }


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("--observation-port", "0"),
        ("--observation-port", "65536"),
        ("--observation-interval-s", "86400.1"),
    ],
)
def test_main_rejects_invalid_observation_arguments(argument: str, value: str) -> None:
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
            ]
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
            ]
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
        callback = cast(SenderHaltCallback | None, kwargs.get("on_sender_halt"))
        assert callback is None or callable(callback)
        if halt is not None and callback is not None:
            callback(halt)
        stop_event.set()
        return _summary(halt)

    monkeypatch.setattr(cli, "run_generator_application", run_application)

    code = asyncio.run(cli._run_config(_ROOT / "examples" / "generator.v1.json"))
    output = capsys.readouterr()

    assert code == expected_code
    assert "run_id=run-a generated=2 acknowledged=1 pending=1" in output.out
    assert (
        "transport enqueued=2 sent=1 acknowledged=1 rejected=0 expired=0 "
        "capacity_discarded=0 oversized=0 connection_failures=0 "
        "pending_frames=1 pending_bytes=1234"
    ) in output.out
    assert "observation=127.0.0.1:9100 sent=1 dropped=1" in output.out
    assert ("transport halted" in output.err) is (halt is not None)
    assert output.err.count("transport halted") == int(halt is not None)
    assert ("sensor_id=sensor-b" in output.err) is (halt is not None)


def test_help_exits_successfully(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])

    assert exit_info.value.code == 0
    assert "usage: scrap-monitoring-lidar-generator" in capsys.readouterr().out
