"""Contract schema validation tests."""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from scrap_monitoring_lidar_generator.observation import (
    decode_observation_header_line,
    decode_observation_line,
)
from scrap_monitoring_lidar_generator.transport import (
    AckMessage,
    ErrorMessage,
    decode_response_message,
    decode_scan_message,
    encode_response_message,
    encode_scan_message,
)

_ROOT = Path(__file__).parents[2]
_CONTRACTS = _ROOT / "contracts" / "v1"
_OBSERVATION_CONTRACTS = _ROOT / "contracts" / "observation" / "v1"
_FIXTURES = _CONTRACTS / "fixtures"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "name",
    [
        "environment.schema.json",
        "ack.schema.json",
        "error.schema.json",
        "generator.schema.json",
        "quality-profile.schema.json",
        "scan.schema.json",
    ],
)
def test_contract_schema_is_valid_draft_2020_12(name: str) -> None:
    Draft202012Validator.check_schema(_load_json(_CONTRACTS / name))


def test_observation_contract_schema_is_valid_draft_2020_12() -> None:
    for name in ("header.schema.json", "observation.schema.json"):
        Draft202012Validator.check_schema(_load_json(_OBSERVATION_CONTRACTS / name))


def test_observation_fixture_matches_contract() -> None:
    header_schema = _load_json(_OBSERVATION_CONTRACTS / "header.schema.json")
    observation_schema = _load_json(_OBSERVATION_CONTRACTS / "observation.schema.json")
    lines = (
        (_OBSERVATION_CONTRACTS / "fixtures" / "observation.v1.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )

    assert len(lines) == 2
    Draft202012Validator(header_schema).validate(json.loads(lines[0]))
    Draft202012Validator(observation_schema).validate(json.loads(lines[1]))
    header = decode_observation_header_line(lines[0])
    record = decode_observation_line(lines[1])
    assert header.run_id == record.run_id
    assert header.scene.sensors[0].sensor_id == "sensor-a"
    assert record.sequence == 1


def test_synthetic_environment_matches_contract() -> None:
    schema = _load_json(_CONTRACTS / "environment.schema.json")
    environment = _load_json(_ROOT / "examples" / "environment.v1.json")

    Draft202012Validator(schema).validate(environment)


@pytest.mark.parametrize(
    ("schema_name", "example_name"),
    [
        ("generator.schema.json", "generator.v1.json"),
        ("quality-profile.schema.json", "quality-profile.v1.json"),
    ],
)
def test_synthetic_generator_inputs_match_contract(
    schema_name: str,
    example_name: str,
) -> None:
    schema = _load_json(_CONTRACTS / schema_name)
    example = _load_json(_ROOT / "examples" / example_name)

    Draft202012Validator(schema).validate(example)


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


def test_messagepack_scan_fixture_matches_human_readable_contract() -> None:
    schema = _load_json(_CONTRACTS / "scan.schema.json")
    expected = _load_json(_FIXTURES / "scan.v1.json")
    payload = bytes.fromhex((_FIXTURES / "scan.v1.msgpack.hex").read_text(encoding="ascii").strip())

    message = decode_scan_message(payload)
    decoded = {
        "protocol_version": message.protocol_version,
        "type": message.message_type,
        "environment_id": message.environment_id,
        "run_id": message.run_id,
        "sensor_id": message.sensor_id,
        "scan_id": message.scan_id,
        "captured_at": message.captured_at,
        "points": [
            [float(angle_deg), float(distance_m), int(quality)]
            for angle_deg, distance_m, quality in zip(
                message.measured_scan.angles_deg,
                message.measured_scan.distances_m,
                message.measured_scan.qualities,
                strict=True,
            )
        ],
    }

    assert decoded == expected
    assert encode_scan_message(message) == payload
    Draft202012Validator(schema).validate(decoded)


@pytest.mark.parametrize("name", ["ack", "error"])
def test_messagepack_response_fixture_matches_human_readable_contract(name: str) -> None:
    schema = _load_json(_CONTRACTS / f"{name}.schema.json")
    expected = _load_json(_FIXTURES / f"{name}.v1.json")
    payload = bytes.fromhex(
        (_FIXTURES / f"{name}.v1.msgpack.hex").read_text(encoding="ascii").strip()
    )

    response = decode_response_message(payload)
    if isinstance(response, AckMessage):
        decoded = {
            "protocol_version": response.protocol_version,
            "type": response.message_type,
            "run_id": response.identity.run_id,
            "sensor_id": response.identity.sensor_id,
            "scan_id": response.identity.scan_id,
        }
    else:
        assert isinstance(response, ErrorMessage)
        decoded = {
            "protocol_version": response.protocol_version,
            "type": response.message_type,
            "code": response.code.value,
        }
        if response.message is not None:
            decoded["message"] = response.message
        if response.identity is not None:
            decoded.update(
                {
                    "run_id": response.identity.run_id,
                    "sensor_id": response.identity.sensor_id,
                    "scan_id": response.identity.scan_id,
                }
            )

    assert decoded == expected
    assert encode_response_message(response) == payload
    Draft202012Validator(schema).validate(decoded)


@pytest.mark.parametrize(
    "error",
    [
        {
            "protocol_version": 1,
            "type": "error",
            "code": "invalid_scan",
        },
        {
            "protocol_version": 1,
            "type": "error",
            "code": "temporary_unavailable",
            "run_id": "run-a",
        },
    ],
)
def test_error_contract_rejects_missing_identity_fields(error: dict[str, object]) -> None:
    schema = _load_json(_CONTRACTS / "error.schema.json")

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(error)


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
