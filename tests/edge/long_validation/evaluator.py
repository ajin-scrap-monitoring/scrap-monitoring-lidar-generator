"""Evaluate aggregate edge soak-test results without retaining raw measurements."""

import argparse
import copy
import json
import math
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

RUN_SCHEMA_VERSION = "run-result.v1"
MATRIX_SCHEMA_VERSION = "matrix-result.v1"
EXPECTED_SENSOR_IDS = ("lidar_1", "lidar_2")
EXPECTED_RESOURCE_SAMPLES = 3_600
EXPECTED_LATENCY_SAMPLES = 36_000
EXPECTED_STATUS_SAMPLES = 3_600
EXPECTED_OBSERVATION_RECORDS = 3_901
MAX_PROCESSING_FRESHNESS_GAP_S = 2.0
MAX_PROCESSING_DELIVERY_AGE_MS = 2_000.0
EXPECTED_WARMUP_DURATION_S = 300
EXPECTED_MEASUREMENT_DURATION_S = 3_600
CPU_P95_LIMIT_PERCENT = 75.0
RSS_P95_LIMIT_BYTES = 128 * 1_048_576
JOINT_LATENCY_P99_LIMIT_MS = 70.0
MAX_SCENARIO_TRANSITIONS = 64
MIN_ACCELERATED_COMPLETE_CYCLES = 5
OBSERVATION_CPU_DELTA_LIMIT_PERCENTAGE_POINTS = 5.0
EXPECTED_RUN_KEYS = frozenset(
    (duration, mode) for duration in (600, 86_400) for mode in ("actual", "noop")
)

RUN_FAILURE_CODES = frozenset(
    {
        "cpu_sample_count",
        "rss_sample_count",
        "sensor_latency_sample_count",
        "joint_latency_sample_count",
        "processing_measurement_missing",
        "processing_measurement_age",
        "processing_freshness",
        "auxiliary_resource_sample_count",
        "service_status_sample_count",
        "service_status_not_healthy",
        "generator_cpu_p95",
        "generator_rss_p95",
        "joint_latency_p99",
        "producer_sequence_gap",
        "processing_frame_loss",
        "processing_local_loss",
        "processing_sequence_regression",
        "scan_server_frame_loss",
        "scan_subscriber_missing",
        "sequence_duplicate_or_regression",
        "unclassified_loss_window",
        "container_restart",
        "oom_event",
        "thermal_throttling_event",
        "processing_not_good",
        "observation_delivery",
        "observation_noop_activity",
        "observation_evidence_missing",
        "event_evidence_missing",
        "scenario_cycle_transition_missing",
        "scenario_unexpected_transition",
        "scenario_evidence_missing",
        "scenario_transition_latency",
        "scenario_transition_latency_sample_count",
        "simulator_semantics",
        "lidar_processing_semantics",
    }
)
MATRIX_FAILURE_CODES = frozenset(
    {
        "run_failed",
        "observation_cpu_delta",
        "observation_cpu_delta_unavailable",
        "comparison_identity_mismatch",
        "scenario_schedule_mismatch",
    }
)


class ResultError(ValueError):
    """Raised when a result does not implement the aggregate result contract."""


def nearest_rank(values: Sequence[float], percentile: int) -> float:
    """Return an exact, non-interpolated nearest-rank percentile."""
    if not values:
        raise ValueError("percentile requires at least one sample")
    if not 1 <= percentile <= 100:
        raise ValueError("percentile must be an integer from 1 through 100")
    if any(isinstance(value, bool) or not math.isfinite(value) for value in values):
        raise ValueError("percentile samples must be finite numbers")
    ordered = sorted(values)
    rank = (percentile * len(ordered) + 99) // 100
    return float(ordered[rank - 1])


def _mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ResultError(f"{path} must be an object")
    return cast(Mapping[str, object], value)


def _array(value: object, path: str) -> list[object]:
    if not isinstance(value, list):
        raise ResultError(f"{path} must be an array")
    return value


def _string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ResultError(f"{path} must be a non-empty string")
    return value


