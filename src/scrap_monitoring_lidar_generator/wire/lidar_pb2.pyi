from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ScanSample(_message.Message):
    __slots__ = ("angle_mdeg", "distance_mm", "quality")
    ANGLE_MDEG_FIELD_NUMBER: _ClassVar[int]
    DISTANCE_MM_FIELD_NUMBER: _ClassVar[int]
    QUALITY_FIELD_NUMBER: _ClassVar[int]
    angle_mdeg: int
    distance_mm: int
    quality: int
    def __init__(self, angle_mdeg: _Optional[int] = ..., distance_mm: _Optional[int] = ..., quality: _Optional[int] = ...) -> None: ...

class ScanFrame(_message.Message):
    __slots__ = ("schema_version", "edge_id", "sensor_id", "sequence", "acquired_at_unix_ms", "acquired_monotonic_ns", "sdk_status", "scan_hz", "samples", "instance_id", "config_revision")
    SCHEMA_VERSION_FIELD_NUMBER: _ClassVar[int]
    EDGE_ID_FIELD_NUMBER: _ClassVar[int]
    SENSOR_ID_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    ACQUIRED_AT_UNIX_MS_FIELD_NUMBER: _ClassVar[int]
    ACQUIRED_MONOTONIC_NS_FIELD_NUMBER: _ClassVar[int]
    SDK_STATUS_FIELD_NUMBER: _ClassVar[int]
    SCAN_HZ_FIELD_NUMBER: _ClassVar[int]
    SAMPLES_FIELD_NUMBER: _ClassVar[int]
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    CONFIG_REVISION_FIELD_NUMBER: _ClassVar[int]
    schema_version: str
    edge_id: str
    sensor_id: str
    sequence: int
    acquired_at_unix_ms: int
    acquired_monotonic_ns: int
    sdk_status: str
    scan_hz: float
    samples: _containers.RepeatedCompositeFieldContainer[ScanSample]
    instance_id: str
    config_revision: str
    def __init__(self, schema_version: _Optional[str] = ..., edge_id: _Optional[str] = ..., sensor_id: _Optional[str] = ..., sequence: _Optional[int] = ..., acquired_at_unix_ms: _Optional[int] = ..., acquired_monotonic_ns: _Optional[int] = ..., sdk_status: _Optional[str] = ..., scan_hz: _Optional[float] = ..., samples: _Optional[_Iterable[_Union[ScanSample, _Mapping]]] = ..., instance_id: _Optional[str] = ..., config_revision: _Optional[str] = ...) -> None: ...

class SubscribeRequest(_message.Message):
    __slots__ = ("consumer_id",)
    CONSUMER_ID_FIELD_NUMBER: _ClassVar[int]
    consumer_id: str
    def __init__(self, consumer_id: _Optional[str] = ...) -> None: ...
