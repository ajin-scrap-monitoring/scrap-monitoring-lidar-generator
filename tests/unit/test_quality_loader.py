"""Tests for strict synthetic quality profile loading."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from scrap_monitoring_lidar_generator.configuration import (
    ConfigurationError,
    load_quality_profile,
    parse_quality_profile,
)

_ROOT = Path(__file__).parents[2]
_EXAMPLE_PATH = _ROOT / "examples" / "quality-profile.v1.json"


@pytest.fixture
def valid_profile() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_EXAMPLE_PATH.read_text(encoding="utf-8")))


def _parse(value: dict[str, Any]) -> None:
    parse_quality_profile(json.dumps(value))


def test_loads_sparse_frequencies_into_256_values() -> None:
    profile = load_quality_profile(_EXAMPLE_PATH)
    sensor = profile.sensors[0]

    assert [item.sensor_id for item in profile.sensors] == ["lidar_1", "lidar_2"]
    assert len(sensor.valid_distance_frequencies) == 256
    assert sensor.valid_distance_frequencies[48] == 1
    assert sensor.valid_distance_frequencies[80] == 3
    assert sensor.valid_distance_frequencies[79] == 0
    assert sensor.invalid_distance_frequencies[0] == 2


def test_rejects_duplicate_sensor_ids(valid_profile: dict[str, Any]) -> None:
    valid_profile["sensors"][1] = deepcopy(valid_profile["sensors"][0])

    with pytest.raises(ConfigurationError, match="unique sensor_id"):
        _parse(valid_profile)


@pytest.mark.parametrize("quality", ["-1", "01", "256", "1.0"])
def test_rejects_invalid_quality_key(valid_profile: dict[str, Any], quality: str) -> None:
    valid_profile["sensors"][0]["valid_distance_frequencies"] = {quality: 1}

    with pytest.raises(ConfigurationError, match="0 through 255"):
        _parse(valid_profile)


@pytest.mark.parametrize("frequency", [0, -1, True, 9_223_372_036_854_775_808])
def test_rejects_invalid_frequency(valid_profile: dict[str, Any], frequency: Any) -> None:
    valid_profile["sensors"][0]["valid_distance_frequencies"] = {"10": frequency}

    with pytest.raises(ConfigurationError):
        _parse(valid_profile)


def test_rejects_empty_frequency_map(valid_profile: dict[str, Any]) -> None:
    valid_profile["sensors"][0]["invalid_distance_frequencies"] = {}

    with pytest.raises(ConfigurationError, match="at least 1 quality value"):
        _parse(valid_profile)
