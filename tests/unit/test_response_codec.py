"""Tests for strict v1 MessagePack receiver responses."""

from importlib import import_module
from typing import Protocol, cast

import pytest

from scrap_monitoring_lidar_generator.transport import (
    AckMessage,
    ErrorCode,
    ErrorMessage,
    ResponseCodecError,
    ScanIdentity,
    decode_response_message,
    encode_response_message,
)


class _MessagePackModule(Protocol):
    def packb(self, value: object, **options: object) -> bytes:
        """Encode one test object."""
        ...


_msgpack = cast(_MessagePackModule, import_module("msgpack"))
_IDENTITY = ScanIdentity(run_id="run-a", sensor_id="sensor-a", scan_id=7)


def _pack(document: object) -> bytes:
    return _msgpack.packb(document, use_bin_type=True)


@pytest.mark.parametrize(
    "response",
    [
        AckMessage(identity=_IDENTITY),
        ErrorMessage(code=ErrorCode.INVALID_SCAN, message="bad point", identity=_IDENTITY),
        ErrorMessage(code=ErrorCode.TEMPORARY_UNAVAILABLE),
        ErrorMessage(code=ErrorCode.ENVIRONMENT_MISMATCH, identity=_IDENTITY),
        ErrorMessage(code=ErrorCode.UNSUPPORTED_VERSION, message="version 2"),
    ],
)
def test_response_round_trip(response: AckMessage | ErrorMessage) -> None:
    assert decode_response_message(encode_response_message(response)) == response


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({"protocol_version": 2, "type": "ack"}, "protocol_version"),
        ({"protocol_version": 1, "type": "unknown"}, "type"),
        (
            {
                "protocol_version": 1,
                "type": "ack",
                "run_id": "run-a",
                "scan_id": 1,
            },
            "missing fields: sensor_id",
        ),
        (
            {
                "protocol_version": 1,
                "type": "ack",
                "run_id": "run-a",
                "sensor_id": "sensor-a",
                "scan_id": True,
            },
            "scan_id",
        ),
        (
            {
                "protocol_version": 1,
                "type": "ack",
                "run_id": "run-a",
                "sensor_id": "sensor-a",
                "scan_id": 1,
                "unknown": True,
            },
            "unknown fields",
        ),
        (
            {
                "protocol_version": 1,
                "type": "error",
                "code": "not_supported",
            },
            "supported v1 error code",
        ),
        (
            {
                "protocol_version": 1,
                "type": "error",
                "code": "invalid_scan",
            },
            "must include scan identity",
        ),
        (
            {
                "protocol_version": 1,
                "type": "error",
                "code": "temporary_unavailable",
                "run_id": "run-a",
            },
            "all present or all absent",
        ),
        (
            {
                "protocol_version": 1,
                "type": "error",
                "code": "temporary_unavailable",
                "message": "",
            },
            "non-empty string",
        ),
    ],
)
def test_decoder_rejects_invalid_response_contract(
    document: dict[str, object], message: str
) -> None:
    with pytest.raises(ResponseCodecError, match=message):
        decode_response_message(_pack(document))


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"", "non-empty bytes"),
        (b"\x90", "root must be a map"),
        (b"\x82\xa1a\x01\xa1a\x02", "duplicate map key"),
        (b"\x81\xc4\x01a\x01", "map keys must be strings"),
    ],
)
def test_decoder_rejects_invalid_messagepack_structure(payload: bytes, message: str) -> None:
    with pytest.raises(ResponseCodecError, match=message):
        decode_response_message(payload)


def test_decoder_rejects_trailing_object() -> None:
    payload = encode_response_message(AckMessage(identity=_IDENTITY))

    with pytest.raises(ResponseCodecError, match="one complete valid"):
        decode_response_message(payload + b"\xc0")


def test_models_reject_invalid_identity_and_invalid_scan_without_identity() -> None:
    with pytest.raises(ValueError, match="run_id"):
        ScanIdentity(run_id="", sensor_id="sensor-a", scan_id=1)
    with pytest.raises(ValueError, match="scan_id"):
        ScanIdentity(run_id="run-a", sensor_id="sensor-a", scan_id=0)
    with pytest.raises(ValueError, match="scan_id"):
        ScanIdentity(run_id="run-a", sensor_id="sensor-a", scan_id=True)
    with pytest.raises(ValueError, match="must include scan identity"):
        ErrorMessage(code=ErrorCode.INVALID_SCAN)
