"""Tests for bounded unacknowledged frame retention."""

import pytest

from scrap_monitoring_lidar_generator.transport import (
    BufferDiscardReason,
    FrameError,
    ScanIdentity,
    UnackedFrameBuffer,
    encode_frame,
)


def _identity(scan_id: int, sensor_id: str = "sensor-a") -> ScanIdentity:
    return ScanIdentity(run_id="run-a", sensor_id=sensor_id, scan_id=scan_id)


def _frame(body: bytes) -> bytes:
    return encode_frame(body, max_body_bytes=100)


def test_buffer_retains_complete_frames_in_enqueue_order() -> None:
    buffer = UnackedFrameBuffer(max_age_s=5.0, max_bytes=100)

    first = buffer.enqueue(identity=_identity(1), frame=_frame(b"one"), now_s=10.0)
    second = buffer.enqueue(
        identity=_identity(1, "sensor-b"),
        frame=_frame(b"two"),
        now_s=10.0,
    )

    assert first.accepted is True
    assert first.discarded == ()
    assert second.accepted is True
    assert [entry.identity for entry in buffer.entries] == [
        _identity(1),
        _identity(1, "sensor-b"),
    ]
    assert [entry.enqueued_at_s for entry in buffer.entries] == [10.0, 10.0]
    assert buffer.total_bytes == len(_frame(b"one")) + len(_frame(b"two"))


def test_expiry_discards_oldest_frames_at_age_limit() -> None:
    buffer = UnackedFrameBuffer(max_age_s=5.0, max_bytes=100)
    buffer.enqueue(identity=_identity(1), frame=_frame(b"one"), now_s=1.0)
    buffer.enqueue(identity=_identity(2), frame=_frame(b"two"), now_s=3.0)

    assert buffer.expire(now_s=5.999) == ()
    discarded = buffer.expire(now_s=6.0)

    assert [(item.buffered.identity, item.reason) for item in discarded] == [
        (_identity(1), BufferDiscardReason.EXPIRED)
    ]
    assert [entry.identity for entry in buffer.entries] == [_identity(2)]


def test_enqueue_expires_old_frames_before_capacity_check() -> None:
    frame = _frame(b"1234")
    buffer = UnackedFrameBuffer(max_age_s=2.0, max_bytes=len(frame))
    buffer.enqueue(identity=_identity(1), frame=frame, now_s=1.0)

    result = buffer.enqueue(identity=_identity(2), frame=frame, now_s=3.0)

    assert result.accepted is True
    assert [(item.buffered.identity, item.reason) for item in result.discarded] == [
        (_identity(1), BufferDiscardReason.EXPIRED)
    ]
    assert [entry.identity for entry in buffer.entries] == [_identity(2)]


def test_capacity_discards_oldest_frames_and_counts_prefixes() -> None:
    frame = _frame(b"1234")
    buffer = UnackedFrameBuffer(max_age_s=5.0, max_bytes=len(frame) * 2)
    buffer.enqueue(identity=_identity(1), frame=frame, now_s=0.0)
    buffer.enqueue(identity=_identity(2), frame=frame, now_s=0.1)

    result = buffer.enqueue(identity=_identity(3), frame=frame, now_s=0.2)

    assert [(item.buffered.identity, item.reason) for item in result.discarded] == [
        (_identity(1), BufferDiscardReason.CAPACITY)
    ]
    assert [entry.identity for entry in buffer.entries] == [_identity(2), _identity(3)]
    assert buffer.total_bytes == len(frame) * 2


def test_single_oversized_frame_is_rejected_without_evicting_retained_frames() -> None:
    buffer = UnackedFrameBuffer(max_age_s=5.0, max_bytes=8)
    retained = _frame(b"1234")
    oversized = _frame(b"12345")
    buffer.enqueue(identity=_identity(1), frame=retained, now_s=0.0)

    result = buffer.enqueue(identity=_identity(2), frame=oversized, now_s=0.1)

    assert result.accepted is False
    assert [(item.buffered.identity, item.reason) for item in result.discarded] == [
        (_identity(2), BufferDiscardReason.FRAME_TOO_LARGE)
    ]
    assert [entry.identity for entry in buffer.entries] == [_identity(1)]
    assert buffer.total_bytes == len(retained)


def test_acknowledgement_removes_only_the_exact_identity() -> None:
    buffer = UnackedFrameBuffer(max_age_s=5.0, max_bytes=100)
    frame = _frame(b"one")
    buffer.enqueue(identity=_identity(1), frame=frame, now_s=0.0)
    buffer.enqueue(identity=_identity(2), frame=frame, now_s=0.1)

    assert buffer.acknowledge(_identity(99)) is None
    acknowledged = buffer.acknowledge(_identity(2))

    assert acknowledged is not None
    assert acknowledged.identity == _identity(2)
    assert [entry.identity for entry in buffer.entries] == [_identity(1)]
    assert buffer.total_bytes == len(frame)


def test_retransmission_reads_original_entry_without_changing_enqueue_time() -> None:
    buffer = UnackedFrameBuffer(max_age_s=5.0, max_bytes=100)
    frame = _frame(b"one")
    buffer.enqueue(identity=_identity(1), frame=frame, now_s=2.0)

    first_attempt = buffer.entries[0]
    retry_attempt = buffer.entries[0]

    assert retry_attempt is first_attempt
    assert retry_attempt.enqueued_at_s == 2.0


def test_buffer_rejects_duplicate_identity_and_backward_time() -> None:
    buffer = UnackedFrameBuffer(max_age_s=5.0, max_bytes=100)
    frame = _frame(b"one")
    buffer.enqueue(identity=_identity(1), frame=frame, now_s=2.0)

    with pytest.raises(ValueError, match="already contains"):
        buffer.enqueue(identity=_identity(1), frame=frame, now_s=2.0)
    with pytest.raises(ValueError, match="monotonically"):
        buffer.expire(now_s=1.9)


@pytest.mark.parametrize(
    "frame",
    [
        b"",
        b"\x00\x00\x00\x00",
        b"\x00\x00\x00\x02a",
        b"\x00\x00\x00\x01ab",
    ],
)
def test_buffer_rejects_incomplete_or_mismatched_frame(frame: bytes) -> None:
    buffer = UnackedFrameBuffer(max_age_s=5.0, max_bytes=100)
    retained_frame = _frame(b"retained")
    buffer.enqueue(identity=_identity(2), frame=retained_frame, now_s=0.0)

    with pytest.raises(ValueError, match="frame"):
        buffer.enqueue(identity=_identity(1), frame=frame, now_s=6.0)

    assert buffer.expire(now_s=4.0) == ()
    assert [entry.identity for entry in buffer.entries] == [_identity(2)]


@pytest.mark.parametrize(
    ("max_age_s", "max_bytes"),
    [(0.0, 1), (float("inf"), 1), (True, 1), (1.0, 0), (1.0, True)],
)
def test_buffer_rejects_invalid_limits(max_age_s: float, max_bytes: int) -> None:
    with pytest.raises(ValueError):
        UnackedFrameBuffer(max_age_s=max_age_s, max_bytes=max_bytes)


def test_buffer_rejects_invalid_observation_time() -> None:
    buffer = UnackedFrameBuffer(max_age_s=5.0, max_bytes=100)

    with pytest.raises(ValueError, match="finite and non-negative"):
        buffer.expire(now_s=float("nan"))


def test_frame_helper_rejects_body_above_test_limit() -> None:
    with pytest.raises(FrameError):
        encode_frame(b"x" * 101, max_body_bytes=100)