def _integer(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ResultError(f"{path} must be an integer")
    return value


def _nonnegative_integer(value: object, path: str) -> int:
    result = _integer(value, path)
    if result < 0:
        raise ResultError(f"{path} must be non-negative")
    return result


def _number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResultError(f"{path} must be a number")
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        raise ResultError(f"{path} must be a finite non-negative number")
    return result


def _optional_number(value: object, path: str) -> float | None:
    return None if value is None else _number(value, path)


def _require_keys(value: Mapping[str, object], keys: set[str], path: str) -> None:
    actual = set(value)
    if actual != keys:
        missing = sorted(keys - actual)
        extra = sorted(actual - keys)
        raise ResultError(f"{path} keys differ: missing={missing} extra={extra}")


def _metric(
    document: Mapping[str, object],
    name: str,
    percentile_name: str,
) -> tuple[int, int, float | None]:
    metric = _mapping(document.get(name), f"metrics.{name}")
    _require_keys(metric, {"sample_count", "missing_count", percentile_name}, name)
    return (
        _nonnegative_integer(metric["sample_count"], f"{name}.sample_count"),
        _nonnegative_integer(metric["missing_count"], f"{name}.missing_count"),
        _optional_number(metric[percentile_name], f"{name}.{percentile_name}"),
    )


def _metric_complete(
    count: int,
    missing: int,
    value: float | None,
    expected: int,
) -> bool:
    return count == expected and missing == 0 and value is not None


def _sensor_metrics(metrics: Mapping[str, object]) -> dict[str, tuple[int, int, float | None]]:
    result: dict[str, tuple[int, int, float | None]] = {}
    ordered_ids: list[str] = []
    for index, item_value in enumerate(
        _array(metrics.get("sensor_frame_completion_latency_ms"), "sensor latency metrics")
    ):
        item = _mapping(item_value, f"sensor latency metric {index}")
        _require_keys(
            item,
            {"sensor_id", "sample_count", "missing_count", "p99"},
            f"sensor latency metric {index}",
        )
        sensor_id = _string(item["sensor_id"], f"sensor latency metric {index}.sensor_id")
        if sensor_id in result:
            raise ResultError(f"duplicate sensor latency metric: {sensor_id}")
        ordered_ids.append(sensor_id)
        result[sensor_id] = (
            _nonnegative_integer(item["sample_count"], f"{sensor_id}.sample_count"),
            _nonnegative_integer(item["missing_count"], f"{sensor_id}.missing_count"),
            _optional_number(item["p99"], f"{sensor_id}.p99"),
        )
    if tuple(ordered_ids) != EXPECTED_SENSOR_IDS:
        raise ResultError("sensor latency metrics must be ordered lidar_1 then lidar_2")
    return result


def _processing_statuses(metrics: Mapping[str, object]) -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    ordered_ids: list[str] = []
    for index, item_value in enumerate(
        _array(metrics.get("processing_status"), "processing status metrics")
    ):
        item = _mapping(item_value, f"processing status metric {index}")
        _require_keys(
            item,
            {"result_id", "sample_count", "good_count"},
            f"processing status metric {index}",
        )
        result_id = _string(item["result_id"], f"processing status metric {index}.result_id")
        if result_id in result:
            raise ResultError(f"duplicate processing status metric: {result_id}")
        ordered_ids.append(result_id)
        sample_count = _nonnegative_integer(item["sample_count"], f"{result_id}.sample_count")
        good_count = _nonnegative_integer(item["good_count"], f"{result_id}.good_count")
        if good_count > sample_count:
            raise ResultError(f"{result_id}.good_count exceeds sample_count")
        result[result_id] = (sample_count, good_count)
    if tuple(ordered_ids) != (*EXPECTED_SENSOR_IDS, "fused"):
        raise ResultError("processing status metrics must be ordered lidar_1, lidar_2 then fused")
    return result


def _service_statuses(metrics: Mapping[str, object]) -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    ordered_components: list[str] = []
    for index, item_value in enumerate(_array(metrics.get("service_status"), "service status")):
        item = _mapping(item_value, f"service status {index}")
        _require_keys(
            item,
            {"component", "sample_count", "healthy_count"},
            f"service status {index}",
        )
        component = _string(item["component"], f"service status {index}.component")
        if component in result:
            raise ResultError(f"duplicate service status: {component}")
        ordered_components.append(component)
        sample_count = _nonnegative_integer(item["sample_count"], f"{component}.sample_count")
        healthy_count = _nonnegative_integer(item["healthy_count"], f"{component}.healthy_count")
        if healthy_count > sample_count:
            raise ResultError(f"{component}.healthy_count exceeds sample_count")
        result[component] = sample_count, healthy_count
    if tuple(ordered_components) != ("generator", "processing"):
        raise ResultError("service status must be ordered generator then processing")
    return result


def _processing_freshness(metrics: Mapping[str, object]) -> tuple[int, int, float | None]:
    freshness = _mapping(
        metrics.get("processing_measurement_freshness_s"),
        "metrics.processing_measurement_freshness_s",
    )
    _require_keys(
        freshness,
        {"measurement_count", "gap_count", "maximum_gap_s"},
        "processing measurement freshness",
    )
    return (
        _nonnegative_integer(freshness["measurement_count"], "freshness.measurement_count"),
        _nonnegative_integer(freshness["gap_count"], "freshness.gap_count"),
        _optional_number(freshness["maximum_gap_s"], "freshness.maximum_gap_s"),
    )


def _processing_delivery_age(metrics: Mapping[str, object]) -> tuple[int, int, float | None]:
    age = _mapping(metrics.get("processing_delivery_age_ms"), "processing delivery age")
    _require_keys(
        age,
        {"sample_count", "missing_count", "maximum"},
        "processing delivery age",
    )
    return (
        _nonnegative_integer(age["sample_count"], "processing age.sample_count"),
        _nonnegative_integer(age["missing_count"], "processing age.missing_count"),
        _optional_number(age["maximum"], "processing age.maximum"),
    )


def _identity(document: Mapping[str, object]) -> tuple[object, ...]:
    identity = _mapping(document.get("identity"), "identity")
    names = {
        "generator_source_commit",
        "generator_image_digest",
        "processing_source_commit",
        "processing_image_digest",
        "config_fingerprint_sha256",
        "processing_config_sha256",
        "seed",
    }
    _require_keys(identity, names, "identity")
    seed = _nonnegative_integer(identity["seed"], "identity.seed")
    for name in ("generator_source_commit", "processing_source_commit"):
        _lower_hex(identity[name], 40, f"identity.{name}")
    for name in ("generator_image_digest", "processing_image_digest"):
        value = _string(identity[name], f"identity.{name}")
        if not value.startswith("sha256:"):
            raise ResultError(f"identity.{name} must be a sha256 digest")
        _lower_hex(value[7:], 64, f"identity.{name}")
    for name in ("config_fingerprint_sha256", "processing_config_sha256"):
        _lower_hex(identity[name], 64, f"identity.{name}")
    return (
        *(_string(identity[name], f"identity.{name}") for name in sorted(names - {"seed"})),
        seed,
    )


def _lower_hex(value: object, length: int, path: str) -> str:
    result = _string(value, path)
    if len(result) != length or result.lower() != result:
        raise ResultError(f"{path} must contain {length} lowercase hexadecimal digits")
    try:
        int(result, 16)
    except ValueError as error:
        raise ResultError(f"{path} must contain {length} lowercase hexadecimal digits") from error
    return result


def _device(document: Mapping[str, object]) -> tuple[object, ...]:
    device = _mapping(document.get("device"), "device")
    names = {
        "model",
        "architecture",
        "os_release",
        "kernel",
        "docker_version",
        "logical_cpu_count",
        "effective_cpu_count",
        "memory_bytes",
        "cpu_governor",
        "cooling",
        "runtime_constraints",
    }
    _require_keys(device, names, "device")
    strings = tuple(
        _string(device[name], f"device.{name}")
        for name in (
            "model",
            "architecture",
            "os_release",
            "kernel",
            "docker_version",
            "cpu_governor",
            "cooling",
        )
    )
    if strings[1] != "aarch64":
        raise ResultError("device architecture must be aarch64")
    logical_cpu_count = _integer(device["logical_cpu_count"], "device.logical_cpu_count")
    effective_cpu_count = _integer(device["effective_cpu_count"], "device.effective_cpu_count")
    memory_bytes = _integer(device["memory_bytes"], "device.memory_bytes")
    if logical_cpu_count != 4 or effective_cpu_count != 4:
        raise ResultError("device must expose all four Raspberry Pi 5 CPUs")
    if not 7 * 1_073_741_824 <= memory_bytes < 9 * 1_073_741_824:
        raise ResultError("device memory is outside the Raspberry Pi 5 8 GB range")
    constraints = _runtime_constraints(device["runtime_constraints"])
    return (*strings, logical_cpu_count, effective_cpu_count, memory_bytes, constraints)


def _runtime_constraints(value: object) -> tuple[tuple[object, ...], ...]:
    result: list[tuple[object, ...]] = []
    for index, raw in enumerate(_array(value, "device.runtime_constraints")):
        item = _mapping(raw, f"device.runtime_constraints[{index}]")
        _require_keys(
            item,
            {"component", "effective_cpu_count", "cpu_quota_cores", "memory_limit_bytes"},
            f"device.runtime_constraints[{index}]",
        )
        component = _string(item["component"], f"runtime constraint {index}.component")
        cpu_count = _integer(
            item["effective_cpu_count"], f"runtime constraint {index}.effective_cpu_count"
        )
        cpu_quota = _optional_number(
            item["cpu_quota_cores"], f"runtime constraint {index}.cpu_quota_cores"
        )
        raw_memory = item["memory_limit_bytes"]
        memory_limit = (
            None
            if raw_memory is None
            else _integer(raw_memory, f"runtime constraint {index}.memory_limit_bytes")
        )
        if cpu_count != 4 or (cpu_quota is not None and cpu_quota < 4.0):
            raise ResultError("runtime constraint does not expose four CPU cores")
        if memory_limit is not None and memory_limit < 7 * 1_073_741_824:
            raise ResultError("runtime memory limit is below the 8 GB device range")
        result.append((component, cpu_count, cpu_quota, memory_limit))
    if [item[0] for item in result] != ["generator", "processing", "helper"]:
        raise ResultError("runtime constraints must be ordered generator, processing then helper")
    return tuple(result)


def _workload(document: Mapping[str, object]) -> tuple[int, str]:
    workload = _mapping(document.get("workload"), "workload")
    _require_keys(
        workload,
        {
            "mean_fill_duration_s",
            "observation_mode",
            "warmup_duration_s",
            "measurement_duration_s",
            "sensor_ids",
        },
        "workload",
    )
    duration = _integer(workload["mean_fill_duration_s"], "workload.mean_fill_duration_s")
    mode = _string(workload["observation_mode"], "workload.observation_mode")
    if (duration, mode) not in EXPECTED_RUN_KEYS:
        raise ResultError("workload is not one of the four required matrix combinations")
    if (
        _integer(workload["warmup_duration_s"], "workload.warmup_duration_s")
        != EXPECTED_WARMUP_DURATION_S
    ):
        raise ResultError("workload.warmup_duration_s must be 300")
    if (
        _integer(workload["measurement_duration_s"], "workload.measurement_duration_s")
        != EXPECTED_MEASUREMENT_DURATION_S
    ):
        raise ResultError("workload.measurement_duration_s must be 3600")
    sensor_ids = tuple(
        _string(item, f"workload.sensor_ids[{index}]")
        for index, item in enumerate(_array(workload["sensor_ids"], "workload.sensor_ids"))
    )
    if sensor_ids != EXPECTED_SENSOR_IDS:
        raise ResultError("workload.sensor_ids must be [lidar_1, lidar_2]")
    return duration, mode


def _events(document: Mapping[str, object]) -> dict[str, int]:
    events = _mapping(document.get("events"), "events")
    expected = {
        "producer_sequence_gaps",
        "processing_frame_loss",
        "processing_local_loss",
        "processing_sequence_regressions",
        "scan_server_frame_loss",
        "scan_subscriber_missing_lanes",
        "sequence_duplicates_or_regressions",
        "unclassified_loss_windows",
        "container_restarts",
        "oom_events",
        "thermal_throttling_events",
        "evidence_complete",
    }
    _require_keys(events, expected, "events")
    if not isinstance(events["evidence_complete"], bool):
        raise ResultError("events.evidence_complete must be a boolean")
    return {
        name: _nonnegative_integer(events[name], f"events.{name}")
        for name in expected - {"evidence_complete"}
    } | {"evidence_complete": int(events["evidence_complete"])}


def _scenario_phase(value: object, path: str) -> str:
    phase = _string(value, path)
    if phase not in {"filling", "collecting"}:
        raise ResultError(f"{path} must be filling or collecting")
    return phase


def _scenario_counts(scenario: Mapping[str, object]) -> tuple[int, int, int]:
    transition_count = _nonnegative_integer(
        scenario["transition_count"], "scenario.transition_count"
    )
    filling_to_collecting = _nonnegative_integer(
        scenario["filling_to_collecting_count"],
        "scenario.filling_to_collecting_count",
    )
    collecting_to_filling = _nonnegative_integer(
        scenario["collecting_to_filling_count"],
        "scenario.collecting_to_filling_count",
    )
    if transition_count > MAX_SCENARIO_TRANSITIONS:
        raise ResultError("scenario.transition_count exceeds its bounded capacity")
    if transition_count != filling_to_collecting + collecting_to_filling:
        raise ResultError("scenario transition counts are inconsistent")
    return transition_count, filling_to_collecting, collecting_to_filling


def _validate_scenario_progression(
    *,
    initial_phase: str,
    initial_cycle: int,
    final_phase: str,
    final_cycle: int,
    filling_to_collecting: int,
    collecting_to_filling: int,
) -> None:
    if initial_phase != "filling" or initial_cycle != 0:
        raise ResultError("scenario must start in filling cycle 0")
    if filling_to_collecting == collecting_to_filling:
        expected_final_phase = "filling"
    elif filling_to_collecting == collecting_to_filling + 1:
        expected_final_phase = "collecting"
    else:
        raise ResultError("scenario phase transition counts are not contiguous")
    if final_phase != expected_final_phase or final_cycle != collecting_to_filling:
        raise ResultError("scenario final state is inconsistent with its transitions")


def _scenario_latency(scenario: Mapping[str, object]) -> tuple[int, float | None]:
    latency_count = _nonnegative_integer(
        scenario["transition_latency_sample_count"],
        "scenario.transition_latency_sample_count",
    )
    maximum_latency = _optional_number(
        scenario["maximum_transition_latency_ms"],
        "scenario.maximum_transition_latency_ms",
    )
    if (latency_count == 0) != (maximum_latency is None):
        raise ResultError("scenario transition latency aggregate is inconsistent")
    if latency_count > 2 * MAX_SCENARIO_TRANSITIONS:
        raise ResultError("scenario transition latency sample count exceeds capacity")
    return latency_count, maximum_latency


def _scenario(document: Mapping[str, object]) -> dict[str, object]:
    scenario = _mapping(document.get("scenario"), "scenario")
    expected = {
        "initial_phase",
        "initial_cycle_index",
        "final_phase",
        "final_cycle_index",
        "transition_count",
        "filling_to_collecting_count",
        "collecting_to_filling_count",
        "transition_latency_sample_count",
        "maximum_transition_latency_ms",
        "schedule_sha256",
        "evidence_complete",
    }
    _require_keys(scenario, expected, "scenario")
    if not isinstance(scenario["evidence_complete"], bool):
        raise ResultError("scenario.evidence_complete must be a boolean")
    initial_phase = _scenario_phase(scenario["initial_phase"], "scenario.initial_phase")
    final_phase = _scenario_phase(scenario["final_phase"], "scenario.final_phase")
    initial_cycle = _nonnegative_integer(
        scenario["initial_cycle_index"], "scenario.initial_cycle_index"
    )
    final_cycle = _nonnegative_integer(scenario["final_cycle_index"], "scenario.final_cycle_index")
    transition_count, filling_to_collecting, collecting_to_filling = _scenario_counts(scenario)
    _validate_scenario_progression(
        initial_phase=initial_phase,
        initial_cycle=initial_cycle,
        final_phase=final_phase,
        final_cycle=final_cycle,
        filling_to_collecting=filling_to_collecting,
        collecting_to_filling=collecting_to_filling,
    )
    latency_count, maximum_latency = _scenario_latency(scenario)
    schedule_sha256 = _lower_hex(scenario["schedule_sha256"], 64, "scenario.schedule_sha256")
    return {
        "transition_count": transition_count,
        "completed_cycle_count": collecting_to_filling,
        "latency_sample_count": latency_count,
        "maximum_latency_ms": maximum_latency,
        "schedule_sha256": schedule_sha256,
        "evidence_complete": scenario["evidence_complete"],
    }


def _cgroup_throttling(metrics: Mapping[str, object]) -> None:
    values = _array(metrics.get("cgroup_throttling"), "metrics.cgroup_throttling")
    components: set[str] = set()
    ordered_components: list[str] = []
    for index, raw_value in enumerate(values):
        value = _mapping(raw_value, f"metrics.cgroup_throttling[{index}]")
        _require_keys(
            value,
            {"component", "nr_throttled_delta", "throttled_usec_delta"},
            f"metrics.cgroup_throttling[{index}]",
        )
        component = _string(value["component"], f"metrics.cgroup_throttling[{index}].component")
        if component not in {"generator", "processing"} or component in components:
            raise ResultError("cgroup throttling must contain generator and processing once")
        components.add(component)
        ordered_components.append(component)
        _nonnegative_integer(
            value["nr_throttled_delta"],
            f"metrics.cgroup_throttling[{index}].nr_throttled_delta",
        )
        _nonnegative_integer(
            value["throttled_usec_delta"],
            f"metrics.cgroup_throttling[{index}].throttled_usec_delta",
        )
    if ordered_components != ["generator", "processing"]:
        raise ResultError("cgroup throttling must be ordered generator then processing")


def _observation(document: Mapping[str, object], mode: str) -> dict[str, int]:
    observation = _mapping(document.get("observation"), "observation")
    expected = {
        "sent_records",
        "received_records",
        "dropped_records",
        "connection_failures",
        "evidence_complete",
    }
    _require_keys(observation, expected, "observation")
    if not isinstance(observation["evidence_complete"], bool):
        raise ResultError("observation.evidence_complete must be a boolean")
    result = {
        name: _nonnegative_integer(observation[name], f"observation.{name}")
        for name in expected - {"evidence_complete"}
    }
    result["evidence_complete"] = int(observation["evidence_complete"])
    if mode == "actual" and result["received_records"] > result["sent_records"]:
        raise ResultError("observation received_records exceeds sent_records")
    return result


def _evaluation(document: Mapping[str, object]) -> tuple[bool, list[str]]:
    evaluation = _mapping(document.get("evaluation"), "evaluation")
    _require_keys(evaluation, {"passed", "failure_codes"}, "evaluation")
    passed = evaluation["passed"]
    if not isinstance(passed, bool):
        raise ResultError("evaluation.passed must be a boolean")
    codes = [
        _string(code, f"evaluation.failure_codes[{index}]")
        for index, code in enumerate(
            _array(evaluation["failure_codes"], "evaluation.failure_codes")
        )
    ]
    if len(codes) != len(set(codes)) or codes != sorted(codes):
        raise ResultError("evaluation.failure_codes must be unique and sorted")
    if not set(codes) <= RUN_FAILURE_CODES:
        raise ResultError("evaluation.failure_codes contains an unknown code")
    if passed != (not codes):
        raise ResultError("evaluation.passed does not match failure_codes")
    return passed, codes


def _resource_failures(metrics: Mapping[str, object]) -> set[str]:
    cpu_count, cpu_missing, cpu_p95 = _metric(metrics, "generator_cpu_percent", "p95")
    rss_count, rss_missing, rss_p95 = _metric(metrics, "generator_rss_bytes", "p95")
    auxiliary_metrics = [
        _metric(metrics, "processing_cpu_percent", "p95"),
        _metric(metrics, "processing_rss_bytes", "p95"),
        _metric(metrics, "system_load_1m", "p95"),
        _metric(metrics, "device_temperature_c", "maximum"),
        _metric(metrics, "helper_cpu_percent", "p95"),
        _metric(metrics, "helper_rss_bytes", "p95"),
    ]
    _metric(metrics, "generator_cgroup_memory_current_bytes", "p95")
    _metric(metrics, "processing_cgroup_memory_current_bytes", "p95")
    _cgroup_throttling(metrics)
    failures: set[str] = set()
    if not _metric_complete(cpu_count, cpu_missing, cpu_p95, EXPECTED_RESOURCE_SAMPLES):
        failures.add("cpu_sample_count")
    elif cast(float, cpu_p95) > CPU_P95_LIMIT_PERCENT:
        failures.add("generator_cpu_p95")
    if not _metric_complete(rss_count, rss_missing, rss_p95, EXPECTED_RESOURCE_SAMPLES):
        failures.add("rss_sample_count")
    elif cast(float, rss_p95) > RSS_P95_LIMIT_BYTES:
        failures.add("generator_rss_p95")
    if any(
        not _metric_complete(count, missing, value, EXPECTED_RESOURCE_SAMPLES)
        for count, missing, value in auxiliary_metrics
    ):
        failures.add("auxiliary_resource_sample_count")
    return failures


def _latency_failures(metrics: Mapping[str, object]) -> set[str]:
    joint_count, joint_missing, joint_p99 = _metric(
        metrics, "joint_frame_completion_latency_ms", "p99"
    )
    sensors = _sensor_metrics(metrics)
    failures: set[str] = set()
    if not _metric_complete(joint_count, joint_missing, joint_p99, EXPECTED_LATENCY_SAMPLES):
        failures.add("joint_latency_sample_count")
    elif cast(float, joint_p99) > JOINT_LATENCY_P99_LIMIT_MS:
        failures.add("joint_latency_p99")
    if any(
        not _metric_complete(count, missing, p99, EXPECTED_LATENCY_SAMPLES)
        for count, missing, p99 in sensors.values()
    ):
        failures.add("sensor_latency_sample_count")
    return failures


def _processing_failures(metrics: Mapping[str, object]) -> set[str]:
    statuses = _processing_statuses(metrics)
    service_statuses = _service_statuses(metrics)
    measurement_count, gap_count, maximum_gap_s = _processing_freshness(metrics)
    age_count, age_missing, maximum_age_ms = _processing_delivery_age(metrics)
    failures: set[str] = set()
    if measurement_count == 0 or any(count != measurement_count for count, _ in statuses.values()):
        failures.add("processing_measurement_missing")
    if any(good != count for count, good in statuses.values()):
        failures.add("processing_not_good")
    if (
        measurement_count == 0
        or gap_count != measurement_count + 1
        or maximum_gap_s is None
        or maximum_gap_s > MAX_PROCESSING_FRESHNESS_GAP_S
    ):
        failures.add("processing_freshness")
    if (
        age_count != measurement_count
        or age_missing != 0
        or maximum_age_ms is None
        or maximum_age_ms > MAX_PROCESSING_DELIVERY_AGE_MS
    ):
        failures.add("processing_measurement_age")
    if any(count != EXPECTED_STATUS_SAMPLES for count, _ in service_statuses.values()):
        failures.add("service_status_sample_count")
    if any(healthy != count for count, healthy in service_statuses.values()):
        failures.add("service_status_not_healthy")
    return failures


def _event_failures(document: Mapping[str, object]) -> set[str]:
    events = _events(document)
    codes = {
        "producer_sequence_gaps": "producer_sequence_gap",
        "processing_frame_loss": "processing_frame_loss",
        "processing_local_loss": "processing_local_loss",
        "processing_sequence_regressions": "processing_sequence_regression",
        "scan_server_frame_loss": "scan_server_frame_loss",
        "scan_subscriber_missing_lanes": "scan_subscriber_missing",
        "sequence_duplicates_or_regressions": "sequence_duplicate_or_regression",
        "unclassified_loss_windows": "unclassified_loss_window",
        "container_restarts": "container_restart",
        "oom_events": "oom_event",
        "thermal_throttling_events": "thermal_throttling_event",
    }
    failures = {code for field, code in codes.items() if events[field] != 0}
    if not events["evidence_complete"]:
        failures.add("event_evidence_missing")
    return failures


def _observation_failures(document: Mapping[str, object], mode: str) -> set[str]:
    observation = _observation(document, mode)
    if not observation["evidence_complete"]:
        return {"observation_evidence_missing"}
    if mode == "actual":
        valid = (
            observation["sent_records"] == EXPECTED_OBSERVATION_RECORDS
            and observation["sent_records"] == observation["received_records"]
            and observation["dropped_records"] == 0
            and observation["connection_failures"] == 0
        )
        return set() if valid else {"observation_delivery"}
    return (
        {"observation_noop_activity"}
        if any(value for name, value in observation.items() if name != "evidence_complete")
        else set()
    )


def _scenario_failures(document: Mapping[str, object], duration: int) -> set[str]:
    scenario = _scenario(document)
    if not scenario["evidence_complete"]:
        return {"scenario_evidence_missing"}
    failures: set[str] = set()
    if (
        duration == 600
        and cast(int, scenario["completed_cycle_count"]) < MIN_ACCELERATED_COMPLETE_CYCLES
    ):
        failures.add("scenario_cycle_transition_missing")
    if duration == 86_400 and scenario["transition_count"] != 0:
        failures.add("scenario_unexpected_transition")
    expected_latency_samples = 2 * cast(int, scenario["transition_count"])
    if scenario["latency_sample_count"] != expected_latency_samples or (
        expected_latency_samples > 0 and scenario["maximum_latency_ms"] is None
    ):
        failures.add("scenario_transition_latency_sample_count")
    elif (
        scenario["maximum_latency_ms"] is not None
        and cast(float, scenario["maximum_latency_ms"]) > JOINT_LATENCY_P99_LIMIT_MS
    ):
        failures.add("scenario_transition_latency")
    return failures


def _semantic_component(
    semantics: Mapping[str, object], name: str, expected_fields: set[str]
) -> Mapping[str, object]:
    component = _mapping(semantics.get(name), f"semantics.{name}")
    _require_keys(component, expected_fields | {"evidence_complete", "passed"}, name)
    for field in ("evidence_complete", "passed"):
        if not isinstance(component[field], bool):
            raise ResultError(f"semantics.{name}.{field} must be a boolean")
    return component


def _simulator_semantic_passed(semantics: Mapping[str, object]) -> bool:
    component = _semantic_component(
        semantics,
        "simulator",
        {"sensor_scan_evidence"},
    )
    evidence = _array(
        component.get("sensor_scan_evidence"),
        "semantics.simulator.sensor_scan_evidence",
    )
    expected_fields = {
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
    ordered_ids: list[str] = []
    valid = component["evidence_complete"] is True
    for index, raw_sensor in enumerate(evidence):
        path = f"semantics.simulator.sensor_scan_evidence[{index}]"
        sensor = _mapping(raw_sensor, path)
        _require_keys(sensor, expected_fields, path)
        sensor_id = _string(sensor["sensor_id"], f"{path}.sensor_id")
        ordered_ids.append(sensor_id)
        counts = {
            field: _nonnegative_integer(sensor[field], f"{path}.{field}")
            for field in expected_fields - {"sensor_id"}
        }
        reference_total = (
            counts["no_hit_count"]
            + counts["floor_hit_count"]
            + counts["wall_hit_count"]
            + counts["surface_hit_count"]
        )
        measured_total = counts["measured_valid_count"] + counts["measured_invalid_count"]
        valid &= (
            counts["sampled_scan_count"] == EXPECTED_STATUS_SAMPLES
            and counts["reference_sample_count"] > 0
            and counts["reference_sample_count"] == reference_total
            and counts["reference_sample_count"] == measured_total
            and counts["no_hit_count"] > 0
            and counts["floor_hit_count"] + counts["wall_hit_count"] > 0
            and counts["surface_hit_count"] > 0
            and counts["measured_valid_count"] > 0
            and counts["measured_invalid_count"] > 0
            and counts["measured_without_reference_count"] == 0
            and counts["reference_hit_without_measurement_count"] == 0
            and 0 < counts["reference_change_count"] < counts["sampled_scan_count"]
        )
    return valid and tuple(ordered_ids) == EXPECTED_SENSOR_IDS


def _trend_direction_passed(segment_count: int, match_count: int) -> bool:
    return (
        segment_count > 0 and match_count <= segment_count and match_count * 5 >= segment_count * 4
    )


def _lidar_processing_semantic_passed(
    semantics: Mapping[str, object], metrics: Mapping[str, object], duration: int
) -> bool:
    count_fields = {
        "measurement_count",
        "complete_measurement_count",
        "missing_value_count",
        "height_range_violation_count",
        "height_order_violation_count",
        "fusion_range_violation_count",
        "quality_ratio_violation_count",
        "filling_segment_count",
        "filling_direction_match_count",
        "collecting_segment_count",
        "collecting_direction_match_count",
    }
    component = _semantic_component(semantics, "lidar_processing", count_fields)
    counts = {
        field: _nonnegative_integer(component[field], f"semantics.lidar_processing.{field}")
        for field in count_fields
    }
    statuses = _processing_statuses(metrics)
    measurement_count = counts["measurement_count"]
    filling_ok = _trend_direction_passed(
        counts["filling_segment_count"], counts["filling_direction_match_count"]
    )
    collecting_ok = duration != 600 or _trend_direction_passed(
        counts["collecting_segment_count"], counts["collecting_direction_match_count"]
    )
    return (
        component["evidence_complete"] is True
        and measurement_count > 0
        and statuses["fused"][0] == measurement_count
        and counts["complete_measurement_count"] == measurement_count
        and all(
            counts[field] == 0
            for field in (
                "missing_value_count",
                "height_range_violation_count",
                "height_order_violation_count",
                "fusion_range_violation_count",
                "quality_ratio_violation_count",
            )
        )
        and filling_ok
        and collecting_ok
    )


def _semantic_verdict(
    document: Mapping[str, object], metrics: Mapping[str, object], duration: int
) -> tuple[set[str], bool, bool, str]:
    semantics = _mapping(document.get("semantics"), "semantics")
    _require_keys(
        semantics,
        {"simulator", "lidar_processing", "failure_domain"},
        "semantics",
    )
    failure_domain = _string(semantics["failure_domain"], "semantics.failure_domain")
    if failure_domain not in {"none", "simulator", "lidar-processing", "indeterminate"}:
        raise ResultError("semantics.failure_domain is invalid")
    simulator_passed = _simulator_semantic_passed(semantics)
    processing_passed = _lidar_processing_semantic_passed(semantics, metrics, duration)
    failures: set[str] = set()
    if not simulator_passed:
        failures.add("simulator_semantics")
    if not processing_passed:
        failures.add("lidar_processing_semantics")
    if simulator_passed and processing_passed:
        domain = "none"
    elif simulator_passed:
        domain = "lidar-processing"
    elif processing_passed:
        domain = "simulator"
    else:
        domain = "indeterminate"
    return failures, simulator_passed, processing_passed, domain


def evaluate_run(document: Mapping[str, object]) -> dict[str, object]:
    """Recompute one run verdict and return a normalized aggregate document."""
    expected_keys = {
        "schema_version",
        "identity",
        "device",
        "workload",
        "metrics",
        "events",
        "observation",
        "scenario",
        "semantics",
        "evaluation",
    }
    _require_keys(document, expected_keys, "run result")
    if document.get("schema_version") != RUN_SCHEMA_VERSION:
        raise ResultError(f"schema_version must be {RUN_SCHEMA_VERSION}")
    _evaluation(document)
    _identity(document)
    _device(document)
    duration, mode = _workload(document)
    metrics = _mapping(document.get("metrics"), "metrics")
    _require_keys(
        metrics,
        {
            "generator_cpu_percent",
            "generator_rss_bytes",
            "processing_cpu_percent",
            "processing_rss_bytes",
            "generator_cgroup_memory_current_bytes",
            "processing_cgroup_memory_current_bytes",
            "system_load_1m",
            "device_temperature_c",
            "helper_cpu_percent",
            "helper_rss_bytes",
            "cgroup_throttling",
            "joint_frame_completion_latency_ms",
            "sensor_frame_completion_latency_ms",
            "processing_status",
            "processing_measurement_freshness_s",
            "processing_delivery_age_ms",
            "service_status",
        },
        "metrics",
    )
    failures = _resource_failures(metrics)
    failures.update(_latency_failures(metrics))
    failures.update(_processing_failures(metrics))
    failures.update(_event_failures(document))
    failures.update(_observation_failures(document, mode))
    failures.update(_scenario_failures(document, duration))
    semantic_failures, simulator_passed, processing_passed, failure_domain = _semantic_verdict(
        document, metrics, duration
    )
    failures.update(semantic_failures)

    normalized = copy.deepcopy(dict(document))
    normalized_semantics = cast(dict[str, object], normalized["semantics"])
    cast(dict[str, object], normalized_semantics["simulator"])["passed"] = simulator_passed
    cast(dict[str, object], normalized_semantics["lidar_processing"])["passed"] = processing_passed
    normalized_semantics["failure_domain"] = failure_domain
    normalized["evaluation"] = {
        "passed": not failures,
        "failure_codes": sorted(failures),
    }
    return normalized


def _run_key(document: Mapping[str, object]) -> tuple[int, str]:
    return _workload(document)


def _cpu_p95(document: Mapping[str, object]) -> float | None:
    metrics = _mapping(document.get("metrics"), "metrics")
    count, missing, value = _metric(metrics, "generator_cpu_percent", "p95")
    return value if _metric_complete(count, missing, value, EXPECTED_RESOURCE_SAMPLES) else None


def _compare_observation_cpu(
    duration: int,
    runs: Mapping[tuple[int, str], Mapping[str, object]],
) -> tuple[dict[str, object], set[str]]:
    actual = runs[(duration, "actual")]
    noop = runs[(duration, "noop")]
    failures: set[str] = set()
    identity_matches = _identity(actual) == _identity(noop) and _device(actual) == _device(noop)
    if not identity_matches:
        failures.add("comparison_identity_mismatch")
    actual_cpu = _cpu_p95(actual)
    noop_cpu = _cpu_p95(noop)
    schedule_matches = _scenario(actual)["schedule_sha256"] == _scenario(noop)["schedule_sha256"]
    if not schedule_matches:
        failures.add("scenario_schedule_mismatch")
    delta = None if actual_cpu is None or noop_cpu is None else actual_cpu - noop_cpu
    passed = (
        identity_matches
        and schedule_matches
        and delta is not None
        and delta <= OBSERVATION_CPU_DELTA_LIMIT_PERCENTAGE_POINTS
    )
    if delta is None:
        failures.add("observation_cpu_delta_unavailable")
    elif delta > OBSERVATION_CPU_DELTA_LIMIT_PERCENTAGE_POINTS:
        failures.add("observation_cpu_delta")
    return (
        {
            "mean_fill_duration_s": duration,
            "actual_cpu_p95_percent": actual_cpu,
            "noop_cpu_p95_percent": noop_cpu,
            "delta_percentage_points": delta,
            "scenario_schedule_matches": schedule_matches,
            "passed": passed,
        },
        failures,
    )


def evaluate_matrix(documents: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Evaluate the required four-run matrix and observation CPU deltas."""
    if len(documents) != len(EXPECTED_RUN_KEYS):
        raise ResultError("matrix requires exactly four run results")
    runs: dict[tuple[int, str], dict[str, object]] = {}
    for document in documents:
        normalized = evaluate_run(document)
        key = _run_key(normalized)
        if key in runs:
            raise ResultError(f"duplicate matrix run: {key}")
        runs[key] = normalized
    if set(runs) != EXPECTED_RUN_KEYS:
        raise ResultError("matrix does not contain the four required workload combinations")

    matrix_failures: set[str] = set()
    baseline = next(iter(runs.values()))
    if any(
        _identity(run) != _identity(baseline) or _device(run) != _device(baseline)
        for run in runs.values()
    ):
        matrix_failures.add("comparison_identity_mismatch")
    run_summaries: list[dict[str, object]] = []
    for key in sorted(runs):
        run = runs[key]
        passed, failure_codes = _evaluation(run)
        if not passed:
            matrix_failures.add("run_failed")
        run_summaries.append(
            {
                "mean_fill_duration_s": key[0],
                "observation_mode": key[1],
                "passed": passed,
                "failure_codes": failure_codes,
            }
        )

    comparisons: list[dict[str, object]] = []
    for duration in (600, 86_400):
        comparison, failures = _compare_observation_cpu(duration, runs)
        comparisons.append(comparison)
        matrix_failures.update(failures)

    return {
        "schema_version": MATRIX_SCHEMA_VERSION,
        "runs": run_summaries,
        "observation_cpu_comparisons": comparisons,
        "evaluation": {
            "passed": not matrix_failures,
            "failure_codes": sorted(matrix_failures),
        },
    }


def _read_document(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ResultError(f"cannot read result {path.name}: {error}") from error
    return _mapping(value, path.name)


def _write_document(path: Path | None, document: Mapping[str, object]) -> None:
    serialized = json.dumps(document, allow_nan=False, indent=2, sort_keys=True) + "\n"
    if path is None:
        sys.stdout.write(serialized)
        return
    with path.open("x", encoding="utf-8") as output:
        output.write(serialized)


def build_parser() -> argparse.ArgumentParser:
    """Build the aggregate-result evaluator CLI parser."""
    parser = argparse.ArgumentParser(
        description="Evaluate four aggregate edge long-validation run results."
    )
    parser.add_argument(
        "--run-result",
        action="append",
        required=True,
        type=Path,
        help="aggregate run-result.v1 file; provide exactly four times",
    )
    parser.add_argument("--output", type=Path, help="matrix-result.v1 output; default is stdout")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Evaluate a matrix, write its safe aggregate result and return its verdict."""
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.output is not None and arguments.output.resolve() in {
            path.resolve() for path in arguments.run_result
        }:
            raise ResultError("matrix output must not overwrite an input run result")
        result = evaluate_matrix([_read_document(path) for path in arguments.run_result])
        _write_document(arguments.output, result)
    except (OSError, ResultError) as error:
        print(f"long validation evaluation failed: {error}", file=sys.stderr)
        return 2
    evaluation = _mapping(result["evaluation"], "evaluation")
    return 0 if evaluation["passed"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
