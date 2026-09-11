"""Scan serialization and delivery."""

from scrap_monitoring_lidar_generator.transport.codec import (
    ScanCodecError,
    decode_scan_message,
    encode_scan_message,
)
from scrap_monitoring_lidar_generator.transport.framing import (
    DEFAULT_MAX_MESSAGE_BODY_BYTES,
    FRAME_PREFIX_BYTES,
    MAX_FRAME_BODY_BYTES,
    FrameDecoder,
    FrameError,
    encode_frame,
)
from scrap_monitoring_lidar_generator.transport.models import (
    MAX_SIGNED_64_BIT,
    MAX_VALID_DISTANCE_M,
    MIN_VALID_DISTANCE_M,
    PROTOCOL_VERSION,
    SCAN_MESSAGE_TYPE,
    ScanMessage,
    ScanMessageFactory,
)
from scrap_monitoring_lidar_generator.transport.responses import (
    ACK_MESSAGE_TYPE,
    ERROR_MESSAGE_TYPE,
    AckMessage,
    ErrorCode,
    ErrorMessage,
    ResponseCodecError,
    ResponseMessage,
    ScanIdentity,
    decode_response_message,
    encode_response_message,
)

__all__ = [
    "ACK_MESSAGE_TYPE",
    "DEFAULT_MAX_MESSAGE_BODY_BYTES",
    "ERROR_MESSAGE_TYPE",
    "FRAME_PREFIX_BYTES",
    "MAX_FRAME_BODY_BYTES",
    "MAX_SIGNED_64_BIT",
    "MAX_VALID_DISTANCE_M",
    "MIN_VALID_DISTANCE_M",
    "PROTOCOL_VERSION",
    "SCAN_MESSAGE_TYPE",
    "AckMessage",
    "ErrorCode",
    "ErrorMessage",
    "FrameDecoder",
    "FrameError",
    "ResponseCodecError",
    "ResponseMessage",
    "ScanCodecError",
    "ScanIdentity",
    "ScanMessage",
    "ScanMessageFactory",
    "decode_response_message",
    "decode_scan_message",
    "encode_frame",
    "encode_response_message",
    "encode_scan_message",
]
