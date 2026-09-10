"""Tests for strict environment configuration loading."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from scrap_monitoring_lidar_generator.configuration import (
    ConfigurationError,
    load_environment,
    parse_environment,
)

_ROOT = Path(__file__).parents[2]


@pytest.fixture
def valid_environment() -> dict[str, Any]:
    return {
        "environment_id": "test-environment",
        "length_unit": "m",
        "angle_unit": "deg",
        "boundary_xy_m": [[0, 0], [5, 0], [5, 3], [0, 3]],
        "floor_z_m": 0,
        "top_z_m": 4,
        "sensors": [
            {
                "sensor_id": "sensor-a",
                "p0_m": [2.5, 1.5, 3],
                "u0": [0, 0, -1],
                "u90": [1, 0, 0],
            }
        ],
    }


def _parse(value: dict[str, Any]) -> None:
    parse_environment(json.dumps(value))


def test_loads_synthetic_environment() -> None:
    environment = load_environment(_ROOT / "examples" / "environment.v1.json")

    assert environment.environment_id == "synthetic-room-v1"
    assert environment.boundary_xy_m[2] == (8.0, 6.0)
    assert environment.floor_z_m == 0.0
    assert environment.top_z_m == 4.0
    assert environment.sensors[0].sensor_id == "sensor-a"
    assert environment.sensors[0].u0 == (0.0, 0.0, -1.0)


def test_rejects_duplicate_json_fields() -> None:
    with pytest.raises(ConfigurationError, match="duplicate JSON field: environment_id"):
        parse_environment('{"environment_id":"a","environment_id":"b"}')


def test_rejects_invalid_json() -> None:
    with pytest.raises(ConfigurationError, match="invalid JSON at line 1"):
        parse_environment("{")


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_rejects_non_finite_json_numbers(constant: str) -> None:
    with pytest.raises(ConfigurationError, match="non-finite JSON number"):
        parse_environment(f'{{"value": {constant}}}')


def test_rejects_unknown_fields(valid_environment: dict[str, Any]) -> None:
    valid_environment["unknown"] = True

    with pytest.raises(ConfigurationError, match="unexpected fields: unknown"):
        _parse(valid_environment)


def test_rejects_missing_fields(valid_environment: dict[str, Any]) -> None:
    del valid_environment["top_z_m"]

    with pytest.raises(ConfigurationError, match="missing fields: top_z_m"):
        _parse(valid_environment)


def test_rejects_wrong_units(valid_environment: dict[str, Any]) -> None:
    valid_environment["length_unit"] = "mm"

    with pytest.raises(ConfigurationError, match=r"\$\.length_unit must be 'm'"):
        _parse(valid_environment)


def test_rejects_non_positive_height(valid_environment: dict[str, Any]) -> None:
    valid_environment["top_z_m"] = 0

    with pytest.raises(ConfigurationError, match="top_z_m must be greater"):
        _parse(valid_environment)


def test_rejects_duplicate_sensor_ids(valid_environment: dict[str, Any]) -> None:
    duplicate = deepcopy(valid_environment["sensors"][0])
    valid_environment["sensors"].append(duplicate)

    with pytest.raises(ConfigurationError, match="unique sensor_id"):
        _parse(valid_environment)


def test_rejects_non_unit_direction(valid_environment: dict[str, Any]) -> None:
    valid_environment["sensors"][0]["u0"] = [0, 0, -2]

    with pytest.raises(ConfigurationError, match="u0 must be a unit vector"):
        _parse(valid_environment)


def test_rejects_non_orthogonal_directions(valid_environment: dict[str, Any]) -> None:
    valid_environment["sensors"][0]["u90"] = [0, 0, 1]

    with pytest.raises(ConfigurationError, match="must be orthogonal"):
        _parse(valid_environment)


def test_accepts_concave_clockwise_boundary(valid_environment: dict[str, Any]) -> None:
    valid_environment["boundary_xy_m"] = [[0, 4], [2, 2], [4, 4], [4, 0], [0, 0]]

    _parse(valid_environment)


def test_rejects_self_intersecting_boundary_with_non_zero_signed_area(
    valid_environment: dict[str, Any],
) -> None:
    valid_environment["boundary_xy_m"] = [[0, 0], [4, 0], [0, 3], [4, 3], [2, 4]]

    with pytest.raises(ConfigurationError, match="must not self-intersect"):
        _parse(valid_environment)


@pytest.mark.parametrize(
    "boundary",
    [
        [[0, 0], [1, 1], [0, 1], [1, 0]],
        [[0, 0], [1, 0], [0, 0], [0, 1]],
        [[0, 0], [1, 0], [2, 0]],
    ],
)
def test_rejects_invalid_boundaries(
    valid_environment: dict[str, Any], boundary: list[list[int]]
) -> None:
    valid_environment["boundary_xy_m"] = boundary

    with pytest.raises(ConfigurationError):
        _parse(valid_environment)
