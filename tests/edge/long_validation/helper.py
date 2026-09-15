"""Bounded in-memory collectors for Raspberry Pi long validation."""

import argparse
import asyncio
import hashlib
import importlib
import json
import math
import os
import statistics
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import grpc

from .evaluator import (
    EXPECTED_LATENCY_SAMPLES,
    EXPECTED_MEASUREMENT_DURATION_S,
    EXPECTED_OBSERVATION_RECORDS,
    EXPECTED_RESOURCE_SAMPLES,
    EXPECTED_SENSOR_IDS,
    EXPECTED_STATUS_SAMPLES,
    EXPECTED_WARMUP_DURATION_S,
    MAX_SCENARIO_TRANSITIONS,
    RUN_SCHEMA_VERSION,
    ResultError,
    evaluate_run,
    nearest_rank,
)

_MAX_PROCESSING_MEASUREMENTS = 7_200
_MAX_MEASUREMENT_BYTES = 65_536
_STATUS_MAX_AGE_S = 30.0
_MAX_RESOURCE_SAMPLE_LATENESS_NS = 250_000_000
_MAX_FUTURE_MEASUREMENT_SKEW_MS = 1_000.0
_MIN_SEMANTIC_TREND_SAMPLES = 8
_SEMANTIC_TREND_WINDOW_SAMPLES = 3
_SEMANTIC_TREND_EDGE_MARGIN_NS = 1_000_000_000
_MIN_SEMANTIC_TREND_DELTA = 0.0001


class CollectionError(ValueError):
    """Raised when a local measurement cannot be safely aggregated."""


class _DuplicateConflict(CollectionError):
    """Raised when one measurement ID is reused with different content."""


def _mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise CollectionError(f"{path} must be an object")
    return cast(Mapping[str, object], value)


def _array(value: object, path: str) -> list[object]:
    if not isinstance(value, list):
        raise CollectionError(f"{path} must be an array")
    return value


