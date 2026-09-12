"""Deterministic simulation-time to video-frame selection."""

import bisect
import math
from dataclasses import dataclass
from itertools import pairwise

from scrap_monitoring_lidar_generator.observation import ObservationRecord

DEFAULT_MAX_FRAMES = 3_000
MAX_FPS = 60
MIN_FPS = 1
_MIN_VIDEO_DURATION_S = 0.1


@dataclass(frozen=True, slots=True)
class RenderTimeline:
    """Selected observation records and playback timing for a video."""

    records: tuple[ObservationRecord, ...]
    fps: int
    duration_s: float
    start_time_s: float
    end_time_s: float

    @property
    def frame_count(self) -> int:
        """Return the number of frames to render."""
        return len(self.records)


def select_render_timeline(
    records: tuple[ObservationRecord, ...],
    *,
    fps: int,
    start_time_s: float | None = None,
    end_time_s: float | None = None,
    duration_s: float | None = None,
    time_scale: float | None = None,
    max_frames: int = DEFAULT_MAX_FRAMES,
) -> RenderTimeline:
    """Select deterministic nearest-at-or-before records for output frames."""
    if not records:
        raise ValueError("at least one observation record is required")
    if isinstance(fps, bool) or not MIN_FPS <= fps <= MAX_FPS:
        raise ValueError(f"fps must be between {MIN_FPS} and {MAX_FPS}")
    if isinstance(max_frames, bool) or not 1 <= max_frames <= DEFAULT_MAX_FRAMES:
        raise ValueError(f"max_frames must be between 1 and {DEFAULT_MAX_FRAMES}")
    if duration_s is not None and time_scale is not None:
        raise ValueError("duration and time_scale are mutually exclusive")

    times = tuple(record.snapshot.state.elapsed_s for record in records)
    if any(not math.isfinite(time_s) for time_s in times):
        raise ValueError("observation times must be finite")
    if any(later < earlier for earlier, later in pairwise(times)):
        raise ValueError("observation times must be monotonic")
    start = times[0] if start_time_s is None else _finite_time(start_time_s, "start_time_s")
    end = times[-1] if end_time_s is None else _finite_time(end_time_s, "end_time_s")
    if start < times[0] or end > times[-1] or end < start:
        raise ValueError("render interval must lie within observation times")

    simulation_span_s = end - start
    if duration_s is not None:
        duration = _positive_finite(duration_s, "duration_s")
    elif time_scale is not None:
        scale = _positive_finite(time_scale, "time_scale")
        duration = max(_MIN_VIDEO_DURATION_S, simulation_span_s / scale)
    else:
        duration = max(_MIN_VIDEO_DURATION_S, simulation_span_s)
    frame_count = max(1, math.ceil(duration * fps))
    if frame_count > max_frames:
        raise ValueError(
            f"render output requires {frame_count} frames, exceeding max_frames {max_frames}"
        )

    selected: list[ObservationRecord] = []
    for frame_index in range(frame_count):
        fraction = 0.0 if frame_count == 1 else frame_index / (frame_count - 1)
        target_time_s = start + simulation_span_s * fraction
        index = bisect.bisect_right(times, target_time_s) - 1
        index = max(0, min(index, len(records) - 1))
        selected.append(records[index])
    return RenderTimeline(
        records=tuple(selected),
        fps=fps,
        duration_s=duration,
        start_time_s=start,
        end_time_s=end,
    )


def _finite_time(value: float, name: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _positive_finite(value: float, name: str) -> float:
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{name} must be finite and positive")
    return value
