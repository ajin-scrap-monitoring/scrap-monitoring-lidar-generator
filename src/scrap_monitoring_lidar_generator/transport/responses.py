"""Versioned receiver response models and MessagePack codec."""

from dataclasses import dataclass
from enum import StrEnum

from scrap_monitoring_lidar_generator.transport._messagepack import (
    MessagePackDocumentError,
    pack_messagepack_document,
    unpack_messagepack_map,
)
from scrap_monitoring_lidar_generator.transport.models import MAX_SIGNED_64_BIT, PROTOCOL_VERSION

ACK_MESSAGE_TYPE = "ack"
ERROR_MESSAGE_TYPE = "error"
_ACK_FIELDS = frozenset({"protocol_version", "type", "run_id", "sensor_id", "scan_id"})
_ERROR_BASE_FIELDS = frozenset({"protocol_version", "type", "code"})
_ERROR_OPTIONAL_FIELDS = frozenset({"message", "run_id", "sensor_id", "scan_id"})
_IDENTITY_FIELDS = frozenset({"run_id", "sensor_id", "scan_id"})


class ErrorCode(StrEnum):
    """Receiver error categories in the public v1 contract."""

    TEMPORARY_UNAVAILABLE = "temporary_unavailable"
    INVALID_SCAN = "invalid_scan"
    ENVIRONMENT_MISMATCH = "environment_mismatch"
    UNSUPPORTED_VERSION = "unsupported_version"


@dataclass(frozen=True, slots=True)
class ScanIdentity:
    """Identity shared by one scan and its receiver response."""

    run_id: str
    sensor_id: str
    scan_id: int

    def __post_init__(self) -> None:
        _require_non_empty_string(self.run_id, "response run_id")
        _require_non_empty_string(self.sensor_id, "response sensor_id")
        _require_scan_id(self.scan_id, "response scan_id")


@dataclass(frozen=True, slots=True)
class AckMessage:
    """Positive acknowledgement for one processed or stored scan."""

    identity: ScanIdentity

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ScanIdentity):
            raise ValueError("ack response must include a ScanIdentity")

    @property
    def protocol_version(self) -> int:
        """Return the fixed public response contract version."""
        return PROTOCOL_VERSION

    @property
    def message_type(self) -> str:
        """Return the fixed acknowledgement type."""
        return ACK_MESSAGE_TYPE


@dataclass(frozen=True, slots=True)
class ErrorMessage:
    """Negative receiver response with optional scan identity."""

    code: ErrorCode
    message: str | None = None
    identity: ScanIdentity | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, ErrorCode):
            raise ValueError("response error code must be an ErrorCode")
        if self.message is not None:
            _require_non_empty_string(self.message, "response error message")
        if self.identity is not None and not isinstance(self.identity, ScanIdentity):
            raise ValueError("error response identity must be a ScanIdentity")
        if self.code is ErrorCode.INVALID_SCAN and self.identity is None:
            raise ValueError("invalid_scan response must include scan identity")

    @property
    def protocol_version(self) -> int:
        """Return the fixed public response contract version."""
        return PROTOCOL_VERSION

    @property
    def message_type(self) -> str:
        """Return the fixed error type."""
        return ERROR_MESSAGE_TYPE


type ResponseMessage = AckMessage | ErrorMessage


class ResponseCodecError(ValueError):
    """A response body is not valid MessagePack or does not match v1."""


def encode_response_message(response: ResponseMessage) -> bytes:
    """Encode one validated acknowledgement or error body."""
    if isinstance(response, AckMessage):
        identity = response.identity
        document: dict[str, object] = {
            "protocol_version": response.protocol_version,
            "type": response.message_type,
            "run_id": identity.run_id,
            "sensor_id": identity.sensor_id,
            "scan_id": identity.scan_id,
        }
    elif isinstance(response, ErrorMessage):
        document = {
            "protocol_version": response.protocol_version,
            "type": response.message_type,
            "code": response.code.value,
        }
        if response.message is not None:
            document["message"] = response.message
        if response.identity is not None:
            document.update(
                {
                    "run_id": response.identity.run_id,
                    "sensor_id": response.identity.sensor_id,
                    "scan_id": response.identity.scan_id,
                }
            )
    else:
        raise TypeError("response must be an AckMessage or ErrorMessage")
    return pack_messagepack_document(document)


