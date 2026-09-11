"""Smooth rate variation with an exact cycle-average factor."""

import math
import random
from bisect import bisect_right
from dataclasses import dataclass, field

type FloatRange = tuple[float, float]

_PROFILE_TOLERANCE = 1e-12


@dataclass(frozen=True, slots=True)
class SmoothRateSegment:
    """One smooth rate deviation that starts and ends at the cycle average."""

    start_s: float
    duration_s: float
    deviation: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.start_s) or self.start_s < 0.0:
            raise ValueError("rate segment start must be a finite non-negative number")
        if not math.isfinite(self.duration_s) or self.duration_s <= 0.0:
            raise ValueError("rate segment duration must be a finite positive number")
        if not math.isfinite(self.deviation) or self.deviation <= -1.0:
            raise ValueError("rate segment deviation must be finite and greater than -1")

    @property
    def end_s(self) -> float:
        """Return the segment end offset."""
        return self.start_s + self.duration_s

    def factor_at(self, elapsed_s: float) -> float:
        """Return the instantaneous factor within this segment."""
        if elapsed_s <= self.start_s or elapsed_s >= self.end_s:
            return 1.0
        normalized = (elapsed_s - self.start_s) / self.duration_s
        return 1.0 + self.deviation * math.sin(math.pi * normalized) ** 2

    def integrated_deviation_to(self, elapsed_s: float) -> float:
        """Integrate only the signed deviation from the segment start."""
        normalized = min(1.0, max(0.0, (elapsed_s - self.start_s) / self.duration_s))
        normalized_integral = normalized / 2.0 - math.sin(2.0 * math.pi * normalized) / (
            4.0 * math.pi
        )
        return self.deviation * self.duration_s * normalized_integral


