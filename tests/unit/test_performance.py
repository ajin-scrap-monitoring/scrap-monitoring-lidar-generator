"""Tests for bounded performance duration aggregation."""

import pytest

from scrap_monitoring_lidar_generator.runtime import PerformanceRecorder, PerformanceStage


def test_recorder_aggregates_each_stage_without_retaining_samples() -> None:
    recorder = PerformanceRecorder()

    recorder.record(PerformanceStage.SCENE_UPDATE, 10)
    recorder.record(PerformanceStage.SCENE_UPDATE, 30)

    summary = recorder.summary(PerformanceStage.SCENE_UPDATE)
    assert summary.samples == 2
    assert summary.total_ns == 40
    assert summary.maximum_ns == 30
    assert summary.mean_ns == 20.0
    assert recorder.summary(PerformanceStage.SERIALIZATION).samples == 0


@pytest.mark.parametrize("duration_ns", [-1, 1.0, True])
def test_recorder_rejects_invalid_durations(duration_ns: object) -> None:
    recorder = PerformanceRecorder()

    with pytest.raises(ValueError, match="non-negative integer"):
        recorder.record(PerformanceStage.SCAN_GENERATION, duration_ns)  # type: ignore[arg-type]
