"""Strict MessagePack encoding and decoding for v1 scan bodies."""

import math
from collections.abc import Iterable
from importlib import import_module
from typing import Protocol, cast

import numpy as np
from numpy.typing import NDArray

from scrap_monitoring_lidar_generator.measurement import MeasuredScan
from scrap_monitoring_lidar_generator.transport.models import (
    MAX_SIGNED_64_BIT,
    MAX_VALID_DISTANCE_M,
    MIN_VALID_DISTANCE_M,
    PROTOCOL_VERSION,
    SCAN_MESSAGE_TYPE,
    ScanMessage,
)

_FIELDS = frozenset(
    {
        "protocol_version",
        "type",
        "environment_id",
        "run_id",
        "sensor_id",
        "scan_id",
        "captured_at",
        "points",
    }
)


class _MessagePackModule(Protocol):
    def packb(self, value: object, **options: object) -> bytes:
        """Encode one object."""
        ...

    def unpackb(self, payload: bytes, **options: object) -> object:
        """Decode one complete object."""
        ...


_msgpack = cast(_MessagePackModule, import_module("msgpack"))


class ScanCodecError(ValueError):
    """A scan body is not valid MessagePack or does not match the v1 contract."""


def encode_scan_message(message: ScanMessage) -> bytes:
    """Encode one validated scan body with 64-bit angle and distance floats."""
    scan = message.measured_scan
    points = [
        [float(angle_deg), float(distance_m), int(quality)]
        for angle_deg, distance_m, quality in zip(
            scan.angles_deg,
            scan.distances_m,
            scan.qualities,
            strict=True,
        )
    ]
    document: dict[str, object] = {
        "protocol_version": message.protocol_version,
        "type": message.message_type,
        "environment_id": message.environment_id,
        "run_id": message.run_id,
        "sensor_id": message.sensor_id,
        "scan_id": message.scan_id,
        "captured_at": message.captured_at,
        "points": points,
    }
    return _msgpack.packb(
        document,
        use_bin_type=True,
        use_single_float=False,
        strict_types=True,
    )


def decode_scan_message(payload: bytes) -> ScanMessage:
    """Decode exactly one v1 scan body and reject ambiguous or invalid input."""
    if not isinstance(payload, bytes) or not payload:
        raise ScanCodecError("scan payload must be non-empty bytes")
    try:
        decoded = _msgpack.unpackb(
            payload,
            raw=False,
            use_list=False,
            strict_map_key=True,
            object_pairs_hook=_unique_string_map,
            max_str_len=len(payload),
            max_bin_len=len(payload),
            max_array_len=len(payload),
            max_map_len=len(payload),
            max_ext_len=len(payload),
        )
    except ScanCodecError:
        raise
    except Exception as error:
        raise ScanCodecError("scan payload is not one complete valid MessagePack object") from error

    if not isinstance(decoded, dict):
        raise ScanCodecError("scan payload root must be a map")
    document = cast(dict[str, object], decoded)
    actual_fields = set(document)
    if actual_fields != _FIELDS:
        missing = sorted(_FIELDS - actual_fields)
        unknown = sorted(actual_fields - _FIELDS)
        details = []
        if missing:
            details.append(f"missing fields: {', '.join(missing)}")
        if unknown:
            details.append(f"unknown fields: {', '.join(unknown)}")
        raise ScanCodecError(f"scan payload fields do not match v1 ({'; '.join(details)})")

    _require_exact_integer(document["protocol_version"], PROTOCOL_VERSION, "protocol_version")
    _require_exact_string(document["type"], SCAN_MESSAGE_TYPE, "type")
    environment_id = _require_non_empty_string(document["environment_id"], "environment_id")
    run_id = _require_non_empty_string(document["run_id"], "run_id")
    sensor_id = _require_non_empty_string(document["sensor_id"], "sensor_id")
    scan_id = _require_integer_range(document["scan_id"], 1, MAX_SIGNED_64_BIT, "scan_id")
    captured_at = _require_integer_range(
        document["captured_at"],
        0,
        MAX_SIGNED_64_BIT,
        "captured_at",
    )
    angles_deg, distances_m, qualities = _decode_points(document["points"])
    return ScanMessage(
        environment_id=environment_id,
        run_id=run_id,
        scan_id=scan_id,
        captured_at=captured_at,
        measured_scan=MeasuredScan(
            sensor_id=sensor_id,
            angles_deg=angles_deg,
            distances_m=distances_m,
            qualities=qualities,
        ),
    )


def _unique_string_map(pairs: Iterable[tuple[object, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str:
            raise ScanCodecError("scan payload map keys must be strings")
        if key in result:
            raise ScanCodecError(f"scan payload contains duplicate map key: {key}")
        result[key] = value
    return result


def _decode_points(
    value: object,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.uint8]]:
    if not isinstance(value, tuple) or not value:
        raise ScanCodecError("points must be a non-empty array")
    angles_deg = np.empty(len(value), dtype=np.float64)
    distances_m = np.empty(len(value), dtype=np.float64)
    qualities = np.empty(len(value), dtype=np.uint8)
    for index, point in enumerate(value):
        if not isinstance(point, tuple) or len(point) != 3:
            raise ScanCodecError(f"points[{index}] must be a three-item array")
        angle_deg = _require_wire_float(point[0], f"points[{index}][0]")
        distance_m = _require_wire_float(point[1], f"points[{index}][1]")
        if not 0.0 <= angle_deg < 360.0:
            raise ScanCodecError(f"points[{index}][0] must be in [0, 360)")
        if distance_m != 0.0 and not MIN_VALID_DISTANCE_M <= distance_m <= MAX_VALID_DISTANCE_M:
            raise ScanCodecError(f"points[{index}][1] must be 0 or in [0.05, 30]")
        angles_deg[index] = angle_deg
        distances_m[index] = distance_m
        qualities[index] = _require_integer_range(point[2], 0, 255, f"points[{index}][2]")
    return angles_deg, distances_m, qualities


def _require_wire_float(value: object, name: str) -> float:
    if type(value) is not float or not math.isfinite(value):
        raise ScanCodecError(f"{name} must be a finite 64-bit float")
    return value


def _require_integer_range(value: object, minimum: int, maximum: int, name: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ScanCodecError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _require_exact_integer(value: object, expected: int, name: str) -> None:
    if type(value) is not int or value != expected:
        raise ScanCodecError(f"{name} must be {expected}")


def _require_exact_string(value: object, expected: str, name: str) -> None:
    if type(value) is not str or value != expected:
        raise ScanCodecError(f"{name} must be {expected!r}")


def _require_non_empty_string(value: object, name: str) -> str:
    if type(value) is not str or not value:
        raise ScanCodecError(f"{name} must be a non-empty string")
    return value
