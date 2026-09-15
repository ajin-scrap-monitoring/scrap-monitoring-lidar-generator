"""Run-relative timestamp conversion for local diagnostic records."""

import math

MAX_SIGNED_64_BIT = 9_223_372_036_854_775_807
_MICROSECONDS_PER_SECOND = 1_000_000


def scan_captured_at_utc_us(run_started_at_utc_us: int, captured_elapsed_s: float) -> int:
    """Return UTC microseconds for one run-relative first measurement time."""
    run_start_us = _require_non_negative_64_bit_integer(
        run_started_at_utc_us,
        "run start UTC timestamp",
    )
    captured_offset_us = _elapsed_microseconds(captured_elapsed_s)
    if captured_offset_us > MAX_SIGNED_64_BIT - run_start_us:
        raise OverflowError("scan captured_at exceeds the signed 64-bit range")
    return run_start_us + captured_offset_us


def _elapsed_microseconds(elapsed_s: float) -> int:
    if not math.isfinite(elapsed_s) or elapsed_s < 0.0:
        raise ValueError("scan captured elapsed time must be finite and non-negative")
    if elapsed_s > MAX_SIGNED_64_BIT / _MICROSECONDS_PER_SECOND:
        raise OverflowError("scan captured elapsed time exceeds the signed 64-bit range")
    return math.floor(elapsed_s * _MICROSECONDS_PER_SECOND + 0.5)


def _require_non_negative_64_bit_integer(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_SIGNED_64_BIT:
        raise ValueError(f"{name} must be a non-negative signed 64-bit integer")
    return value
