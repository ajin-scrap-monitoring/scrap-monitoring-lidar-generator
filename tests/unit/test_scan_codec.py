"""Tests for strict v1 MessagePack scan body encoding."""

import math
import struct
from importlib import import_module
from typing import Protocol, cast

import numpy as np
import pytest

from scrap_monitoring_lidar_generator.measurement import MeasuredScan
from scrap_monitoring_lidar_generator.transport import (
    ScanCodecError,
    ScanMessage,
    decode_scan_message,
    encode_scan_message,
)


class _MessagePackModule(Protocol):
    def packb(self, value: object, **options: object) -> bytes:
        """Encode one test object."""
        ...


_msgpack = cast(_MessagePackModule, import_module("msgpack"))


def _message() -> ScanMessage:
    return ScanMessage(
        environment_id="synthetic-room-v1",
        run_id="synthetic-run-a",
        scan_id=1,
        captured_at=1_800_000_000_000_000,
        measured_scan=MeasuredScan(
            sensor_id="sensor-a",
            angles_deg=np.asarray([0.0, 359.9], dtype=np.float64),
            distances_m=np.asarray([2.5, 0.0], dtype=np.float64),
            qualities=np.asarray([64, 255], dtype=np.uint8),
        ),
    )


def _pack(document: object) -> bytes:
    return _msgpack.packb(document, use_bin_type=True, use_single_float=False)


def test_round_trip_preserves_fields_point_order_and_numeric_values() -> None:
    message = _message()

    payload = encode_scan_message(message)
    decoded = decode_scan_message(payload)

    assert decoded.environment_id == message.environment_id
    assert decoded.run_id == message.run_id
    assert decoded.sensor_id == message.sensor_id
    assert decoded.scan_id == message.scan_id
    assert decoded.captured_at == message.captured_at
    np.testing.assert_array_equal(
        decoded.measured_scan.angles_deg, message.measured_scan.angles_deg
    )
    np.testing.assert_array_equal(
        decoded.measured_scan.distances_m,
        message.measured_scan.distances_m,
    )
    np.testing.assert_array_equal(decoded.measured_scan.qualities, message.measured_scan.qualities)
    assert payload.count(b"\xcb" + struct.pack(">d", 0.0)) == 2
    assert b"\xcb" + struct.pack(">d", 2.5) in payload
    assert b"\xcb" + struct.pack(">d", 359.9) in payload


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("protocol_version", 2, "protocol_version"),
        ("protocol_version", True, "protocol_version"),
        ("type", "ack", "type"),
        ("environment_id", "", "environment_id"),
        ("run_id", "", "run_id"),
        ("sensor_id", "", "sensor_id"),
        ("scan_id", 0, "scan_id"),
        ("scan_id", True, "scan_id"),
        ("captured_at", -1, "captured_at"),
        ("captured_at", True, "captured_at"),
        ("points", (), "points"),
        ("points", ((0, 1.0, 1),), "64-bit float"),
        ("points", ((0.0, 1, 1),), "64-bit float"),
        ("points", ((math.nan, 1.0, 1),), "finite 64-bit float"),
        ("points", ((0.0, math.inf, 1),), "finite 64-bit float"),
        ("points", ((360.0, 1.0, 1),), "in \\[0, 360\\)"),
        ("points", ((0.0, -1.0, 1),), "0 or in \\[0.05, 30\\]"),
        ("points", ((0.0, 0.01, 1),), "0 or in \\[0.05, 30\\]"),
        ("points", ((0.0, 1.0, 256),), "points\\[0\\]\\[2\\]"),
        ("points", ((0.0, 1.0),), "three-item array"),
    ],
)
def test_decoder_rejects_invalid_contract_values(field: str, value: object, message: str) -> None:
    document: dict[str, object] = {
        "protocol_version": 1,
        "type": "scan",
        "environment_id": "environment-a",
        "run_id": "run-a",
        "sensor_id": "sensor-a",
        "scan_id": 1,
        "captured_at": 100,
        "points": ((0.0, 1.0, 1),),
    }
    document[field] = value

    with pytest.raises(ScanCodecError, match=message):
        decode_scan_message(_pack(document))


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"", "non-empty bytes"),
        (b"\x90", "root must be a map"),
        (b"\x81\xa1a\x01", "missing fields"),
        (b"\x82\xa1a\x01\xa1a\x02", "duplicate map key"),
        (b"\x81\xc4\x01a\x01", "map keys must be strings"),
    ],
)
def test_decoder_rejects_invalid_messagepack_structure(payload: bytes, message: str) -> None:
    with pytest.raises(ScanCodecError, match=message):
        decode_scan_message(payload)


def test_decoder_rejects_unknown_field_and_trailing_object() -> None:
    document: dict[str, object] = {
        "protocol_version": 1,
        "type": "scan",
        "environment_id": "environment-a",
        "run_id": "run-a",
        "sensor_id": "sensor-a",
        "scan_id": 1,
        "captured_at": 100,
        "points": ((0.0, 1.0, 1),),
        "unknown": True,
    }

    with pytest.raises(ScanCodecError, match="unknown fields: unknown"):
        decode_scan_message(_pack(document))
    with pytest.raises(ScanCodecError, match="one complete valid"):
        decode_scan_message(encode_scan_message(_message()) + b"\xc0")
