"""Contract schema validation tests."""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

_ROOT = Path(__file__).parents[2]
_CONTRACTS = _ROOT / "contracts" / "v1"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["environment.schema.json", "scan.schema.json"])
def test_contract_schema_is_valid_draft_2020_12(name: str) -> None:
    Draft202012Validator.check_schema(_load_json(_CONTRACTS / name))


def test_synthetic_environment_matches_contract() -> None:
    schema = _load_json(_CONTRACTS / "environment.schema.json")
    environment = _load_json(_ROOT / "examples" / "environment.v1.json")

    Draft202012Validator(schema).validate(environment)


def test_scan_sequence_matches_contract() -> None:
    schema = _load_json(_CONTRACTS / "scan.schema.json")
    scan = {
        "protocol_version": 1,
        "type": "scan",
        "environment_id": "synthetic-room-v1",
        "run_id": "synthetic-run-a",
        "sensor_id": "sensor-a",
        "scan_id": 1,
        "captured_at": 1_800_000_000_000_000,
        "points": [[0.0, 2.5, 64], [359.9, 0.0, 0]],
    }

    Draft202012Validator(schema).validate(scan)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scan_id", 0),
        ("scan_id", 9_223_372_036_854_775_808),
        ("captured_at", -1),
        ("captured_at", 9_223_372_036_854_775_808),
        ("points", [[360.0, 1.0, 1]]),
        ("points", [[0.0, 0.01, 1]]),
        ("points", [[0.0, 1.0, 256]]),
    ],
)
def test_scan_contract_rejects_out_of_range_values(field: str, value: Any) -> None:
    schema = _load_json(_CONTRACTS / "scan.schema.json")
    scan = {
        "protocol_version": 1,
        "type": "scan",
        "environment_id": "synthetic-room-v1",
        "run_id": "synthetic-run-a",
        "sensor_id": "sensor-a",
        "scan_id": 1,
        "captured_at": 1_800_000_000_000_000,
        "points": [[0.0, 2.5, 64]],
    }
    scan[field] = value

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(scan)


def test_scan_contract_rejects_empty_scan() -> None:
    schema = _load_json(_CONTRACTS / "scan.schema.json")
    scan = {
        "protocol_version": 1,
        "type": "scan",
        "environment_id": "synthetic-room-v1",
        "run_id": "synthetic-run-a",
        "sensor_id": "sensor-a",
        "scan_id": 1,
        "captured_at": 1_800_000_000_000_000,
        "points": [],
    }

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(scan)


def test_scan_contract_rejects_unknown_fields() -> None:
    schema = _load_json(_CONTRACTS / "scan.schema.json")
    scan = {
        "protocol_version": 1,
        "type": "scan",
        "environment_id": "synthetic-room-v1",
        "run_id": "synthetic-run-a",
        "sensor_id": "sensor-a",
        "scan_id": 1,
        "captured_at": 1_800_000_000_000_000,
        "points": [[0.0, 2.5, 64]],
        "unknown": True,
    }

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(scan)
