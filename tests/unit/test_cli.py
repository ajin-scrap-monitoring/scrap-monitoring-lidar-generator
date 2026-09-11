"""Tests for the command-line application."""

import asyncio
from pathlib import Path

import pytest

import scrap_monitoring_lidar_generator.cli as cli
from scrap_monitoring_lidar_generator.configuration import GeneratorInputs
from scrap_monitoring_lidar_generator.runtime import GeneratorRunSummary
from scrap_monitoring_lidar_generator.transport import SenderHalt, SenderHaltCode, SenderStats

_ROOT = Path(__file__).parents[2]


def _summary(sender_halt: SenderHalt | None = None) -> GeneratorRunSummary:
    return GeneratorRunSummary(
        run_id="run-a",
        run_started_at_utc_us=123,
        generated_scans=2,
        pending_frames=1,
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
    )


def test_main_runs_requested_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[Path] = []

    async def run_config(path: Path) -> int:
        received.append(path)
        return 0

    monkeypatch.setattr(cli, "_run_config", run_config)

    assert cli.main(["--config", "generator.json"]) == 0
    assert received == [Path("generator.json")]


def test_main_requires_configuration() -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main([])

    assert exit_info.value.code == 2


def test_main_reports_configuration_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--config", "missing.json"]) == 2
    assert "configuration error:" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("halt", "expected_code"),
    [
        (None, 0),
        (
            SenderHalt(
                code=SenderHaltCode.ENVIRONMENT_MISMATCH,
                detail="wrong environment",
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
        inputs: GeneratorInputs, *, stop_event: asyncio.Event
    ) -> GeneratorRunSummary:
        assert inputs.environment.environment_id == "synthetic-room-v1"
        stop_event.set()
        return _summary(halt)

    monkeypatch.setattr(cli, "run_generator_application", run_application)

    code = asyncio.run(cli._run_config(_ROOT / "examples" / "generator.v1.json"))
    output = capsys.readouterr()

    assert code == expected_code
    assert "run_id=run-a generated=2 acknowledged=1 pending=1" in output.out
    assert ("transport halted" in output.err) is (halt is not None)


def test_help_exits_successfully(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])

    assert exit_info.value.code == 0
    assert "usage: scrap-monitoring-lidar-generator" in capsys.readouterr().out
