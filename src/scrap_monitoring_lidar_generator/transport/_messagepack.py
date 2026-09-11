"""Shared strict MessagePack document handling."""

from collections.abc import Iterable
from importlib import import_module
from typing import Protocol, cast


class _MessagePackModule(Protocol):
    def packb(self, value: object, **options: object) -> bytes:
        """Encode one object."""
        ...

    def unpackb(self, payload: bytes, **options: object) -> object:
        """Decode one complete object."""
        ...


_msgpack = cast(_MessagePackModule, import_module("msgpack"))


class MessagePackDocumentError(ValueError):
    """A payload is not one unambiguous MessagePack map."""


def pack_messagepack_document(document: dict[str, object]) -> bytes:
    """Encode one map with stable public wire types."""
    return _msgpack.packb(
        document,
        use_bin_type=True,
        use_single_float=False,
        strict_types=True,
    )


def unpack_messagepack_map(payload: bytes, payload_name: str) -> dict[str, object]:
    """Decode exactly one map while rejecting duplicate and non-string keys."""
    if not isinstance(payload, bytes) or not payload:
        raise MessagePackDocumentError(f"{payload_name} must be non-empty bytes")

    def unique_string_map(pairs: Iterable[tuple[object, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if type(key) is not str:
                raise MessagePackDocumentError(f"{payload_name} map keys must be strings")
            if key in result:
                raise MessagePackDocumentError(f"{payload_name} contains duplicate map key: {key}")
            result[key] = value
        return result

    try:
        decoded = _msgpack.unpackb(
            payload,
            raw=False,
            use_list=False,
            strict_map_key=True,
            object_pairs_hook=unique_string_map,
            max_str_len=len(payload),
            max_bin_len=len(payload),
            max_array_len=len(payload),
            max_map_len=len(payload),
            max_ext_len=len(payload),
        )
    except MessagePackDocumentError:
        raise
    except Exception as error:
        raise MessagePackDocumentError(
            f"{payload_name} is not one complete valid MessagePack object"
        ) from error

    if not isinstance(decoded, dict):
        raise MessagePackDocumentError(f"{payload_name} root must be a map")
    return cast(dict[str, object], decoded)