def decode_response_message(payload: bytes) -> ResponseMessage:
    """Decode exactly one strict v1 acknowledgement or error body."""
    try:
        document = unpack_messagepack_map(payload, "response payload")
    except MessagePackDocumentError as error:
        raise ResponseCodecError(str(error)) from error

    _require_exact_integer(document.get("protocol_version"), PROTOCOL_VERSION, "protocol_version")
    message_type = document.get("type")
    if message_type == ACK_MESSAGE_TYPE:
        _require_exact_fields(document, _ACK_FIELDS)
        return AckMessage(identity=_decode_identity(document))
    if message_type == ERROR_MESSAGE_TYPE:
        return _decode_error(document)
    raise ResponseCodecError("type must be 'ack' or 'error'")


def _decode_error(document: dict[str, object]) -> ErrorMessage:
    actual_fields = set(document)
    allowed_fields = _ERROR_BASE_FIELDS | _ERROR_OPTIONAL_FIELDS
    if not actual_fields >= _ERROR_BASE_FIELDS or not actual_fields <= allowed_fields:
        _raise_field_mismatch(actual_fields, _ERROR_BASE_FIELDS, allowed_fields)

    code_value = document["code"]
    if type(code_value) is not str:
        raise ResponseCodecError("code must be a string")
    try:
        code = ErrorCode(code_value)
    except ValueError as error:
        raise ResponseCodecError("code is not a supported v1 error code") from error

    identity_fields = actual_fields & _IDENTITY_FIELDS
    if identity_fields and identity_fields != _IDENTITY_FIELDS:
        raise ResponseCodecError("error identity fields must be all present or all absent")
    identity = _decode_identity(document) if identity_fields else None

    message_value = document.get("message")
    try:
        message = (
            _require_non_empty_string(message_value, "message") if "message" in document else None
        )
    except ValueError as error:
        raise ResponseCodecError(str(error)) from error
    try:
        return ErrorMessage(code=code, message=message, identity=identity)
    except ValueError as error:
        raise ResponseCodecError(str(error)) from error


def _decode_identity(document: dict[str, object]) -> ScanIdentity:
    try:
        return ScanIdentity(
            run_id=_require_non_empty_string(document["run_id"], "run_id"),
            sensor_id=_require_non_empty_string(document["sensor_id"], "sensor_id"),
            scan_id=_require_scan_id(document["scan_id"], "scan_id"),
        )
    except ValueError as error:
        raise ResponseCodecError(str(error)) from error


def _require_exact_fields(document: dict[str, object], expected: frozenset[str]) -> None:
    actual = set(document)
    if actual != expected:
        _raise_field_mismatch(actual, expected, expected)


def _raise_field_mismatch(
    actual: set[str], required: frozenset[str], allowed: frozenset[str]
) -> None:
    details = []
    missing = sorted(required - actual)
    unknown = sorted(actual - allowed)
    if missing:
        details.append(f"missing fields: {', '.join(missing)}")
    if unknown:
        details.append(f"unknown fields: {', '.join(unknown)}")
    raise ResponseCodecError(f"response payload fields do not match v1 ({'; '.join(details)})")


def _require_exact_integer(value: object, expected: int, name: str) -> None:
    if type(value) is not int or value != expected:
        raise ResponseCodecError(f"{name} must be {expected}")


def _require_non_empty_string(value: object, name: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_scan_id(value: object, name: str) -> int:
    if type(value) is not int or not 1 <= value <= MAX_SIGNED_64_BIT:
        raise ValueError(f"{name} must be a positive signed 64-bit integer")
    return value