def _integer(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CollectionError(f"{path} must be an integer")
    return value


def _nonnegative_integer(value: object, path: str) -> int:
    result = _integer(value, path)
    if result < 0:
        raise CollectionError(f"{path} must be non-negative")
    return result


def _finite_nonnegative(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CollectionError(f"{path} must be a number")
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        raise CollectionError(f"{path} must be finite and non-negative")
    return result


def _ratio(value: object, path: str) -> float:
    result = _finite_nonnegative(value, path)
    if result > 1.0:
        raise CollectionError(f"{path} must be at most one")
    return result


def _optional_ratio(value: object, path: str) -> float | None:
    return None if value is None else _ratio(value, path)


def _optional_finite(value: object, path: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CollectionError(f"{path} must be a number when present")
    result = float(value)
    if not math.isfinite(result):
        raise CollectionError(f"{path} must be finite when present")
    return result


def _string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise CollectionError(f"{path} must be a non-empty string")
    return value


def _require_keys(document: Mapping[str, object], expected: set[str], path: str) -> None:
    if set(document) != expected:
        raise CollectionError(f"{path} fields differ from its contract")


def _scenario_state(value: object, path: str) -> tuple[int, str]:
    state = _mapping(value, path)
    _require_keys(state, {"cycle_index", "phase"}, path)
    cycle_index = _nonnegative_integer(state.get("cycle_index"), f"{path}.cycle_index")
    phase = _string(state.get("phase"), f"{path}.phase")
    if phase not in {"filling", "collecting"}:
        raise CollectionError(f"{path}.phase must be filling or collecting")
    return cycle_index, phase


def _batch_completion(value: object, path: str) -> tuple[int, int]:
    sample = _mapping(value, path)
    _require_keys(
        sample,
        {"deadline_monotonic_ns", "published_monotonic_ns", "latency_ns"},
        path,
    )
    deadline_ns = _nonnegative_integer(
        sample.get("deadline_monotonic_ns"), f"{path}.deadline_monotonic_ns"
    )
    published_ns = _nonnegative_integer(
        sample.get("published_monotonic_ns"), f"{path}.published_monotonic_ns"
    )
    latency_ns = _nonnegative_integer(sample.get("latency_ns"), f"{path}.latency_ns")
    if published_ns != deadline_ns + latency_ns:
        raise CollectionError(f"{path} latency is inconsistent")
    return deadline_ns, latency_ns


def _scenario_window(scenario: Mapping[str, object]) -> float:
    started_s = _finite_nonnegative(
        scenario.get("window_started_elapsed_s"), "scenario.window_started_elapsed_s"
    )
    ended_s = _finite_nonnegative(
        scenario.get("window_ended_elapsed_s"), "scenario.window_ended_elapsed_s"
    )
    observed_s = _finite_nonnegative(
        scenario.get("observed_through_elapsed_s"),
        "scenario.observed_through_elapsed_s",
    )
    expected_s = EXPECTED_WARMUP_DURATION_S + EXPECTED_MEASUREMENT_DURATION_S
    if started_s != 0.0 or ended_s != expected_s or observed_s != ended_s:
        raise CollectionError("runtime scenario telemetry window is incomplete")
    return ended_s


def _scenario_transition_records(scenario: Mapping[str, object]) -> list[object]:
    if scenario.get("overflowed") is not False:
        raise CollectionError("runtime scenario transition buffer overflowed")
    capacity = _nonnegative_integer(
        scenario.get("transition_capacity"), "scenario.transition_capacity"
    )
    if capacity != MAX_SCENARIO_TRANSITIONS:
        raise CollectionError("runtime scenario transition capacity differs from contract")
    transitions = _array(scenario.get("transitions"), "scenario.transitions")
    declared = _nonnegative_integer(scenario.get("transition_count"), "scenario.transition_count")
    if declared != len(transitions) or declared > capacity:
        raise CollectionError("runtime scenario transition count is inconsistent")
    return transitions


def _scenario_state_change(from_state: tuple[int, str], to_state: tuple[int, str]) -> str:
    if from_state[1] == "filling" and to_state == (from_state[0], "collecting"):
        return "filling_to_collecting"
    if from_state[1] == "collecting" and to_state == (from_state[0] + 1, "filling"):
        return "collecting_to_filling"
    raise CollectionError("runtime scenario phase and cycle transition is invalid")


def _scenario_transition_latencies(
    transition: Mapping[str, object],
    path: str,
    *,
    validation_started_ns: int,
    measurement_ended_ns: int,
    transitioned_at_ns: int,
) -> tuple[int, int]:
    before_deadline_ns, before_latency_ns = _batch_completion(
        transition.get("before_batch"), f"{path}.before_batch"
    )
    after_deadline_ns, after_latency_ns = _batch_completion(
        transition.get("after_batch"), f"{path}.after_batch"
    )
    if not (
        validation_started_ns
        < before_deadline_ns
        < transitioned_at_ns
        <= after_deadline_ns
        <= measurement_ended_ns
    ):
        raise CollectionError("runtime scenario transition is not inside its latency bracket")
    if after_deadline_ns - before_deadline_ns != 100_000_000:
        raise CollectionError("runtime scenario transition latency samples are not adjacent scans")
    return before_latency_ns, after_latency_ns


@dataclass(frozen=True, slots=True)
class _ProcessingMeasurementValues:
    states: Mapping[str, str]
    sequences: Mapping[str, int]
    measured_at: datetime
    fill_ratio: float | None
    sensor_fill_ratios: Mapping[str, float | None]
    sensor_median_heights_mm: Mapping[str, float | None]
    sensor_p90_heights_mm: Mapping[str, float | None]
    sensor_valid_sample_ratios: Mapping[str, float]
    sensor_coverage_ratios: Mapping[str, float]


def _processing_measurement_values(
    measurement: Mapping[str, object],
) -> _ProcessingMeasurementValues:
    sensors = _array(measurement.get("sensors"), "measurement.sensors")
    states: dict[str, str] = {}
    sequences: dict[str, int] = {}
    sensor_fill_ratios: dict[str, float | None] = {}
    sensor_median_heights_mm: dict[str, float | None] = {}
    sensor_p90_heights_mm: dict[str, float | None] = {}
    sensor_valid_sample_ratios: dict[str, float] = {}
    sensor_coverage_ratios: dict[str, float] = {}
    ordered_sensor_ids: list[str] = []
    for index, raw_sensor in enumerate(sensors):
        sensor = _mapping(raw_sensor, f"measurement.sensors[{index}]")
        sensor_id = _string(sensor.get("sensor_id"), f"measurement.sensors[{index}].sensor_id")
        if sensor_id in states:
            raise CollectionError(f"duplicate processing sensor state: {sensor_id}")
        ordered_sensor_ids.append(sensor_id)
        states[sensor_id] = _string(sensor.get("state"), f"measurement.sensors[{index}].state")
        sequences[sensor_id] = _nonnegative_integer(
            sensor.get("sequence"), f"measurement.sensors[{index}].sequence"
        )
        sensor_fill_ratios[sensor_id] = (
            None
            if sensor.get("section_fill_ratio") is None
            else _ratio(
                sensor.get("section_fill_ratio"),
                f"measurement.sensors[{index}].section_fill_ratio",
            )
        )
        sensor_median_heights_mm[sensor_id] = _optional_finite(
            sensor.get("median_height_mm"),
            f"measurement.sensors[{index}].median_height_mm",
        )
        sensor_p90_heights_mm[sensor_id] = _optional_finite(
            sensor.get("p90_height_mm"),
            f"measurement.sensors[{index}].p90_height_mm",
        )
        sensor_valid_sample_ratios[sensor_id] = _ratio(
            sensor.get("valid_sample_ratio"),
            f"measurement.sensors[{index}].valid_sample_ratio",
        )
        sensor_coverage_ratios[sensor_id] = _ratio(
            sensor.get("coverage_ratio"),
            f"measurement.sensors[{index}].coverage_ratio",
        )
    if tuple(ordered_sensor_ids) != EXPECTED_SENSOR_IDS:
        raise CollectionError("processing measurement sensors must be lidar_1 then lidar_2")
    measured_at = _string(measurement.get("measured_at"), "measurement.measured_at")
    try:
        timestamp = datetime.fromisoformat(measured_at.replace("Z", "+00:00"))
    except (OverflowError, ValueError) as error:
        raise CollectionError("processing measurement measured_at is invalid") from error
    if timestamp.tzinfo is None:
        raise CollectionError("processing measurement measured_at must include an offset")
    return _ProcessingMeasurementValues(
        states=states,
        sequences=sequences,
        measured_at=timestamp.astimezone(UTC),
        fill_ratio=_optional_ratio(measurement.get("fill_ratio"), "measurement.fill_ratio"),
        sensor_fill_ratios=sensor_fill_ratios,
        sensor_median_heights_mm=sensor_median_heights_mm,
        sensor_p90_heights_mm=sensor_p90_heights_mm,
        sensor_valid_sample_ratios=sensor_valid_sample_ratios,
        sensor_coverage_ratios=sensor_coverage_ratios,
    )


def _scenario_transition(
    raw_transition: object,
    path: str,
    *,
    current_state: tuple[int, str],
    previous_elapsed_s: float,
    window_ended_s: float,
    validation_started_ns: int,
    measurement_ended_ns: int,
) -> tuple[tuple[int, str], float, str, tuple[int, int]]:
    transition = _mapping(raw_transition, path)
    _require_keys(
        transition,
        {"transitioned_at_elapsed_s", "from", "to", "before_batch", "after_batch"},
        path,
    )
    elapsed_s = _finite_nonnegative(
        transition.get("transitioned_at_elapsed_s"), f"{path}.transitioned_at_elapsed_s"
    )
    if elapsed_s <= previous_elapsed_s or elapsed_s > window_ended_s:
        raise CollectionError("runtime scenario transition times are not ordered")
    from_state = _scenario_state(transition.get("from"), f"{path}.from")
    to_state = _scenario_state(transition.get("to"), f"{path}.to")
    if from_state != current_state:
        raise CollectionError("runtime scenario transition state is discontinuous")
    kind = _scenario_state_change(from_state, to_state)
    transitioned_at_ns = validation_started_ns + round(elapsed_s * 1_000_000_000)
    latencies = _scenario_transition_latencies(
        transition,
        path,
        validation_started_ns=validation_started_ns,
        measurement_ended_ns=measurement_ended_ns,
        transitioned_at_ns=transitioned_at_ns,
    )
    return to_state, elapsed_s, kind, latencies


def _scenario_schedule_sha256(
    *,
    window_ended_s: float,
    initial_state: tuple[int, str],
    final_state: tuple[int, str],
    transitions: Sequence[Mapping[str, object]],
) -> str:
    schedule = {
        "window_started_elapsed_s": 0.0,
        "window_ended_elapsed_s": window_ended_s,
        "initial_state": {
            "cycle_index": initial_state[0],
            "phase": initial_state[1],
        },
        "final_state": {
            "cycle_index": final_state[0],
            "phase": final_state[1],
        },
        "transitions": list(transitions),
    }
    encoded = json.dumps(
        schedule,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _phase_intervals(
    *,
    measurement_started_ns: int,
    measurement_ended_ns: int,
    validation_started_ns: int,
    transitions: Sequence[Mapping[str, object]],
) -> list[tuple[int, int, str]]:
    intervals: list[tuple[int, int, str]] = []
    phase = "filling"
    started_ns = validation_started_ns
    for index, transition in enumerate(transitions):
        elapsed_s = _finite_nonnegative(
            transition.get("transitioned_at_elapsed_s"),
            f"scenario transition {index}.transitioned_at_elapsed_s",
        )
        ended_ns = validation_started_ns + round(elapsed_s * 1_000_000_000)
        _append_phase_interval(
            intervals,
            started_ns=max(started_ns, measurement_started_ns),
            ended_ns=min(ended_ns, measurement_ended_ns),
            phase=phase,
        )
        to_state = _mapping(transition.get("to"), f"scenario transition {index}.to")
        phase = _string(to_state.get("phase"), f"scenario transition {index}.to.phase")
        started_ns = ended_ns
    _append_phase_interval(
        intervals,
        started_ns=max(started_ns, measurement_started_ns),
        ended_ns=measurement_ended_ns,
        phase=phase,
    )
    return intervals


def _append_phase_interval(
    intervals: list[tuple[int, int, str]],
    *,
    started_ns: int,
    ended_ns: int,
    phase: str,
) -> None:
    if ended_ns > started_ns:
        intervals.append((started_ns, ended_ns, phase))


@dataclass(frozen=True, slots=True)
class CgroupSnapshot:
    """One instantaneous cgroup and process memory reading."""

    monotonic_ns: int
    cpu_usage_usec: int
    rss_bytes: int
    memory_current_bytes: int | None
    nr_throttled: int
    throttled_usec: int


@dataclass(slots=True)
class _BoundedSamples:
    capacity: int
    values: list[float] = field(default_factory=list)

    def add(self, value: float) -> None:
        if not math.isfinite(value) or value < 0.0:
            raise CollectionError("sample must be finite and non-negative")
        if len(self.values) >= self.capacity:
            raise CollectionError(f"sample capacity {self.capacity} exceeded")
        self.values.append(value)

    def metric(self, *, percentile: int, expected: int) -> dict[str, object]:
        return {
            "sample_count": len(self.values),
            "missing_count": max(expected - len(self.values), 0),
            f"p{percentile}": nearest_rank(self.values, percentile) if self.values else None,
        }


def _maximum_metric(samples: _BoundedSamples, *, expected: int) -> dict[str, object]:
    return {
        "sample_count": len(samples.values),
        "missing_count": max(expected - len(samples.values), 0),
        "maximum": max(samples.values) if samples.values else None,
    }


def _cpu_percent(previous: CgroupSnapshot, current: CgroupSnapshot) -> float:
    elapsed_ns = current.monotonic_ns - previous.monotonic_ns
    cpu_delta_usec = current.cpu_usage_usec - previous.cpu_usage_usec
    if elapsed_ns <= 0 or cpu_delta_usec < 0:
        raise CollectionError("cgroup counters or monotonic clock moved backwards")
    return 100.0 * cpu_delta_usec / (elapsed_ns / 1_000.0)


@dataclass(slots=True)
class _StatusCounts:
    sample_count: int = 0
    good_count: int = 0

    def add(self, state: str) -> None:
        if self.sample_count >= _MAX_PROCESSING_MEASUREMENTS:
            raise CollectionError("processing status sample capacity exceeded")
        self.sample_count += 1
        self.good_count += state == "GOOD"


@dataclass(frozen=True, slots=True)
class _ProcessingSemanticSample:
    received_monotonic_ns: int
    fill_ratio: float


class RunCollector:
    """Retain only bounded scalar series and emit a public-safe aggregate result."""

    def __init__(
        self,
        *,
        generator_source_commit: str,
        generator_image_digest: str,
        processing_source_commit: str,
        processing_image_digest: str,
        config_fingerprint_sha256: str,
        processing_config_sha256: str,
        seed: int,
        mean_fill_duration_s: int,
        observation_mode: str,
        device: Mapping[str, object],
    ) -> None:
        if mean_fill_duration_s not in (600, 86_400):
            raise CollectionError("mean_fill_duration_s must be 600 or 86400")
        if observation_mode not in ("actual", "noop"):
            raise CollectionError("observation_mode must be actual or noop")
        self._identity = {
            "generator_source_commit": generator_source_commit,
            "generator_image_digest": generator_image_digest,
            "processing_source_commit": processing_source_commit,
            "processing_image_digest": processing_image_digest,
            "config_fingerprint_sha256": config_fingerprint_sha256,
            "processing_config_sha256": processing_config_sha256,
            "seed": seed,
        }
        self._device = dict(device)
        self._duration = mean_fill_duration_s
        self._observation_mode = observation_mode
        self._cpu = _BoundedSamples(EXPECTED_RESOURCE_SAMPLES)
        self._rss = _BoundedSamples(EXPECTED_RESOURCE_SAMPLES)
        self._processing_cpu = _BoundedSamples(EXPECTED_RESOURCE_SAMPLES)
        self._processing_rss = _BoundedSamples(EXPECTED_RESOURCE_SAMPLES)
        self._generator_memory_current = _BoundedSamples(EXPECTED_RESOURCE_SAMPLES)
        self._processing_memory_current = _BoundedSamples(EXPECTED_RESOURCE_SAMPLES)
        self._system_load = _BoundedSamples(EXPECTED_RESOURCE_SAMPLES)
        self._device_temperature = _BoundedSamples(EXPECTED_RESOURCE_SAMPLES)
        self._helper_cpu = _BoundedSamples(EXPECTED_RESOURCE_SAMPLES)
        self._helper_rss = _BoundedSamples(EXPECTED_RESOURCE_SAMPLES)
        self._sensor_latency = {
            sensor_id: _BoundedSamples(EXPECTED_LATENCY_SAMPLES)
            for sensor_id in EXPECTED_SENSOR_IDS
        }
        self._joint_latency = _BoundedSamples(EXPECTED_LATENCY_SAMPLES)
        self._statuses = {
            result_id: _StatusCounts() for result_id in (*EXPECTED_SENSOR_IDS, "fused")
        }
        self._service_statuses = {
            component: _StatusCounts() for component in ("generator", "processing")
        }
        self._measurement_window: tuple[int, int] | None = None
        self._last_measurement_ns: int | None = None
        self._maximum_measurement_gap_ns = 0
        self._processing_delivery_age_ms = _BoundedSamples(_MAX_PROCESSING_MEASUREMENTS)
        self._last_processing_sequences: dict[str, int] = {}
        self._processing_semantic_samples: list[_ProcessingSemanticSample] = []
        self._processing_semantic_counts = {
            "measurement_count": 0,
            "complete_measurement_count": 0,
            "missing_value_count": 0,
            "height_range_violation_count": 0,
            "height_order_violation_count": 0,
            "fusion_range_violation_count": 0,
            "quality_ratio_violation_count": 0,
        }
        self._scenario_phase_intervals: list[tuple[int, int, str]] | None = None
        self._simulator_semantics: dict[str, object] = {
            "sensor_scan_evidence": [],
            "evidence_complete": False,
            "passed": True,
        }
        self._events = {
            "producer_sequence_gaps": 0,
            "processing_frame_loss": 0,
            "processing_local_loss": 0,
            "processing_sequence_regressions": 0,
            "scan_server_frame_loss": 0,
            "scan_subscriber_missing_lanes": 0,
            "sequence_duplicates_or_regressions": 0,
            "unclassified_loss_windows": 0,
            "container_restarts": 0,
            "oom_events": 0,
            "thermal_throttling_events": 0,
            "evidence_complete": False,
        }
        self._observation = {
            "sent_records": 0,
            "received_records": 0,
            "dropped_records": 0,
            "connection_failures": 0,
            "evidence_complete": False,
        }
        self._scenario: dict[str, object] = {
            "initial_phase": "filling",
            "initial_cycle_index": 0,
            "final_phase": "filling",
            "final_cycle_index": 0,
            "transition_count": 0,
            "filling_to_collecting_count": 0,
            "collecting_to_filling_count": 0,
            "transition_latency_sample_count": 0,
            "maximum_transition_latency_ms": None,
            "schedule_sha256": "0" * 64,
            "evidence_complete": False,
        }
        self._runtime_telemetry_ingested = False
        self._observation_telemetry_ingested = False
        self._runtime_run_id: str | None = None
        self._throttling_first: dict[str, tuple[int, int]] = {}
        self._throttling_last: dict[str, tuple[int, int]] = {}

    def record_resource(
        self,
        *,
        generator_cpu_percent: float,
        generator_rss_bytes: int,
        generator_memory_current_bytes: int | None,
        processing_cpu_percent: float,
        processing_rss_bytes: int,
        processing_memory_current_bytes: int | None,
        system_load_1m: float,
        device_temperature_c: float,
        helper_cpu_percent: float,
        helper_rss_bytes: int,
    ) -> None:
        """Record one 1-second resource sample."""
        self._cpu.add(generator_cpu_percent)
        self._rss.add(float(generator_rss_bytes))
        if generator_memory_current_bytes is not None:
            self._generator_memory_current.add(float(generator_memory_current_bytes))
        self._processing_cpu.add(processing_cpu_percent)
        self._processing_rss.add(float(processing_rss_bytes))
        if processing_memory_current_bytes is not None:
            self._processing_memory_current.add(float(processing_memory_current_bytes))
        self._system_load.add(system_load_1m)
        self._device_temperature.add(device_temperature_c)
        self._helper_cpu.add(helper_cpu_percent)
        self._helper_rss.add(float(helper_rss_bytes))

    def record_cgroup_interval(
        self,
        generator_previous: CgroupSnapshot,
        generator_current: CgroupSnapshot,
        processing_previous: CgroupSnapshot,
        processing_current: CgroupSnapshot,
        *,
        system_load_1m: float,
        device_temperature_c: float,
        helper_cpu_percent: float,
        helper_rss_bytes: int,
    ) -> None:
        """Convert generator and processing cgroup intervals into one sample."""
        generator_cpu = _cpu_percent(generator_previous, generator_current)
        processing_cpu = _cpu_percent(processing_previous, processing_current)
        self.record_resource(
            generator_cpu_percent=generator_cpu,
            generator_rss_bytes=generator_current.rss_bytes,
            generator_memory_current_bytes=generator_current.memory_current_bytes,
            processing_cpu_percent=processing_cpu,
            processing_rss_bytes=processing_current.rss_bytes,
            processing_memory_current_bytes=processing_current.memory_current_bytes,
            system_load_1m=system_load_1m,
            device_temperature_c=device_temperature_c,
            helper_cpu_percent=helper_cpu_percent,
            helper_rss_bytes=helper_rss_bytes,
        )
        self._record_throttling("generator", generator_previous, generator_current)
        self._record_throttling("processing", processing_previous, processing_current)

    def _record_throttling(
        self,
        component: str,
        previous: CgroupSnapshot,
        current: CgroupSnapshot,
    ) -> None:
        first = self._throttling_first.setdefault(
            component, (previous.nr_throttled, previous.throttled_usec)
        )
        if current.nr_throttled < first[0] or current.throttled_usec < first[1]:
            raise CollectionError("cgroup throttling counters moved backwards")
        self._throttling_last[component] = (current.nr_throttled, current.throttled_usec)

    def set_processing_measurement_window(self, started_ns: int, ended_ns: int) -> None:
        """Set the common monotonic window used for processing freshness."""
        if started_ns < 0 or ended_ns <= started_ns or self._measurement_window is not None:
            raise CollectionError("processing measurement window is invalid or already set")
        self._measurement_window = started_ns, ended_ns

    def record_processing_measurement(
        self,
        measurement: Mapping[str, object],
        *,
        received_monotonic_ns: int,
        received_at_utc: datetime,
    ) -> None:
        """Aggregate the two sensor states and fused quality state from one measurement."""
        if self._measurement_window is None:
            raise CollectionError("processing measurement window is not configured")
        started_ns, ended_ns = self._measurement_window
        if not started_ns <= received_monotonic_ns < ended_ns:
            raise CollectionError("processing measurement is outside the configured window")
        if received_at_utc.tzinfo is None:
            raise CollectionError("processing measurement receive time must be timezone-aware")
        values = _processing_measurement_values(measurement)
        quality = _mapping(measurement.get("quality"), "measurement.quality")
        fused_state = _string(quality.get("state"), "measurement.quality.state")
        delivery_age_ms = (
            received_at_utc.astimezone(UTC) - values.measured_at
        ).total_seconds() * 1_000.0
        if delivery_age_ms < -_MAX_FUTURE_MEASUREMENT_SKEW_MS:
            raise CollectionError("processing measurement timestamp is too far in the future")
        for sensor_id in EXPECTED_SENSOR_IDS:
            self._statuses[sensor_id].add(values.states[sensor_id])
            previous_sequence = self._last_processing_sequences.get(sensor_id)
            if previous_sequence is not None and values.sequences[sensor_id] <= previous_sequence:
                self._events["processing_sequence_regressions"] += 1
            self._last_processing_sequences[sensor_id] = values.sequences[sensor_id]
        self._statuses["fused"].add(fused_state)
        self._record_processing_semantics(values, fused_state, received_monotonic_ns)
        self._processing_delivery_age_ms.add(max(0.0, delivery_age_ms))
        previous_ns = (
            self._last_measurement_ns if self._last_measurement_ns is not None else started_ns
        )
        if self._last_measurement_ns is not None and received_monotonic_ns < previous_ns:
            raise CollectionError("processing measurement receive time moved backwards")
        self._maximum_measurement_gap_ns = max(
            self._maximum_measurement_gap_ns, received_monotonic_ns - previous_ns
        )
        self._last_measurement_ns = received_monotonic_ns

    def _record_processing_semantics(
        self,
        values: _ProcessingMeasurementValues,
        fused_state: str,
        received_monotonic_ns: int,
    ) -> None:
        if len(self._processing_semantic_samples) >= _MAX_PROCESSING_MEASUREMENTS:
            raise CollectionError("processing semantic sample capacity exceeded")
        self._processing_semantic_counts["measurement_count"] += 1
        sensor_values_complete = all(
            values.sensor_fill_ratios[sensor_id] is not None
            and values.sensor_median_heights_mm[sensor_id] is not None
            and values.sensor_p90_heights_mm[sensor_id] is not None
            for sensor_id in EXPECTED_SENSOR_IDS
        )
        complete = (
            fused_state == "GOOD"
            and values.fill_ratio is not None
            and all(values.states[sensor_id] == "GOOD" for sensor_id in EXPECTED_SENSOR_IDS)
            and sensor_values_complete
        )
        self._processing_semantic_counts["complete_measurement_count"] += int(complete)
        self._processing_semantic_counts["missing_value_count"] += int(
            not sensor_values_complete or values.fill_ratio is None
        )
        self._processing_semantic_counts["quality_ratio_violation_count"] += int(
            any(
                values.sensor_valid_sample_ratios[sensor_id] <= 0.0
                or values.sensor_coverage_ratios[sensor_id] <= 0.0
                for sensor_id in EXPECTED_SENSOR_IDS
            )
        )
        for sensor_id in EXPECTED_SENSOR_IDS:
            median = values.sensor_median_heights_mm[sensor_id]
            p90 = values.sensor_p90_heights_mm[sensor_id]
            if median is None or p90 is None:
                continue
            self._processing_semantic_counts["height_range_violation_count"] += int(
                not 0.0 <= median <= 10_000.0 or not 0.0 <= p90 <= 10_000.0
            )
            self._processing_semantic_counts["height_order_violation_count"] += int(p90 < median)
        ratios = [
            values.sensor_fill_ratios[sensor_id]
            for sensor_id in EXPECTED_SENSOR_IDS
            if values.sensor_fill_ratios[sensor_id] is not None
        ]
        if len(ratios) == len(EXPECTED_SENSOR_IDS) and values.fill_ratio is not None:
            lower = min(cast(float, ratio) for ratio in ratios)
            upper = max(cast(float, ratio) for ratio in ratios)
            self._processing_semantic_counts["fusion_range_violation_count"] += int(
                not lower - 1e-12 <= values.fill_ratio <= upper + 1e-12
            )
        if values.fill_ratio is not None:
            self._processing_semantic_samples.append(
                _ProcessingSemanticSample(received_monotonic_ns, values.fill_ratio)
            )

    def record_service_status(self, *, generator_healthy: bool, processing_healthy: bool) -> None:
        """Record one deadline-based status observation for both services."""
        if any(
            status.sample_count >= EXPECTED_STATUS_SAMPLES
            for status in self._service_statuses.values()
        ):
            raise CollectionError("service status sample capacity exceeded")
        self._service_statuses["generator"].add("GOOD" if generator_healthy else "NOT_GOOD")
        self._service_statuses["processing"].add("GOOD" if processing_healthy else "NOT_GOOD")

    def ingest_runtime_telemetry(self, document: Mapping[str, object]) -> None:
        """Aggregate one Rust edge-validation telemetry artifact and discard its raw series."""
        if self._runtime_telemetry_ingested:
            raise CollectionError("runtime telemetry was already ingested")
        self._runtime_telemetry_ingested = True
        started_ns, ended_ns = self._validate_runtime_telemetry_header(document)
        self._ingest_scenario_telemetry(document, started_ns, ended_ns)
        sensor_deadlines = self._ingest_sensor_series(document, started_ns, ended_ns)
        self._ingest_batch_series(document, sensor_deadlines)
        self._ingest_scan_stream(document)
        self._ingest_scan_semantics(document)
        self._ingest_observation_telemetry(document)

    def _validate_runtime_telemetry_header(self, document: Mapping[str, object]) -> tuple[int, int]:
        self._validate_runtime_telemetry_static_fields(document)
        started_ns = _nonnegative_integer(
            document.get("measurement_started_monotonic_ns"),
            "measurement_started_monotonic_ns",
        )
        ended_ns = _nonnegative_integer(
            document.get("measurement_ended_monotonic_ns"),
            "measurement_ended_monotonic_ns",
        )
        if ended_ns - started_ns != EXPECTED_MEASUREMENT_DURATION_S * 1_000_000_000:
            raise CollectionError("runtime telemetry measurement window is invalid")
        if self._measurement_window not in (None, (started_ns, ended_ns)):
            raise CollectionError("runtime and helper measurement windows differ")
        return started_ns, ended_ns

    def _validate_runtime_telemetry_static_fields(self, document: Mapping[str, object]) -> None:
        if document.get("schema_version") != "edge-validation-telemetry.v1":
            raise CollectionError("unexpected runtime telemetry schema_version")
        self._runtime_run_id = _string(document.get("run_id"), "run_id")
        telemetry_observation_mode = "no-op" if self._observation_mode == "noop" else "actual"
        if document.get("observation_mode") != telemetry_observation_mode:
            raise CollectionError("runtime telemetry observation mode mismatch")
        durations = (
            _integer(document.get("warmup_duration_s"), "warmup_duration_s"),
            _integer(document.get("measurement_duration_s"), "measurement_duration_s"),
        )
        if durations != (EXPECTED_WARMUP_DURATION_S, EXPECTED_MEASUREMENT_DURATION_S):
            raise CollectionError("runtime telemetry duration mismatch")
        expected_sensor_ids = tuple(
            _string(item, f"expected_sensor_ids[{index}]")
            for index, item in enumerate(
                _array(document.get("expected_sensor_ids"), "expected_sensor_ids")
            )
        )
        if expected_sensor_ids != EXPECTED_SENSOR_IDS:
            raise CollectionError("runtime telemetry must contain lidar_1 and lidar_2")
        if document.get("overflowed") is not False:
            raise CollectionError("runtime telemetry sample buffer overflowed")
        expected_sample_count = _nonnegative_integer(
            document.get("expected_sample_count"), "expected_sample_count"
        )
        sample_capacity = _nonnegative_integer(document.get("sample_capacity"), "sample_capacity")
        if expected_sample_count != EXPECTED_LATENCY_SAMPLES:
            raise CollectionError("runtime telemetry expected_sample_count must be 36000")
        if sample_capacity < expected_sample_count:
            raise CollectionError("runtime telemetry sample capacity is too small")
        dropped = _nonnegative_integer(document.get("dropped_sample_count"), "dropped_sample_count")
        if dropped:
            raise CollectionError("runtime telemetry dropped samples")

    def _ingest_scenario_telemetry(
        self,
        document: Mapping[str, object],
        measurement_started_ns: int,
        measurement_ended_ns: int,
    ) -> None:
        scenario = _mapping(document.get("scenario"), "scenario")
        _require_keys(
            scenario,
            {
                "window_started_elapsed_s",
                "window_ended_elapsed_s",
                "observed_through_elapsed_s",
                "initial_state",
                "final_state",
                "transition_capacity",
                "transition_count",
                "overflowed",
                "transitions",
            },
            "scenario",
        )
        window_ended_s = _scenario_window(scenario)
        transitions = _scenario_transition_records(scenario)
        initial_state = _scenario_state(scenario.get("initial_state"), "scenario.initial_state")
        if initial_state != (0, "filling"):
            raise CollectionError("runtime scenario must start in filling cycle 0")
        current_state = initial_state
        counts = {"filling_to_collecting": 0, "collecting_to_filling": 0}
        transition_latencies_ns: list[int] = []
        schedule_transitions: list[Mapping[str, object]] = []
        previous_elapsed_s = 0.0
        validation_started_ns = measurement_started_ns - (
            EXPECTED_WARMUP_DURATION_S * 1_000_000_000
        )
        if validation_started_ns < 0:
            raise CollectionError("runtime scenario validation start precedes monotonic epoch")
        for index, raw_transition in enumerate(transitions):
            path = f"scenario.transitions[{index}]"
            previous_state = current_state
            current_state, elapsed_s, kind, latencies = _scenario_transition(
                raw_transition,
                path,
                current_state=current_state,
                previous_elapsed_s=previous_elapsed_s,
                window_ended_s=window_ended_s,
                validation_started_ns=validation_started_ns,
                measurement_ended_ns=measurement_ended_ns,
            )
            counts[kind] += 1
            transition_latencies_ns.extend(latencies)
            schedule_transitions.append(
                {
                    "transitioned_at_elapsed_s": elapsed_s,
                    "from": {
                        "cycle_index": previous_state[0],
                        "phase": previous_state[1],
                    },
                    "to": {
                        "cycle_index": current_state[0],
                        "phase": current_state[1],
                    },
                }
            )
            previous_elapsed_s = elapsed_s

        final_state = _scenario_state(scenario.get("final_state"), "scenario.final_state")
        if final_state != current_state:
            raise CollectionError("runtime scenario final state differs from its transitions")
        self._scenario = {
            "initial_phase": initial_state[1],
            "initial_cycle_index": initial_state[0],
            "final_phase": final_state[1],
            "final_cycle_index": final_state[0],
            "transition_count": len(transitions),
            "filling_to_collecting_count": counts["filling_to_collecting"],
            "collecting_to_filling_count": counts["collecting_to_filling"],
            "transition_latency_sample_count": len(transition_latencies_ns),
            "maximum_transition_latency_ms": (
                max(transition_latencies_ns) / 1_000_000.0 if transition_latencies_ns else None
            ),
            "schedule_sha256": _scenario_schedule_sha256(
                window_ended_s=window_ended_s,
                initial_state=initial_state,
                final_state=final_state,
                transitions=schedule_transitions,
            ),
            "evidence_complete": True,
        }
        self._scenario_phase_intervals = _phase_intervals(
            measurement_started_ns=measurement_started_ns,
            measurement_ended_ns=measurement_ended_ns,
            validation_started_ns=validation_started_ns,
            transitions=schedule_transitions,
        )

    def _ingest_sensor_series(
        self,
        document: Mapping[str, object],
        started_ns: int,
        ended_ns: int,
    ) -> dict[str, dict[int, int]]:
        sensor_deadlines: dict[str, dict[int, int]] = {}
        raw_sensors = _array(document.get("sensors"), "sensors")
        for index, raw_sensor in enumerate(raw_sensors):
            sensor = _mapping(raw_sensor, f"sensors[{index}]")
            sensor_id = _string(sensor.get("sensor_id"), f"sensors[{index}].sensor_id")
            if sensor_id not in self._sensor_latency or sensor_id in sensor_deadlines:
                raise CollectionError(f"unexpected or duplicate telemetry sensor: {sensor_id}")
            raw_samples = _array(sensor.get("samples"), f"sensors[{index}].samples")
            declared_count = _nonnegative_integer(
                sensor.get("sample_count"), f"sensors[{index}].sample_count"
            )
            if declared_count != len(raw_samples):
                raise CollectionError(f"{sensor_id} telemetry sample_count mismatch")
            if (
                _nonnegative_integer(
                    sensor.get("missing_sample_count"), f"sensors[{index}].missing_sample_count"
                )
                != 0
            ):
                raise CollectionError(f"{sensor_id} telemetry reports missing samples")
            sensor_deadlines[sensor_id] = self._ingest_one_sensor(
                sensor_id, raw_samples, started_ns, ended_ns
            )
            if raw_samples:
                first = _mapping(raw_samples[0], f"{sensor_id}.samples[0]")
                last = _mapping(raw_samples[-1], f"{sensor_id}.samples[-1]")
                if _nonnegative_integer(
                    sensor.get("first_sequence"), f"sensors[{index}].first_sequence"
                ) != _nonnegative_integer(first.get("sequence"), f"{sensor_id}.first_sequence"):
                    raise CollectionError(f"{sensor_id} first_sequence mismatch")
                if _nonnegative_integer(
                    sensor.get("last_sequence"), f"sensors[{index}].last_sequence"
                ) != _nonnegative_integer(last.get("sequence"), f"{sensor_id}.last_sequence"):
                    raise CollectionError(f"{sensor_id} last_sequence mismatch")
        if set(sensor_deadlines) != set(EXPECTED_SENSOR_IDS):
            raise CollectionError("runtime telemetry omitted a sensor")
        return sensor_deadlines

    def _ingest_one_sensor(
        self,
        sensor_id: str,
        raw_samples: Sequence[object],
        started_ns: int,
        ended_ns: int,
    ) -> dict[int, int]:
        deadline_latencies: dict[int, int] = {}
        previous_sequence: int | None = None
        for sample_index, raw_sample in enumerate(raw_samples):
            sample = _mapping(raw_sample, f"{sensor_id}.samples[{sample_index}]")
            sequence = _nonnegative_integer(
                sample.get("sequence"), f"{sensor_id}.samples[{sample_index}].sequence"
            )
            deadline_ns = _nonnegative_integer(
                sample.get("deadline_monotonic_ns"),
                f"{sensor_id}.samples[{sample_index}].deadline_monotonic_ns",
            )
            published_ns = _nonnegative_integer(
                sample.get("published_monotonic_ns"),
                f"{sensor_id}.samples[{sample_index}].published_monotonic_ns",
            )
            latency_ns = _nonnegative_integer(
                sample.get("latency_ns"), f"{sensor_id}.samples[{sample_index}].latency_ns"
            )
            if not started_ns < deadline_ns <= ended_ns:
                raise CollectionError(f"{sensor_id} telemetry deadline is outside measurement")
            if published_ns < deadline_ns or latency_ns != published_ns - deadline_ns:
                raise CollectionError(f"{sensor_id} telemetry latency is inconsistent")
            if deadline_ns in deadline_latencies:
                raise CollectionError(f"{sensor_id} has duplicate telemetry deadlines")
            deadline_latencies[deadline_ns] = latency_ns
            if previous_sequence is not None:
                self._record_sequence_change(previous_sequence, sequence)
            previous_sequence = sequence
            self._sensor_latency[sensor_id].add(latency_ns / 1_000_000.0)
        return deadline_latencies

    def _record_sequence_change(self, previous: int, current: int) -> None:
        if current <= previous:
            self._events["sequence_duplicates_or_regressions"] += 1
        else:
            self._events["producer_sequence_gaps"] += current - previous - 1

    def _ingest_batch_series(
        self,
        document: Mapping[str, object],
        sensor_deadlines: Mapping[str, Mapping[int, int]],
    ) -> None:
        batch_samples = _array(document.get("batch_max_samples"), "batch_max_samples")
        completed_batch_count = _nonnegative_integer(
            document.get("completed_batch_count"), "completed_batch_count"
        )
        if completed_batch_count != len(batch_samples):
            raise CollectionError("runtime telemetry completed_batch_count mismatch")
        seen_batch_deadlines: set[int] = set()
        for index, raw_batch in enumerate(batch_samples):
            batch = _mapping(raw_batch, f"batch_max_samples[{index}]")
            deadline_ns = _nonnegative_integer(
                batch.get("deadline_monotonic_ns"),
                f"batch_max_samples[{index}].deadline_monotonic_ns",
            )
            latency_ns = _nonnegative_integer(
                batch.get("latency_ns"), f"batch_max_samples[{index}].latency_ns"
            )
            published_ns = _nonnegative_integer(
                batch.get("published_monotonic_ns"),
                f"batch_max_samples[{index}].published_monotonic_ns",
            )
            if deadline_ns in seen_batch_deadlines:
                raise CollectionError("runtime telemetry has duplicate batch deadlines")
            seen_batch_deadlines.add(deadline_ns)
            try:
                expected_latency_ns = max(
                    sensor_deadlines[sensor_id][deadline_ns] for sensor_id in EXPECTED_SENSOR_IDS
                )
            except KeyError as error:
                raise CollectionError("batch deadline does not have both sensor samples") from error
            if latency_ns != expected_latency_ns:
                raise CollectionError("batch latency is not the two-sensor maximum")
            if published_ns != deadline_ns + latency_ns:
                raise CollectionError("batch published time and latency are inconsistent")
            self._joint_latency.add(latency_ns / 1_000_000.0)
        if any(set(deadlines) != seen_batch_deadlines for deadlines in sensor_deadlines.values()):
            raise CollectionError("sensor and batch telemetry deadlines differ")

    def _ingest_observation_telemetry(self, document: Mapping[str, object]) -> None:
        observation = _mapping(document.get("observation"), "observation")
        accepted = _nonnegative_integer(
            observation.get("accepted_records"), "observation.accepted_records"
        )
        sent = _nonnegative_integer(observation.get("sent_records"), "observation.sent_records")
        dropped = _nonnegative_integer(
            observation.get("dropped_records"), "observation.dropped_records"
        )
        failures = _nonnegative_integer(
            observation.get("connection_failures"), "observation.connection_failures"
        )
        if accepted != sent + dropped:
            raise CollectionError("runtime observation counters are inconsistent")
        self._observation.update(
            {
                "sent_records": sent,
                "dropped_records": dropped,
                "connection_failures": failures,
            }
        )
        self._observation_telemetry_ingested = True

    def _ingest_scan_stream(self, document: Mapping[str, object]) -> None:
        seen: set[str] = set()
        server_loss = 0
        missing_subscribers = 0
        for index, raw_lane in enumerate(_array(document.get("scan_stream"), "scan_stream")):
            lane = _mapping(raw_lane, f"scan_stream[{index}]")
            sensor_id = _string(lane.get("sensor_id"), f"scan_stream[{index}].sensor_id")
            if sensor_id not in EXPECTED_SENSOR_IDS or sensor_id in seen:
                raise CollectionError(f"unexpected or duplicate scan stream lane: {sensor_id}")
            seen.add(sensor_id)
            published_frames = _nonnegative_integer(
                lane.get("published_frames"), f"scan_stream[{index}].published_frames"
            )
            if published_frames < len(self._sensor_latency[sensor_id].values):
                raise CollectionError(
                    f"scan stream lane {sensor_id} published fewer frames than its telemetry series"
                )
            subscribers = _nonnegative_integer(
                lane.get("subscribers"), f"scan_stream[{index}].subscribers"
            )
            if subscribers > 8:
                raise CollectionError(f"scan stream lane {sensor_id} subscribers exceed capacity")
            server_loss += _nonnegative_integer(
                lane.get("frame_loss"), f"scan_stream[{index}].frame_loss"
            )
            subscriber_seen = lane.get("subscriber_seen")
            if not isinstance(subscriber_seen, bool):
                raise CollectionError(f"scan_stream[{index}].subscriber_seen must be a boolean")
            missing_subscribers += not subscriber_seen
        if seen != set(EXPECTED_SENSOR_IDS):
            raise CollectionError("runtime telemetry omitted a scan stream lane")
        self._events["scan_server_frame_loss"] = server_loss
        self._events["scan_subscriber_missing_lanes"] = missing_subscribers

    def _ingest_scan_semantics(self, document: Mapping[str, object]) -> None:
        fields = {
            "sensor_id",
            "sampled_scan_count",
            "reference_sample_count",
            "no_hit_count",
            "floor_hit_count",
            "wall_hit_count",
            "surface_hit_count",
            "measured_valid_count",
            "measured_invalid_count",
            "measured_without_reference_count",
            "reference_hit_without_measurement_count",
            "reference_change_count",
        }
        evidence: list[dict[str, object]] = []
        ordered_sensor_ids: list[str] = []
        for index, raw_sensor in enumerate(
            _array(document.get("scan_semantics"), "scan_semantics")
        ):
            path = f"scan_semantics[{index}]"
            sensor = _mapping(raw_sensor, path)
            _require_keys(sensor, fields, path)
            sensor_id = _string(sensor.get("sensor_id"), f"{path}.sensor_id")
            ordered_sensor_ids.append(sensor_id)
            evidence.append(
                {
                    "sensor_id": sensor_id,
                    **{
                        field: _nonnegative_integer(sensor.get(field), f"{path}.{field}")
                        for field in fields - {"sensor_id"}
                    },
                }
            )
        if tuple(ordered_sensor_ids) != EXPECTED_SENSOR_IDS:
            raise CollectionError("scan semantics must be ordered lidar_1 then lidar_2")
        self._simulator_semantics = {
            "sensor_scan_evidence": evidence,
            "evidence_complete": True,
            "passed": True,
        }

    def set_observation_received(self, received_records: int, run_id: str | None = None) -> None:
        """Combine receiver evidence with counters from runtime telemetry."""
        if not self._observation_telemetry_ingested:
            raise CollectionError("runtime observation telemetry is unavailable")
        if isinstance(received_records, bool) or not isinstance(received_records, int):
            raise CollectionError("received_records must be an integer")
        if received_records < 0:
            raise CollectionError("received_records must be non-negative")
        if self._observation_mode == "actual":
            if run_id is None or run_id != self._runtime_run_id:
                raise CollectionError("observation receiver run_id differs from runtime telemetry")
        elif run_id is not None:
            raise CollectionError("no-op observation evidence must not contain a run_id")
        self._observation["received_records"] = received_records
        self._observation["evidence_complete"] = True

    def set_event_counts(
        self,
        *,
        unclassified_loss_windows: int,
        container_restarts: int,
        oom_events: int,
        thermal_throttling_events: int,
        processing_frame_loss: int = 0,
        processing_local_loss: int = 0,
    ) -> None:
        """Set final lifecycle and host event counts."""
        if not self._runtime_telemetry_ingested:
            raise CollectionError("runtime event telemetry is unavailable")
        values = {
            "unclassified_loss_windows": unclassified_loss_windows,
            "container_restarts": container_restarts,
            "oom_events": oom_events,
            "thermal_throttling_events": thermal_throttling_events,
            "processing_frame_loss": processing_frame_loss,
            "processing_local_loss": processing_local_loss,
        }
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in values.values()
        ):
            raise CollectionError("event counters must be non-negative integers")
        self._events.update(values)
        self._events["evidence_complete"] = True

    def build_result(self) -> dict[str, object]:
        """Build and evaluate one aggregate document with no raw samples or endpoints."""
        document: dict[str, object] = {
            "schema_version": RUN_SCHEMA_VERSION,
            "identity": self._identity.copy(),
            "device": self._device.copy(),
            "workload": {
                "mean_fill_duration_s": self._duration,
                "observation_mode": self._observation_mode,
                "warmup_duration_s": EXPECTED_WARMUP_DURATION_S,
                "measurement_duration_s": EXPECTED_MEASUREMENT_DURATION_S,
                "sensor_ids": list(EXPECTED_SENSOR_IDS),
            },
            "metrics": {
                "generator_cpu_percent": self._cpu.metric(
                    percentile=95, expected=EXPECTED_RESOURCE_SAMPLES
                ),
                "generator_rss_bytes": self._rss.metric(
                    percentile=95, expected=EXPECTED_RESOURCE_SAMPLES
                ),
                "processing_cpu_percent": self._processing_cpu.metric(
                    percentile=95, expected=EXPECTED_RESOURCE_SAMPLES
                ),
                "processing_rss_bytes": self._processing_rss.metric(
                    percentile=95, expected=EXPECTED_RESOURCE_SAMPLES
                ),
                "generator_cgroup_memory_current_bytes": self._generator_memory_current.metric(
                    percentile=95, expected=EXPECTED_RESOURCE_SAMPLES
                ),
                "processing_cgroup_memory_current_bytes": self._processing_memory_current.metric(
                    percentile=95, expected=EXPECTED_RESOURCE_SAMPLES
                ),
                "system_load_1m": self._system_load.metric(
                    percentile=95, expected=EXPECTED_RESOURCE_SAMPLES
                ),
                "device_temperature_c": _maximum_metric(
                    self._device_temperature, expected=EXPECTED_RESOURCE_SAMPLES
                ),
                "helper_cpu_percent": self._helper_cpu.metric(
                    percentile=95, expected=EXPECTED_RESOURCE_SAMPLES
                ),
                "helper_rss_bytes": self._helper_rss.metric(
                    percentile=95, expected=EXPECTED_RESOURCE_SAMPLES
                ),
                "cgroup_throttling": [
                    self._throttling_document(component)
                    for component in ("generator", "processing")
                ],
                "joint_frame_completion_latency_ms": self._joint_latency.metric(
                    percentile=99, expected=EXPECTED_LATENCY_SAMPLES
                ),
                "sensor_frame_completion_latency_ms": [
                    {
                        "sensor_id": sensor_id,
                        **self._sensor_latency[sensor_id].metric(
                            percentile=99, expected=EXPECTED_LATENCY_SAMPLES
                        ),
                    }
                    for sensor_id in EXPECTED_SENSOR_IDS
                ],
                "processing_status": [
                    {
                        "result_id": result_id,
                        "sample_count": self._statuses[result_id].sample_count,
                        "good_count": self._statuses[result_id].good_count,
                    }
                    for result_id in (*EXPECTED_SENSOR_IDS, "fused")
                ],
                "processing_measurement_freshness_s": self._processing_freshness_document(),
                "processing_delivery_age_ms": _maximum_metric(
                    self._processing_delivery_age_ms,
                    expected=self._statuses["fused"].sample_count,
                ),
                "service_status": [
                    {
                        "component": component,
                        "sample_count": self._service_statuses[component].sample_count,
                        "healthy_count": self._service_statuses[component].good_count,
                    }
                    for component in ("generator", "processing")
                ],
            },
            "events": self._events.copy(),
            "observation": self._observation.copy(),
            "scenario": self._scenario.copy(),
            "semantics": {
                "simulator": self._simulator_semantics.copy(),
                "lidar_processing": self._processing_semantics_document(),
                "failure_domain": "none",
            },
            "evaluation": {"passed": True, "failure_codes": []},
        }
        return evaluate_run(document)

    def _processing_semantics_document(self) -> dict[str, object]:
        trend = {
            "filling_segment_count": 0,
            "filling_direction_match_count": 0,
            "collecting_segment_count": 0,
            "collecting_direction_match_count": 0,
        }
        if self._scenario_phase_intervals is not None:
            for started_ns, ended_ns, phase in self._scenario_phase_intervals:
                samples = [
                    sample.fill_ratio
                    for sample in self._processing_semantic_samples
                    if started_ns + _SEMANTIC_TREND_EDGE_MARGIN_NS
                    <= sample.received_monotonic_ns
                    < ended_ns - _SEMANTIC_TREND_EDGE_MARGIN_NS
                ]
                if len(samples) < _MIN_SEMANTIC_TREND_SAMPLES:
                    continue
                first = statistics.median(samples[:_SEMANTIC_TREND_WINDOW_SAMPLES])
                last = statistics.median(samples[-_SEMANTIC_TREND_WINDOW_SAMPLES:])
                delta = last - first
                trend[f"{phase}_segment_count"] += 1
                if (phase == "filling" and delta > _MIN_SEMANTIC_TREND_DELTA) or (
                    phase == "collecting" and delta < -_MIN_SEMANTIC_TREND_DELTA
                ):
                    trend[f"{phase}_direction_match_count"] += 1
        return {
            **self._processing_semantic_counts,
            **trend,
            "evidence_complete": (
                self._runtime_telemetry_ingested and self._scenario_phase_intervals is not None
            ),
            "passed": True,
        }

    def _processing_freshness_document(self) -> dict[str, object]:
        measurement_count = self._statuses["fused"].sample_count
        if self._measurement_window is None or self._last_measurement_ns is None:
            return {"measurement_count": measurement_count, "gap_count": 0, "maximum_gap_s": None}
        _, ended_ns = self._measurement_window
        final_gap_ns = max(ended_ns - self._last_measurement_ns, 0)
        maximum_gap_ns = max(self._maximum_measurement_gap_ns, final_gap_ns)
        return {
            "measurement_count": measurement_count,
            "gap_count": measurement_count + 1,
            "maximum_gap_s": maximum_gap_ns / 1_000_000_000.0,
        }

    def _throttling_document(self, component: str) -> dict[str, object]:
        first = self._throttling_first.get(component, (0, 0))
        last = self._throttling_last.get(component, first)
        return {
            "component": component,
            "nr_throttled_delta": last[0] - first[0],
            "throttled_usec_delta": last[1] - first[1],
        }


def read_cgroup_snapshot(
    cgroup_directory: Path,
    *,
    monotonic_ns: int,
    proc_root: Path = Path("/proc"),
) -> CgroupSnapshot:
    """Read one cgroup v2 CPU snapshot and summed process RSS."""
    cpu = _read_key_value_file(cgroup_directory / "cpu.stat")
    try:
        cpu_usage_usec = cpu["usage_usec"]
        nr_throttled = cpu["nr_throttled"]
        throttled_usec = cpu["throttled_usec"]
    except KeyError as error:
        raise CollectionError("cannot read required cgroup v2 CPU counters") from error
    try:
        memory_current: int | None = int(
            (cgroup_directory / "memory.current").read_text(encoding="ascii").strip()
        )
    except FileNotFoundError:
        memory_current = None
    except (OSError, UnicodeError, ValueError) as error:
        raise CollectionError("cannot read optional cgroup v2 memory counter") from error
    if memory_current is not None and memory_current < 0:
        raise CollectionError("cgroup v2 memory counter must be non-negative")
    process_ids = _read_recursive_cgroup_process_ids(cgroup_directory)
    if not process_ids:
        raise CollectionError("cgroup has no processes")
    rss_bytes = sum(_read_process_rss_bytes(proc_root, process_id) for process_id in process_ids)
    return CgroupSnapshot(
        monotonic_ns=monotonic_ns,
        cpu_usage_usec=cpu_usage_usec,
        rss_bytes=rss_bytes,
        memory_current_bytes=memory_current,
        nr_throttled=nr_throttled,
        throttled_usec=throttled_usec,
    )


def _read_key_value_file(path: Path) -> dict[str, int]:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
        result = {}
        for line in lines:
            name, value = line.split()
            result[name] = int(value)
    except (OSError, UnicodeError, ValueError) as error:
        raise CollectionError(f"cannot parse {path.name}") from error
    return result


def _read_recursive_cgroup_process_ids(cgroup_directory: Path) -> set[int]:
    process_ids: set[int] = set()
    try:
        process_files = [cgroup_directory / "cgroup.procs"]
        process_files.extend(
            directory / "cgroup.procs"
            for raw_directory, _, _ in os.walk(cgroup_directory, followlinks=False)
            if (directory := Path(raw_directory)) != cgroup_directory
        )
        for process_file in process_files:
            if not process_file.is_file():
                continue
            for value in process_file.read_text(encoding="ascii").split():
                process_id = int(value)
                if process_id <= 0:
                    raise ValueError("process ID must be positive")
                process_ids.add(process_id)
    except (OSError, UnicodeError, ValueError) as error:
        raise CollectionError("cannot read cgroup process IDs") from error
    return process_ids


def _read_process_rss_bytes(proc_root: Path, process_id: int) -> int:
    try:
        lines = (
            (proc_root / str(process_id) / "smaps_rollup").read_text(encoding="ascii").splitlines()
        )
        values = [line.split() for line in lines if line.startswith("Rss:")]
        if len(values) != 1 or len(values[0]) != 3 or values[0][2] != "kB":
            raise ValueError("smaps_rollup Rss field")
        resident_kib = int(values[0][1])
        if resident_kib < 0:
            raise ValueError("negative smaps_rollup Rss")
        return resident_kib * 1_024
    except (OSError, UnicodeError, ValueError) as error:
        raise CollectionError(f"cannot read RSS for process {process_id}") from error


def fingerprint_files(paths: Sequence[Path]) -> str:
    """Hash file names, lengths and contents without including private absolute paths."""
    digest = hashlib.sha256()
    names = [path.name for path in paths]
    if len(names) != len(set(names)):
        raise CollectionError("fingerprinted file names must be unique")
    for path in sorted(paths, key=lambda item: item.name):
        try:
            content = path.read_bytes()
        except OSError as error:
            raise CollectionError(f"cannot fingerprint {path.name}") from error
        name = path.name.encode("utf-8")
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def load_runtime_telemetry(path: Path) -> Mapping[str, object]:
    """Load local raw telemetry for immediate in-memory aggregation."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CollectionError(f"cannot read runtime telemetry {path.name}") from error
    return _mapping(value, "runtime telemetry")


@dataclass(frozen=True, slots=True)
class _ProcessTarget:
    host_pid: int
    cgroup_directory: Path


@dataclass(frozen=True, slots=True)
class _HelperControl:
    identity: Mapping[str, object]
    expected_measurement_identity: Mapping[str, str]
    device: Mapping[str, object]
    mean_fill_duration_s: int
    observation_mode: str
    start_at_monotonic_ns: int
    generator: _ProcessTarget
    processing: _ProcessTarget
    runtime_telemetry_path: Path
    temperature_path: Path
    observation_result_path: Path | None
    lifecycle_result_path: Path


class _MeasurementSink:
    def __init__(self, validate_measurement: Callable[[object], bytes]) -> None:
        self._validate_measurement = validate_measurement
        self.collector: RunCollector | None = None
        self.expected_identity: Mapping[str, str] | None = None
        self.measurement_started_ns = 0
        self.measurement_ended_ns = 0
        self._measurement_digests: dict[bytes, bytes] = {}
        self._recorded_measurement_hashes: set[bytes] = set()

    def configure(
        self,
        collector: RunCollector,
        *,
        measurement_started_ns: int,
        measurement_ended_ns: int,
        expected_identity: Mapping[str, str],
    ) -> None:
        if set(expected_identity) != {
            "site_id",
            "edge_id",
            "config_revision",
            "calibration_version",
        }:
            raise CollectionError("expected measurement identity fields differ")
        if any(not isinstance(value, str) or not value for value in expected_identity.values()):
            raise CollectionError("expected measurement identity values must be non-empty strings")
        self.collector = collector
        self.expected_identity = dict(expected_identity)
        self.measurement_started_ns = measurement_started_ns
        self.measurement_ended_ns = measurement_ended_ns
        collector.set_processing_measurement_window(measurement_started_ns, measurement_ended_ns)

    async def enqueue(
        self,
        request: bytes,
        context: grpc.aio.ServicerContext[bytes, bytes],
    ) -> bytes:
        try:
            payload = _decode_enqueue_request(request)
            measurement = _mapping(
                json.loads(payload, parse_constant=_reject_nonfinite_json),
                "measurement",
            )
            canonical = self._validated_canonical(measurement)
            measurement_id = _string(measurement.get("measurement_id"), "measurement_id")
            identity_hash = hashlib.sha256(measurement_id.encode("utf-8")).digest()
            content_digest = hashlib.sha256(canonical).digest()
            previous_digest = self._measurement_digests.get(identity_hash)
            duplicate = previous_digest is not None
            if previous_digest is not None and previous_digest != content_digest:
                raise _DuplicateConflict("measurement ID reused with different content")
            if not duplicate:
                if len(self._measurement_digests) >= _MAX_PROCESSING_MEASUREMENTS + 600:
                    raise CollectionError("measurement identity capacity exceeded")
                self._measurement_digests[identity_hash] = content_digest
            received_ns = time.monotonic_ns()
            received_at_utc = datetime.now(UTC)
            if (
                self.collector is not None
                and self.measurement_started_ns <= received_ns < self.measurement_ended_ns
                and identity_hash not in self._recorded_measurement_hashes
            ):
                self.collector.record_processing_measurement(
                    measurement,
                    received_monotonic_ns=received_ns,
                    received_at_utc=received_at_utc,
                )
                self._recorded_measurement_hashes.add(identity_hash)
            return _encode_enqueue_reply(measurement_id, duplicate=duplicate)
        except _DuplicateConflict as error:
            await context.abort(grpc.StatusCode.ALREADY_EXISTS, str(error))
            raise AssertionError("context.abort returned") from error
        except (CollectionError, UnicodeError, ValueError) as error:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(error))
            raise AssertionError("context.abort returned") from error

    def _validated_canonical(self, measurement: Mapping[str, object]) -> bytes:
        canonical = self._validate_measurement(measurement)
        if not isinstance(canonical, bytes) or len(canonical) > _MAX_MEASUREMENT_BYTES:
            raise CollectionError("measurement validator returned invalid canonical JSON")
        if self.expected_identity is None:
            raise CollectionError("measurement sink is not configured")
        for name, expected in self.expected_identity.items():
            if measurement.get(name) != expected:
                raise CollectionError(f"measurement {name} differs from validation control")
        return canonical


def _load_measurement_validator() -> Callable[[object], bytes]:
    """Load the exact measurement validator shipped in the processing image."""
    try:
        module = importlib.import_module("ajin_edge.contracts")
    except ImportError as error:
        raise CollectionError("processing measurement validator is unavailable") from error
    validator = vars(module).get("validate_measurement")
    if not callable(validator):
        raise CollectionError("processing measurement validator is not callable")
    return cast(Callable[[object], bytes], validator)


def _reject_nonfinite_json(value: str) -> object:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _decode_varint(value: bytes, offset: int) -> tuple[int, int]:
    result = 0
    shift = 0
    for index in range(offset, min(len(value), offset + 10)):
        byte = value[index]
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, index + 1
        shift += 7
    raise CollectionError("invalid protobuf varint")


def _encode_varint(value: int) -> bytes:
    if value < 0:
        raise CollectionError("protobuf varint cannot be negative")
    result = bytearray()
    while value >= 0x80:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def _decode_enqueue_request(value: bytes) -> bytes:
    if not value or value[0] != 0x0A:
        raise CollectionError("EnqueueRequest must contain measurement_json field 1")
    length, offset = _decode_varint(value, 1)
    if length > _MAX_MEASUREMENT_BYTES or offset + length != len(value):
        raise CollectionError("EnqueueRequest has invalid measurement_json length")
    return value[offset:]


def _encode_enqueue_reply(measurement_id: str, *, duplicate: bool) -> bytes:
    encoded_identity = measurement_id.encode("utf-8")
    result = b"\x0a" + _encode_varint(len(encoded_identity)) + encoded_identity
    return result + (b"\x10\x01" if duplicate else b"")


async def _start_measurement_server(
    socket_path: Path,
    sink: _MeasurementSink,
) -> grpc.aio.Server:
    if socket_path.exists() or socket_path.is_symlink():
        raise CollectionError("measurement socket path already exists")
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    server = grpc.aio.server(options=(("grpc.max_receive_message_length", 66_000),))
    handler = grpc.method_handlers_generic_handler(
        "ajin.edge.delivery.v1.MeasurementSink",
        {
            "Enqueue": grpc.unary_unary_rpc_method_handler(
                sink.enqueue,
                request_deserializer=lambda value: value,
                response_serializer=lambda value: value,
            )
        },
    )
    server.add_generic_rpc_handlers((handler,))
    if server.add_insecure_port(f"unix:{socket_path}") != 1:
        raise CollectionError("cannot bind measurement socket")
    await server.start()
    try:
        os.chmod(socket_path, 0o660)
    except OSError as error:
        await server.stop(grace=0)
        raise CollectionError("cannot set measurement socket permissions") from error
    return server


def _atomic_write_json(path: Path, document: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    data = json.dumps(document, allow_nan=False, indent=2, sort_keys=True) + "\n"
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def _write_clock(path: Path) -> None:
    _atomic_write_json(
        path,
        {
            "reported_at": datetime.now(UTC).isoformat(),
            "synchronized": True,
            "offset_ms": 0.0,
        },
    )


async def _maintain_clock(path: Path, stop: asyncio.Event) -> None:
    while not stop.is_set():
        _write_clock(path)
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=1.0)


def _absolute_path(value: object, path: str) -> Path:
    result = Path(_string(value, path))
    if not result.is_absolute():
        raise CollectionError(f"{path} must be an absolute local path")
    return result


def _process_target(value: object, path: str, cgroup_root: Path) -> _ProcessTarget:
    target = _mapping(value, path)
    _require_keys(target, {"host_pid", "cgroup_path"}, path)
    host_pid = _integer(target.get("host_pid"), f"{path}.host_pid")
    if host_pid <= 0:
        raise CollectionError(f"{path}.host_pid must be positive")
    cgroup_directory = _absolute_path(target.get("cgroup_path"), f"{path}.cgroup_path")
    if not cgroup_directory.resolve().is_relative_to(cgroup_root.resolve()):
        raise CollectionError(f"{path}.cgroup_path must be below the configured cgroup root")
    return _ProcessTarget(host_pid=host_pid, cgroup_directory=cgroup_directory)


def _load_control(path: Path, *, expected_start_ns: int, cgroup_root: Path) -> _HelperControl:
    try:
        document = _mapping(json.loads(path.read_text(encoding="utf-8")), "control")
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CollectionError("cannot read helper control") from error
    if document.get("schema_version") != "long-validation-control.v1":
        raise CollectionError("unexpected helper control schema_version")
    _require_keys(
        document,
        {
            "schema_version",
            "start_at_monotonic_ns",
            "identity",
            "expected_measurement_identity",
            "device",
            "workload",
            "generator",
            "processing",
            "runtime_telemetry_path",
            "temperature_path",
            "observation_result_path",
            "lifecycle_result_path",
        },
        "control",
    )
    start_ns = _nonnegative_integer(
        document.get("start_at_monotonic_ns"), "control.start_at_monotonic_ns"
    )
    if start_ns != expected_start_ns:
        raise CollectionError("helper control start_at_monotonic_ns mismatch")
    workload = _mapping(document.get("workload"), "control.workload")
    _require_keys(workload, {"mean_fill_duration_s", "observation_mode"}, "control.workload")
    raw_measurement_identity = _mapping(
        document.get("expected_measurement_identity"),
        "control.expected_measurement_identity",
    )
    _require_keys(
        raw_measurement_identity,
        {"site_id", "edge_id", "config_revision", "calibration_version"},
        "control.expected_measurement_identity",
    )
    expected_measurement_identity = {
        field: _string(
            raw_measurement_identity.get(field),
            f"control.expected_measurement_identity.{field}",
        )
        for field in ("site_id", "edge_id", "config_revision", "calibration_version")
    }
    observation_result = document.get("observation_result_path")
    return _HelperControl(
        identity=_mapping(document.get("identity"), "control.identity"),
        expected_measurement_identity=expected_measurement_identity,
        device=_mapping(document.get("device"), "control.device"),
        mean_fill_duration_s=_integer(
            workload.get("mean_fill_duration_s"), "control.workload.mean_fill_duration_s"
        ),
        observation_mode=_string(
            workload.get("observation_mode"), "control.workload.observation_mode"
        ),
        start_at_monotonic_ns=start_ns,
        generator=_process_target(document.get("generator"), "control.generator", cgroup_root),
        processing=_process_target(document.get("processing"), "control.processing", cgroup_root),
        runtime_telemetry_path=_absolute_path(
            document.get("runtime_telemetry_path"), "control.runtime_telemetry_path"
        ),
        temperature_path=_absolute_path(
            document.get("temperature_path"), "control.temperature_path"
        ),
        observation_result_path=(
            None
            if observation_result is None
            else _absolute_path(observation_result, "control.observation_result_path")
        ),
        lifecycle_result_path=_absolute_path(
            document.get("lifecycle_result_path"), "control.lifecycle_result_path"
        ),
    )


async def _wait_for_control(
    path: Path,
    *,
    expected_start_ns: int,
    cgroup_root: Path,
    timeout_s: float,
) -> _HelperControl:
    deadline = time.monotonic() + timeout_s
    last_error: CollectionError | None = None
    while time.monotonic() < deadline:
        if path.is_file():
            try:
                return _load_control(
                    path,
                    expected_start_ns=expected_start_ns,
                    cgroup_root=cgroup_root,
                )
            except CollectionError as error:
                last_error = error
        await asyncio.sleep(0.05)
    if last_error is not None:
        raise last_error
    raise CollectionError("helper control was not provided before timeout")


async def _sleep_until_ns(deadline_ns: int) -> None:
    while (remaining_ns := deadline_ns - time.monotonic_ns()) > 0:
        await asyncio.sleep(remaining_ns / 1_000_000_000)


def _temperature_c(path: Path) -> float:
    try:
        value = float(path.read_text(encoding="ascii").strip())
    except (OSError, UnicodeError, ValueError) as error:
        raise CollectionError("cannot read device temperature") from error
    if value > 1_000.0:
        value /= 1_000.0
    if not math.isfinite(value) or not 0.0 <= value <= 150.0:
        raise CollectionError("device temperature is outside the supported range")
    return value


@dataclass(frozen=True, slots=True)
class _HelperProcessSnapshot:
    monotonic_ns: int
    cpu_time_ns: int


def _helper_cpu_percent(
    previous: _HelperProcessSnapshot,
    current: _HelperProcessSnapshot,
) -> float:
    elapsed_ns = current.monotonic_ns - previous.monotonic_ns
    cpu_ns = current.cpu_time_ns - previous.cpu_time_ns
    if elapsed_ns <= 0 or cpu_ns < 0:
        raise CollectionError("helper CPU counters moved backwards")
    return 100.0 * cpu_ns / elapsed_ns


def _sample_status_files(
    generator_status_dir: Path,
    processing_status_dir: Path,
    *,
    now: datetime | None = None,
) -> tuple[bool, bool, int | None, int | None]:
    paths = (
        generator_status_dir / "lidar-driver-a" / "lidar-driver-a.json",
        generator_status_dir / "lidar-driver-b" / "lidar-driver-b.json",
        processing_status_dir / "lidar-processing.json",
    )
    try:
        statuses = [
            _mapping(json.loads(path.read_text(encoding="utf-8")), path.name) for path in paths
        ]
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        CollectionError,
    ):
        return False, False, None, None
    frame_loss = statuses[-1].get("frame_loss")
    local_loss = statuses[-1].get("local_loss_count")
    if (
        isinstance(frame_loss, bool)
        or not isinstance(frame_loss, int)
        or frame_loss < 0
        or isinstance(local_loss, bool)
        or not isinstance(local_loss, int)
        or local_loss < 0
    ):
        return False, False, None, None
    sampled_at = datetime.now(UTC) if now is None else now
    generator_healthy = all(
        _status_is_current_and_healthy(status, service, sampled_at)
        for status, service in zip(
            statuses[:2],
            ("lidar-driver-a", "lidar-driver-b"),
            strict=True,
        )
    )
    processing_healthy = _status_is_current_and_healthy(
        statuses[-1], "lidar-processing", sampled_at
    )
    return generator_healthy, processing_healthy, frame_loss, local_loss


def _status_is_current_and_healthy(
    status: Mapping[str, object],
    expected_service: str,
    now: datetime,
) -> bool:
    if now.tzinfo is None:
        raise CollectionError("status sampling time must be timezone-aware")
    if (
        status.get("schema_version") != "1.0"
        or status.get("service") != expected_service
        or status.get("state") != "HEALTHY"
    ):
        return False
    try:
        for name in ("reported_at", "last_progress_at"):
            value = _string(status.get(name), f"status.{name}")
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                return False
            age_s = (now - stamp.astimezone(UTC)).total_seconds()
            if not -1.0 <= age_s <= _STATUS_MAX_AGE_S:
                return False
    except (
        CollectionError,
        ValueError,
        OverflowError,
    ):
        return False
    return True


async def _collect_resource_samples(
    collector: RunCollector,
    control: _HelperControl,
    *,
    generator_status_dir: Path,
    processing_status_dir: Path,
    proc_root: Path,
    measurement_start_ns: int,
    sample_count: int,
    sample_interval_s: float,
) -> tuple[int, int, int]:
    interval_ns = round(sample_interval_s * 1_000_000_000)
    await _sleep_until_ns(measurement_start_ns)
    (
        baseline_generator_healthy,
        baseline_processing_healthy,
        baseline_frame_loss,
        baseline_local_loss,
    ) = _sample_status_files(generator_status_dir, processing_status_dir)
    baseline_ns = time.monotonic_ns()
    generator_previous = _read_target_snapshot(
        control.generator, monotonic_ns=baseline_ns, proc_root=proc_root
    )
    processing_previous = _read_target_snapshot(
        control.processing, monotonic_ns=baseline_ns, proc_root=proc_root
    )
    helper_previous = _HelperProcessSnapshot(baseline_ns, time.process_time_ns())
    errors = int(
        not baseline_generator_healthy
        or not baseline_processing_healthy
        or baseline_frame_loss is None
        or baseline_local_loss is None
    )
    final_frame_loss = baseline_frame_loss
    final_local_loss = baseline_local_loss
    for index in range(1, sample_count + 1):
        sample_error = False
        deadline_ns = measurement_start_ns + index * interval_ns
        await _sleep_until_ns(deadline_ns)
        sampled_ns = time.monotonic_ns()
        if _sample_deadline_was_missed(sampled_ns, deadline_ns, interval_ns):
            errors += 1
            continue
        helper_current = _HelperProcessSnapshot(sampled_ns, time.process_time_ns())
        try:
            generator_current = _read_target_snapshot(
                control.generator, monotonic_ns=sampled_ns, proc_root=proc_root
            )
            processing_current = _read_target_snapshot(
                control.processing, monotonic_ns=sampled_ns, proc_root=proc_root
            )
            collector.record_cgroup_interval(
                generator_previous,
                generator_current,
                processing_previous,
                processing_current,
                system_load_1m=os.getloadavg()[0],
                device_temperature_c=_temperature_c(control.temperature_path),
                helper_cpu_percent=_helper_cpu_percent(helper_previous, helper_current),
                helper_rss_bytes=_read_process_rss_bytes(proc_root, os.getpid()),
            )
            generator_previous = generator_current
            processing_previous = processing_current
            helper_previous = helper_current
        except (
            CollectionError,
            OSError,
        ):
            sample_error = True
        (
            generator_healthy,
            processing_healthy,
            current_frame_loss,
            current_local_loss,
        ) = _sample_status_files(generator_status_dir, processing_status_dir)
        collector.record_service_status(
            generator_healthy=generator_healthy,
            processing_healthy=processing_healthy,
        )
        if current_frame_loss is None or current_local_loss is None:
            sample_error = True
        if current_frame_loss is not None:
            final_frame_loss = current_frame_loss
        if current_local_loss is not None:
            final_local_loss = current_local_loss
        errors += sample_error
    if (
        baseline_frame_loss is None
        or final_frame_loss is None
        or baseline_local_loss is None
        or final_local_loss is None
    ):
        return errors, 0, 0
    if final_frame_loss < baseline_frame_loss or final_local_loss < baseline_local_loss:
        return errors + 1, 0, 0
    return errors, final_frame_loss, final_local_loss


def _sample_deadline_was_missed(sampled_ns: int, deadline_ns: int, interval_ns: int) -> bool:
    if interval_ns <= 0:
        raise CollectionError("resource sample interval must be positive")
    return sampled_ns - deadline_ns > min(interval_ns, _MAX_RESOURCE_SAMPLE_LATENESS_NS)


def _read_target_snapshot(
    target: _ProcessTarget,
    *,
    monotonic_ns: int,
    proc_root: Path,
) -> CgroupSnapshot:
    if target.host_pid not in _read_recursive_cgroup_process_ids(target.cgroup_directory):
        raise CollectionError("controlled host process is absent from its cgroup")
    return read_cgroup_snapshot(
        target.cgroup_directory,
        monotonic_ns=monotonic_ns,
        proc_root=proc_root,
    )


async def _wait_for_file(path: Path, timeout_s: float, name: str) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.is_file():
            return
        await asyncio.sleep(0.05)
    raise CollectionError(f"{name} was not provided before timeout")


def _load_counter_result(path: Path, names: set[str], label: str) -> dict[str, int]:
    try:
        document = _mapping(json.loads(path.read_text(encoding="utf-8")), label)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CollectionError(f"cannot read {label}") from error
    if set(document) != names | {"schema_version"}:
        raise CollectionError(f"{label} fields differ from its contract")
    if document["schema_version"] != f"long-validation-{label}.v1":
        raise CollectionError(f"unexpected {label} schema_version")
    return {name: _nonnegative_integer(document[name], f"{label}.{name}") for name in names}


async def _run_helper(arguments: argparse.Namespace) -> dict[str, object]:
    _validate_cli_paths(arguments)
    if arguments.output.exists() or arguments.output.is_symlink():
        raise CollectionError("output path already exists")
    if arguments.ready_file.exists() or arguments.ready_file.is_symlink():
        raise CollectionError("ready path already exists")
    sink = _MeasurementSink(_load_measurement_validator())
    server = await _start_measurement_server(arguments.measurement_socket, sink)
    clock_stop = asyncio.Event()
    clock_task: asyncio.Task[None] | None = None
    try:
        _write_clock(arguments.clock_file)
        clock_task = asyncio.create_task(_maintain_clock(arguments.clock_file, clock_stop))
        ready_ns = time.monotonic_ns()
        start_ns = ready_ns + round(arguments.setup_lead_s * 1_000_000_000)
        _atomic_write_json(
            arguments.ready_file,
            {
                "schema_version": "long-validation-helper-ready.v1",
                "ready_at_monotonic_ns": ready_ns,
                "start_at_monotonic_ns": start_ns,
            },
        )
        control = await _wait_for_control(
            arguments.control,
            expected_start_ns=start_ns,
            cgroup_root=arguments.cgroup_root,
            timeout_s=arguments.control_timeout_s,
        )
        if time.monotonic_ns() >= start_ns:
            raise CollectionError("helper control arrived after the shared start deadline")
        protected_inputs = {
            control.runtime_telemetry_path.resolve(),
            control.temperature_path.resolve(),
            control.lifecycle_result_path.resolve(),
        }
        if control.observation_result_path is not None:
            protected_inputs.add(control.observation_result_path.resolve())
        if (
            arguments.output.resolve() in protected_inputs
            or arguments.ready_file.resolve() in protected_inputs
        ):
            raise CollectionError("helper output paths overlap a control input")
        collector = _collector_from_control(control)
        measurement_start_ns = start_ns + round(arguments.warmup_s * 1_000_000_000)
        measurement_end_ns = measurement_start_ns + round(arguments.measure_s * 1_000_000_000)
        sink.configure(
            collector,
            measurement_started_ns=measurement_start_ns,
            measurement_ended_ns=measurement_end_ns,
            expected_identity=control.expected_measurement_identity,
        )
        (
            resource_errors,
            processing_frame_loss,
            processing_local_loss,
        ) = await _collect_resource_samples(
            collector,
            control,
            generator_status_dir=arguments.generator_status_dir,
            processing_status_dir=arguments.processing_status_dir,
            proc_root=arguments.proc_root,
            measurement_start_ns=measurement_start_ns,
            sample_count=EXPECTED_RESOURCE_SAMPLES,
            sample_interval_s=1.0,
        )
        await _wait_for_file(
            control.runtime_telemetry_path, arguments.final_result_timeout_s, "runtime telemetry"
        )
        collector.ingest_runtime_telemetry(load_runtime_telemetry(control.runtime_telemetry_path))
        lifecycle = await _load_lifecycle(control, arguments.final_result_timeout_s)
        collector.set_event_counts(
            unclassified_loss_windows=(lifecycle["unclassified_loss_windows"] + resource_errors),
            container_restarts=lifecycle["container_restarts"],
            oom_events=lifecycle["oom_events"],
            thermal_throttling_events=lifecycle["thermal_throttling_events"],
            processing_frame_loss=processing_frame_loss,
            processing_local_loss=processing_local_loss,
        )
        received, observation_run_id = await _load_observation_received(
            control, arguments.final_result_timeout_s
        )
        collector.set_observation_received(received, observation_run_id)
        return collector.build_result()
    finally:
        clock_stop.set()
        try:
            if clock_task is not None:
                await clock_task
        finally:
            await server.stop(grace=2.0)
            with suppress(FileNotFoundError):
                arguments.measurement_socket.unlink()


def _validate_cli_paths(arguments: argparse.Namespace) -> None:
    paths = {
        "control": arguments.control,
        "measurement socket": arguments.measurement_socket,
        "generator status directory": arguments.generator_status_dir,
        "processing status directory": arguments.processing_status_dir,
        "clock file": arguments.clock_file,
        "ready file": arguments.ready_file,
        "output": arguments.output,
        "proc root": arguments.proc_root,
        "cgroup root": arguments.cgroup_root,
    }
    for name, path in paths.items():
        if not path.is_absolute():
            raise CollectionError(f"{name} must be an absolute path")
    unique_files = {
        arguments.control.resolve(),
        arguments.measurement_socket.resolve(),
        arguments.clock_file.resolve(),
        arguments.ready_file.resolve(),
        arguments.output.resolve(),
    }
    if len(unique_files) != 5:
        raise CollectionError("helper file paths must be distinct")


def _collector_from_control(control: _HelperControl) -> RunCollector:
    identity = control.identity
    _require_keys(
        identity,
        {
            "generator_source_commit",
            "generator_image_digest",
            "processing_source_commit",
            "processing_image_digest",
            "config_fingerprint_sha256",
            "processing_config_sha256",
            "seed",
        },
        "control.identity",
    )
    collector = RunCollector(
        generator_source_commit=_string(
            identity.get("generator_source_commit"), "identity.generator_source_commit"
        ),
        generator_image_digest=_string(
            identity.get("generator_image_digest"), "identity.generator_image_digest"
        ),
        processing_source_commit=_string(
            identity.get("processing_source_commit"), "identity.processing_source_commit"
        ),
        processing_image_digest=_string(
            identity.get("processing_image_digest"), "identity.processing_image_digest"
        ),
        config_fingerprint_sha256=_string(
            identity.get("config_fingerprint_sha256"), "identity.config_fingerprint_sha256"
        ),
        processing_config_sha256=_string(
            identity.get("processing_config_sha256"), "identity.processing_config_sha256"
        ),
        seed=_nonnegative_integer(identity.get("seed"), "identity.seed"),
        device=control.device,
        mean_fill_duration_s=control.mean_fill_duration_s,
        observation_mode=control.observation_mode,
    )
    collector.build_result()
    return collector


async def _load_lifecycle(control: _HelperControl, timeout_s: float) -> dict[str, int]:
    await _wait_for_file(control.lifecycle_result_path, timeout_s, "lifecycle result")
    return _load_counter_result(
        control.lifecycle_result_path,
        {
            "unclassified_loss_windows",
            "container_restarts",
            "oom_events",
            "thermal_throttling_events",
        },
        "lifecycle-result",
    )


async def _load_observation_received(
    control: _HelperControl, timeout_s: float
) -> tuple[int, str | None]:
    if control.observation_mode == "noop":
        if control.observation_result_path is not None:
            raise CollectionError("no-op run must not provide an observation result path")
        return 0, None
    if control.observation_result_path is None:
        raise CollectionError("actual observation run requires receiver evidence")
    await _wait_for_file(control.observation_result_path, timeout_s, "observation result")
    try:
        result = _mapping(
            json.loads(control.observation_result_path.read_text(encoding="utf-8")),
            "observation-result",
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CollectionError("cannot read observation-result") from error
    return _validate_observation_result(result, control)


def _validate_observation_result(
    result: Mapping[str, object], control: _HelperControl
) -> tuple[int, str]:
    _require_keys(
        result,
        {
            "schema_version",
            "run_id",
            "generator_source_commit",
            "environment_id",
            "input_fingerprint_sha256",
            "seed",
            "scene_fingerprint_sha256",
            "received_records",
            "first_sequence",
            "last_sequence",
            "first_elapsed_s",
            "last_elapsed_s",
            "surface_stream_sha256",
            "surface_change_count",
            "minimum_surface_volume_m3",
            "maximum_surface_volume_m3",
        },
        "observation-result",
    )
    if result["schema_version"] != "long-validation-observation-result.v1":
        raise CollectionError("unexpected observation-result schema_version")
    identity = control.identity
    if result["generator_source_commit"] != identity.get("generator_source_commit") or result[
        "seed"
    ] != identity.get("seed"):
        raise CollectionError("observation-result identity differs from validation control")
    _validate_observation_result_digests(result)
    received = _validate_observation_result_range(result)
    return received, _string(result["run_id"], "observation-result.run_id")


def _validate_observation_result_digests(result: Mapping[str, object]) -> None:
    for name in (
        "input_fingerprint_sha256",
        "scene_fingerprint_sha256",
        "surface_stream_sha256",
    ):
        value = _string(result[name], f"observation-result.{name}")
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise CollectionError("observation-result fingerprint is invalid")


def _validate_observation_result_range(result: Mapping[str, object]) -> int:
    received = _nonnegative_integer(
        result["received_records"], "observation-result.received_records"
    )
    if (
        received != EXPECTED_OBSERVATION_RECORDS
        or _nonnegative_integer(result["first_sequence"], "observation-result.first_sequence") != 1
        or _nonnegative_integer(result["last_sequence"], "observation-result.last_sequence")
        != EXPECTED_OBSERVATION_RECORDS
        or _finite_nonnegative(result["first_elapsed_s"], "observation-result.first_elapsed_s")
        != 0.1
        or _finite_nonnegative(result["last_elapsed_s"], "observation-result.last_elapsed_s")
        != 3_900.0
        or _nonnegative_integer(
            result["surface_change_count"], "observation-result.surface_change_count"
        )
        != EXPECTED_OBSERVATION_RECORDS - 1
    ):
        raise CollectionError("observation-result cadence evidence is incomplete")
    minimum_volume = _finite_nonnegative(
        result["minimum_surface_volume_m3"],
        "observation-result.minimum_surface_volume_m3",
    )
    maximum_volume = _finite_nonnegative(
        result["maximum_surface_volume_m3"],
        "observation-result.maximum_surface_volume_m3",
    )
    if maximum_volume <= minimum_volume:
        raise CollectionError("observation-result surface volume evidence is invalid")
    return received


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise argparse.ArgumentTypeError("value must be finite and positive")
    return parsed


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """Build the live helper CLI parser."""
    parser = argparse.ArgumentParser(
        description="Collect bounded edge validation aggregates and serve a measurement sink.",
        epilog=(
            "Run the helper container with --pid=host, --cgroupns=host, and read-only "
            "/proc and /sys/fs/cgroup mounts."
        ),
    )
    parser.add_argument("--control", required=True, type=Path)
    parser.add_argument("--measurement-socket", required=True, type=Path)
    parser.add_argument("--generator-status-dir", required=True, type=Path)
    parser.add_argument("--processing-status-dir", required=True, type=Path)
    parser.add_argument("--clock-file", required=True, type=Path)
    parser.add_argument("--ready-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))
    parser.add_argument("--cgroup-root", type=Path, default=Path("/sys/fs/cgroup"))
    parser.add_argument(
        "--warmup-s",
        type=_positive_int,
        choices=(EXPECTED_WARMUP_DURATION_S,),
        default=EXPECTED_WARMUP_DURATION_S,
    )
    parser.add_argument(
        "--measure-s",
        type=_positive_int,
        choices=(EXPECTED_MEASUREMENT_DURATION_S,),
        default=EXPECTED_MEASUREMENT_DURATION_S,
    )
    parser.add_argument("--setup-lead-s", type=_positive_float, default=30.0)
    parser.add_argument("--control-timeout-s", type=_positive_float, default=20.0)
    parser.add_argument("--final-result-timeout-s", type=_positive_float, default=30.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the live helper and write one aggregate run result."""
    arguments = build_parser().parse_args(argv)
    try:
        result = asyncio.run(_run_helper(arguments))
        _atomic_write_json(arguments.output, result)
    except (CollectionError, ResultError, OSError, grpc.RpcError) as error:
        print(f"long validation helper failed: {error}", file=sys.stderr)
        return 2
    evaluation = _mapping(result["evaluation"], "evaluation")
    return 0 if evaluation["passed"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
