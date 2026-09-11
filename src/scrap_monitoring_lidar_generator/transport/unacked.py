"""Bounded retention of sent or pending scan frames."""

import math
from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum

from scrap_monitoring_lidar_generator.transport.framing import (
    MAX_FRAME_BODY_BYTES,
    FrameError,
    validate_frame,
)
from scrap_monitoring_lidar_generator.transport.responses import ScanIdentity


class BufferDiscardReason(StrEnum):
    """Reason that an unacknowledged frame left the buffer."""

    EXPIRED = "expired"
    CAPACITY = "capacity"
    FRAME_TOO_LARGE = "frame_too_large"


@dataclass(frozen=True, slots=True)
class BufferedFrame:
    """One complete wire frame retained from its original enqueue time."""

    identity: ScanIdentity
    frame: bytes
    enqueued_at_s: float

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ScanIdentity):
            raise ValueError("buffered frame identity must be a ScanIdentity")
        try:
            validate_frame(self.frame, max_body_bytes=MAX_FRAME_BODY_BYTES)
        except FrameError as error:
            raise ValueError(str(error)) from error
        _validate_monotonic_time(self.enqueued_at_s, "buffered frame enqueue time")

    @property
    def byte_count(self) -> int:
        """Return frame bytes including the length prefix."""
        return len(self.frame)


@dataclass(frozen=True, slots=True)
class DiscardedFrame:
    """A frame removed without a matching acknowledgement."""

    buffered: BufferedFrame
    reason: BufferDiscardReason


@dataclass(frozen=True, slots=True)
class BufferEnqueueResult:
    """Outcome of adding one frame after applying both buffer limits."""

    accepted: bool
    discarded: tuple[DiscardedFrame, ...]


class UnackedFrameBuffer:
    """Retain complete frames in deterministic oldest-first order."""

    __slots__ = ("_entries", "_last_observed_s", "_max_age_s", "_max_bytes", "_total_bytes")

    def __init__(self, *, max_age_s: float, max_bytes: int) -> None:
        if not _is_finite_number(max_age_s) or max_age_s <= 0.0:
            raise ValueError("unacknowledged buffer max age must be finite and positive")
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("unacknowledged buffer max bytes must be a positive integer")
        self._max_age_s = float(max_age_s)
        self._max_bytes = max_bytes
        self._entries: OrderedDict[ScanIdentity, BufferedFrame] = OrderedDict()
        self._total_bytes = 0
        self._last_observed_s = 0.0

    @property
    def entries(self) -> tuple[BufferedFrame, ...]:
        """Return retained frames in original enqueue order."""
        return tuple(self._entries.values())

    @property
    def total_bytes(self) -> int:
        """Return retained wire bytes including frame prefixes."""
        return self._total_bytes

    def __len__(self) -> int:
        return len(self._entries)

    def enqueue(
        self,
        *,
        identity: ScanIdentity,
        frame: bytes,
        now_s: float,
    ) -> BufferEnqueueResult:
        """Retain one new frame and report every expired or capacity discard."""
        candidate_time_s = _validate_monotonic_time(now_s, "buffer observation time")
        buffered = BufferedFrame(
            identity=identity,
            frame=frame,
            enqueued_at_s=candidate_time_s,
        )
        if identity in self._entries:
            raise ValueError("unacknowledged buffer already contains scan identity")
        observed_s = self._observe(candidate_time_s)
        discarded = list(self._expire(observed_s))
        if buffered.byte_count > self._max_bytes:
            discarded.append(
                DiscardedFrame(buffered=buffered, reason=BufferDiscardReason.FRAME_TOO_LARGE)
            )
            return BufferEnqueueResult(accepted=False, discarded=tuple(discarded))

        self._entries[identity] = buffered
        self._total_bytes += buffered.byte_count
        while self._total_bytes > self._max_bytes:
            discarded.append(
                DiscardedFrame(
                    buffered=self._pop_oldest(),
                    reason=BufferDiscardReason.CAPACITY,
                )
            )
        return BufferEnqueueResult(accepted=True, discarded=tuple(discarded))

    def expire(self, *, now_s: float) -> tuple[DiscardedFrame, ...]:
        """Discard frames whose maximum retention age has been reached."""
        return self._expire(self._observe(now_s))

    def acknowledge(self, identity: ScanIdentity) -> BufferedFrame | None:
        """Remove only the frame matching one complete scan identity."""
        if not isinstance(identity, ScanIdentity):
            raise ValueError("acknowledged identity must be a ScanIdentity")
        buffered = self._entries.pop(identity, None)
        if buffered is not None:
            self._total_bytes -= buffered.byte_count
        return buffered

    def _observe(self, now_s: float) -> float:
        observed_s = _validate_monotonic_time(now_s, "buffer observation time")
        if observed_s < self._last_observed_s:
            raise ValueError("buffer observation time must advance monotonically")
        self._last_observed_s = observed_s
        return observed_s

    def _expire(self, now_s: float) -> tuple[DiscardedFrame, ...]:
        discarded: list[DiscardedFrame] = []
        while self._entries:
            oldest = next(iter(self._entries.values()))
            if now_s - oldest.enqueued_at_s < self._max_age_s:
                break
            discarded.append(
                DiscardedFrame(
                    buffered=self._pop_oldest(),
                    reason=BufferDiscardReason.EXPIRED,
                )
            )
        return tuple(discarded)

    def _pop_oldest(self) -> BufferedFrame:
        _, buffered = self._entries.popitem(last=False)
        self._total_bytes -= buffered.byte_count
        return buffered


def _validate_monotonic_time(value: float, name: str) -> float:
    if not _is_finite_number(value) or value < 0.0:
        raise ValueError(f"{name} must be finite and non-negative")
    return float(value)


def _is_finite_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int | float) and math.isfinite(value)
