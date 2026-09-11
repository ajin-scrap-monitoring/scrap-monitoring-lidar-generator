"""Deterministic full-jitter reconnect backoff."""

import hashlib
import math
import random

_MAX_SEED = 18_446_744_073_709_551_615


class ReconnectBackoff:
    """Increase the reconnect delay cap until a normal acknowledgement."""

    __slots__ = (
        "_consecutive_failures",
        "_current_delay_cap_s",
        "_initial_delay_s",
        "_maximum_delay_s",
        "_rng",
    )

    def __init__(
        self,
        *,
        initial_delay_s: float,
        maximum_delay_s: float,
        seed: int,
    ) -> None:
        if not _is_finite_positive(initial_delay_s):
            raise ValueError("reconnect initial delay must be finite and positive")
        if not _is_finite_positive(maximum_delay_s):
            raise ValueError("reconnect maximum delay must be finite and positive")
        if initial_delay_s > maximum_delay_s:
            raise ValueError("reconnect initial delay must not exceed maximum delay")
        if type(seed) is not int or not 0 <= seed <= _MAX_SEED:
            raise ValueError("reconnect seed must be an unsigned 64-bit integer")
        self._initial_delay_s = float(initial_delay_s)
        self._maximum_delay_s = float(maximum_delay_s)
        self._current_delay_cap_s = self._initial_delay_s
        self._consecutive_failures = 0
        self._rng = random.Random(_derive_seed(seed))

    @property
    def current_delay_cap_s(self) -> float:
        """Return the cap that the next failure will use."""
        return self._current_delay_cap_s

    @property
    def consecutive_failures(self) -> int:
        """Return failures observed since the last normal acknowledgement."""
        return self._consecutive_failures

    def next_delay_after_failure(self) -> float:
        """Draw full jitter under the current cap and advance the next cap."""
        cap_s = self._current_delay_cap_s
        delay_s = self._rng.random() * cap_s
        self._consecutive_failures += 1
        self._current_delay_cap_s = min(cap_s * 2.0, self._maximum_delay_s)
        return delay_s

    def reset_after_ack(self) -> None:
        """Reset the cap only after a normal acknowledgement."""
        self._current_delay_cap_s = self._initial_delay_s
        self._consecutive_failures = 0


def _derive_seed(seed: int) -> int:
    payload = b"transport\0" + seed.to_bytes(8, byteorder="big") + b"reconnect-jitter"
    return int.from_bytes(hashlib.sha256(payload).digest()[:16], byteorder="big")


def _is_finite_positive(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(value)
        and value > 0.0
    )