@dataclass(frozen=True, slots=True)
class SmoothRateProfile:
    """Positive smooth factor profile whose cycle average is exactly one."""

    duration_s: float
    segments: tuple[SmoothRateSegment, ...]
    _segment_starts_s: tuple[float, ...] = field(init=False, repr=False)
    _segment_ends_s: tuple[float, ...] = field(init=False, repr=False)
    _prefix_deviation_integrals_s: tuple[float, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not math.isfinite(self.duration_s) or self.duration_s <= 0.0:
            raise ValueError("rate profile duration must be a finite positive number")

        tolerance_s = max(1.0, self.duration_s) * _PROFILE_TOLERANCE
        previous_end_s = 0.0
        prefix_integrals_s = [0.0]
        for segment in self.segments:
            if segment.start_s < previous_end_s - tolerance_s:
                raise ValueError("rate profile segments must not overlap")
            if segment.end_s > self.duration_s + tolerance_s:
                raise ValueError("rate profile segment must end within the profile duration")
            previous_end_s = segment.end_s
            prefix_integrals_s.append(
                prefix_integrals_s[-1] + segment.deviation * segment.duration_s / 2.0
            )

        if not math.isclose(
            prefix_integrals_s[-1],
            0.0,
            rel_tol=0.0,
            abs_tol=tolerance_s,
        ):
            raise ValueError("rate profile deviations must have a zero integral")

        object.__setattr__(
            self,
            "_segment_starts_s",
            tuple(segment.start_s for segment in self.segments),
        )
        object.__setattr__(
            self,
            "_segment_ends_s",
            tuple(segment.end_s for segment in self.segments),
        )
        object.__setattr__(
            self,
            "_prefix_deviation_integrals_s",
            tuple(prefix_integrals_s),
        )

    def factor_at(self, elapsed_s: float) -> float:
        """Return the rate factor at a profile-relative elapsed time."""
        elapsed = self._require_elapsed(elapsed_s)
        if elapsed == self.duration_s or not self.segments:
            return 1.0

        segment_index = bisect_right(self._segment_starts_s, elapsed) - 1
        if segment_index < 0:
            return 1.0
        return self.segments[segment_index].factor_at(elapsed)

    def integrated_factor_between(self, start_s: float, end_s: float) -> float:
        """Return the exact factor integral over a profile-relative interval."""
        start = self._require_elapsed(start_s)
        end = self._require_elapsed(end_s)
        if end < start:
            raise ValueError("rate profile interval end must be at least its start")
        if start == end:
            return 0.0
        return self._integrated_factor_to(end) - self._integrated_factor_to(start)

    def _integrated_factor_to(self, elapsed_s: float) -> float:
        if elapsed_s == self.duration_s:
            return self.duration_s

        completed_count = bisect_right(self._segment_ends_s, elapsed_s)
        deviation_integral_s = self._prefix_deviation_integrals_s[completed_count]
        if completed_count < len(self.segments):
            segment = self.segments[completed_count]
            if elapsed_s > segment.start_s:
                deviation_integral_s += segment.integrated_deviation_to(elapsed_s)
        return elapsed_s + deviation_integral_s

    def _require_elapsed(self, elapsed_s: float) -> float:
        if not math.isfinite(elapsed_s) or elapsed_s < 0.0 or elapsed_s > self.duration_s:
            raise ValueError("rate profile elapsed time must be within its duration")
        return elapsed_s


def create_smooth_rate_profile(
    *,
    duration_s: float,
    factor_range: FloatRange,
    change_duration_s_range: FloatRange,
    rng: random.Random,
) -> SmoothRateProfile:
    """Create balanced random lobes that preserve the exact average rate."""
    if not math.isfinite(duration_s) or duration_s <= 0.0:
        raise ValueError("rate profile duration must be a finite positive number")
    lower_factor, upper_factor = _require_range(factor_range, "rate factor")
    if not lower_factor <= 1.0 <= upper_factor:
        raise ValueError("rate factor range must include 1")
    minimum_change_s, maximum_change_s = _require_range(
        change_duration_s_range,
        "rate change duration",
    )
    if minimum_change_s <= 0.0:
        raise ValueError("rate change duration range must be positive")

    positive_limit = upper_factor - 1.0
    negative_limit = 1.0 - lower_factor
    if positive_limit == 0.0 or negative_limit == 0.0:
        return SmoothRateProfile(duration_s=duration_s, segments=())

    cursor_s = 0.0
    segments: list[SmoothRateSegment] = []
    while duration_s - cursor_s >= 2.0 * minimum_change_s:
        remaining_s = duration_s - cursor_s
        first_duration_s = rng.uniform(
            minimum_change_s,
            min(maximum_change_s, remaining_s - minimum_change_s),
        )
        second_duration_s = rng.uniform(
            minimum_change_s,
            min(maximum_change_s, remaining_s - first_duration_s),
        )
        positive_first = bool(rng.getrandbits(1))
        positive_duration_s = first_duration_s if positive_first else second_duration_s
        negative_duration_s = second_duration_s if positive_first else first_duration_s
        maximum_balance_s = min(
            positive_limit * positive_duration_s,
            negative_limit * negative_duration_s,
        )
        balance_s = rng.uniform(0.0, maximum_balance_s)
        positive_deviation = balance_s / positive_duration_s
        negative_deviation = -balance_s / negative_duration_s
        first_deviation = positive_deviation if positive_first else negative_deviation
        second_deviation = negative_deviation if positive_first else positive_deviation
        segments.extend(
            (
                SmoothRateSegment(cursor_s, first_duration_s, first_deviation),
                SmoothRateSegment(
                    cursor_s + first_duration_s,
                    second_duration_s,
                    second_deviation,
                ),
            )
        )
        cursor_s += first_duration_s + second_duration_s

    return SmoothRateProfile(duration_s=duration_s, segments=tuple(segments))


def _require_range(value: FloatRange, name: str) -> FloatRange:
    lower, upper = value
    if not math.isfinite(lower) or not math.isfinite(upper) or upper < lower:
        raise ValueError(f"{name} range must contain finite ordered values")
    return lower, upper
