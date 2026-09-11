"""Low-overhead duration aggregation for generation performance stages."""

from dataclasses import dataclass
from enum import StrEnum


class PerformanceStage(StrEnum):
    """Non-overlapping stages measured by the generation benchmark."""

    SCENE_UPDATE = "scene_update"
    SCAN_GENERATION = "scan_generation"
    SERIALIZATION = "serialization"
    TRANSPORT_WAIT = "transport_wait"


@dataclass(frozen=True, slots=True)
class DurationSummary:
    """Aggregated nanosecond durations for one stage."""

    samples: int
    total_ns: int
    maximum_ns: int

    @property
    def mean_ns(self) -> float:
        """Return the arithmetic mean duration or zero without samples."""
        return self.total_ns / self.samples if self.samples else 0.0


class PerformanceRecorder:
    """Aggregate duration samples without retaining individual observations."""

    def __init__(self) -> None:
        self._samples = dict.fromkeys(PerformanceStage, 0)
        self._totals_ns = dict.fromkeys(PerformanceStage, 0)
        self._maximums_ns = dict.fromkeys(PerformanceStage, 0)

    def record(self, stage: PerformanceStage, duration_ns: int) -> None:
        """Record one non-negative duration for a known stage."""
        if not isinstance(stage, PerformanceStage):
            raise TypeError("performance stage must be a PerformanceStage")
        if type(duration_ns) is not int or duration_ns < 0:
            raise ValueError("performance duration must be a non-negative integer")
        self._samples[stage] += 1
        self._totals_ns[stage] += duration_ns
        self._maximums_ns[stage] = max(self._maximums_ns[stage], duration_ns)

    def summary(self, stage: PerformanceStage) -> DurationSummary:
        """Return an immutable aggregate for one stage."""
        if not isinstance(stage, PerformanceStage):
            raise TypeError("performance stage must be a PerformanceStage")
        return DurationSummary(
            samples=self._samples[stage],
            total_ns=self._totals_ns[stage],
            maximum_ns=self._maximums_ns[stage],
        )
