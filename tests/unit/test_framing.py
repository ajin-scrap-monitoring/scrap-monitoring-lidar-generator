"""Tests for length-prefixed TCP framing."""

import pytest

from scrap_monitoring_lidar_generator.transport import (
    FrameDecoder,
    FrameError,
    encode_frame,
    validate_frame,
    validate_max_body_bytes,
)


def test_encoder_uses_unsigned_big_endian_body_length() -> None:
    assert encode_frame(b"abc") == b"\x00\x00\x00\x03abc"


def test_default_limit_is_one_mebibyte() -> None:
    body = b"x" * 1_048_576

    assert len(encode_frame(body)) == 1_048_580
    with pytest.raises(FrameError, match="1048576-byte limit"):
        encode_frame(body + b"x")


def test_complete_frame_validation_rejects_length_mismatch_and_limit() -> None:
    validate_frame(encode_frame(b"test", max_body_bytes=4), max_body_bytes=4)

    with pytest.raises(FrameError, match="match its body"):
        validate_frame(b"\x00\x00\x00\x03test")
    with pytest.raises(FrameError, match="4-byte limit"):
        validate_frame(b"\x00\x00\x00\x05abcde", max_body_bytes=4)


def test_decoder_accepts_every_split_position() -> None:
    frame = encode_frame(b"message")
    for split_at in range(1, len(frame)):
        decoder = FrameDecoder()

        assert decoder.feed(frame[:split_at]) == ()
        assert decoder.feed(frame[split_at:]) == (b"message",)
        assert decoder.pending_bytes == 0
        assert decoder.expected_body_bytes is None


def test_decoder_returns_concatenated_frames_in_order() -> None:
    decoder = FrameDecoder()

    assert decoder.feed(encode_frame(b"first") + encode_frame(b"second")) == (
        b"first",
        b"second",
    )


def test_decoder_retains_only_incomplete_frame_state() -> None:
    decoder = FrameDecoder()

    assert decoder.feed(b"\x00\x00\x00\x05ab") == ()
    assert decoder.expected_body_bytes == 5
    assert decoder.pending_bytes == 2
    assert decoder.feed(b"cde\x00\x00") == (b"abcde",)
    assert decoder.expected_body_bytes is None
    assert decoder.pending_bytes == 2


def test_reset_discards_partial_or_failed_connection_state() -> None:
    decoder = FrameDecoder(max_body_bytes=4)
    decoder.feed(b"\x00\x00")
    decoder.reset()

    assert decoder.feed(encode_frame(b"ok", max_body_bytes=4)) == (b"ok",)

    with pytest.raises(FrameError, match="exceeds"):
        decoder.feed(b"\x00\x00\x00\x05")
    assert decoder.failed is True
    with pytest.raises(FrameError, match="failed"):
        decoder.feed(b"data")

    decoder.reset()
    assert decoder.failed is False
    assert decoder.feed(encode_frame(b"done", max_body_bytes=4)) == (b"done",)


@pytest.mark.parametrize("prefix", [b"\x00\x00\x00\x00", b"\x00\x00\x00\x05"])
def test_decoder_rejects_zero_or_oversized_declared_body(prefix: bytes) -> None:
    decoder = FrameDecoder(max_body_bytes=4)

    with pytest.raises(FrameError):
        decoder.feed(prefix)

    assert decoder.failed is True
    assert decoder.pending_bytes == 0


@pytest.mark.parametrize("body", [b"", b"12345"])
def test_encoder_rejects_empty_or_oversized_body(body: bytes) -> None:
    with pytest.raises(FrameError):
        encode_frame(body, max_body_bytes=4)


@pytest.mark.parametrize("limit", [0, True, 4_294_967_296])
def test_rejects_invalid_maximum_body_size(limit: int) -> None:
    with pytest.raises(FrameError, match="max body bytes"):
        FrameDecoder(max_body_bytes=limit)
    with pytest.raises(FrameError, match="max body bytes"):
        encode_frame(b"a", max_body_bytes=limit)
    with pytest.raises(FrameError, match="max body bytes"):
        validate_max_body_bytes(limit)
