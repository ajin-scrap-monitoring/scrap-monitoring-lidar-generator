"""Tests for the command-line application."""

import pytest

from scrap_monitoring_lidar_generator.cli import main


def test_main_returns_success() -> None:
    assert main([]) == 0


def test_help_exits_successfully(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    assert exit_info.value.code == 0
    assert "usage: scrap-monitoring-lidar-generator" in capsys.readouterr().out
