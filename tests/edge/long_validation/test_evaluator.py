"""Tests for exact aggregate edge validation decisions."""

import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest

from .evaluator import ResultError, evaluate_matrix, evaluate_run, main, nearest_rank
from .fixtures import passing_run


def _section(document: dict[str, object], name: str) -> dict[str, Any]:
    return cast(dict[str, Any], document[name])


def test_nearest_rank_uses_one_based_ceiling_without_interpolation() -> None:
    values = [float(value) for value in range(1, 101)]

    assert nearest_rank(values, 95) == 95.0
    assert nearest_rank(values, 99) == 99.0
    assert nearest_rank([20.0, 1.0, 10.0], 95) == 20.0
    assert nearest_rank([20.0, 1.0, 10.0], 1) == 1.0


@pytest.mark.parametrize(
    "values,percentile", [([], 95), ([1.0], 0), ([1.0], 101), ([float("nan")], 95)]
)
def test_nearest_rank_rejects_invalid_inputs(values: list[float], percentile: int) -> None:
    with pytest.raises(ValueError):
        nearest_rank(values, percentile)


def test_complete_run_passes_at_inclusive_limits() -> None:
    run = passing_run(86_400, "actual", cpu_p95=75.0)
    run["metrics"]["generator_rss_bytes"]["p95"] = 128 * 1_048_576
    run["metrics"]["joint_frame_completion_latency_ms"]["p99"] = 70.0
    run["metrics"]["processing_delivery_age_ms"]["maximum"] = 2_000.0

    result = evaluate_run(run)

    assert result["evaluation"] == {"passed": True, "failure_codes": []}


def test_complete_run_allows_unavailable_cgroup_memory_diagnostics() -> None:
    run = passing_run(86_400, "actual")
    unavailable = {
        "sample_count": 0,
        "missing_count": 3_600,
        "p95": None,
    }
    run["metrics"]["generator_cgroup_memory_current_bytes"] = unavailable.copy()
    run["metrics"]["processing_cgroup_memory_current_bytes"] = unavailable.copy()

    assert evaluate_run(run)["evaluation"] == {"passed": True, "failure_codes": []}


def test_accelerated_run_requires_five_complete_cycles() -> None:
    run = passing_run(600, "actual")
    run["scenario"].update(
        {
            "final_phase": "filling",
            "final_cycle_index": 4,
            "transition_count": 8,
            "filling_to_collecting_count": 4,
            "collecting_to_filling_count": 4,
            "transition_latency_sample_count": 16,
        }
    )

    assert _section(evaluate_run(run), "evaluation")["failure_codes"] == [
        "scenario_cycle_transition_missing"
    ]


def test_default_duration_requires_no_scenario_transition() -> None:
    run = passing_run(86_400, "actual")

    assert run["scenario"]["transition_count"] == 0
    assert evaluate_run(run)["evaluation"] == {"passed": True, "failure_codes": []}

    run["scenario"].update(
        {
            "final_phase": "collecting",
            "final_cycle_index": 0,
            "transition_count": 1,
            "filling_to_collecting_count": 1,
            "collecting_to_filling_count": 0,
            "transition_latency_sample_count": 2,
            "maximum_transition_latency_ms": 60.0,
        }
    )

    assert _section(evaluate_run(run), "evaluation")["failure_codes"] == [
        "scenario_unexpected_transition"
    ]


def test_transition_adjacent_latency_requires_two_bounded_samples_per_transition() -> None:
    run = passing_run(600, "actual")
    run["scenario"]["transition_latency_sample_count"] = 3

    assert _section(evaluate_run(run), "evaluation")["failure_codes"] == [
        "scenario_transition_latency_sample_count"
    ]

    run = passing_run(600, "actual")
    run["scenario"]["maximum_transition_latency_ms"] = 70.0001

    assert _section(evaluate_run(run), "evaluation")["failure_codes"] == [
        "scenario_transition_latency"
    ]


@pytest.mark.parametrize(
    "mutation,code",
    [
        (("metrics", "generator_cpu_percent", "p95", 75.0001), "generator_cpu_p95"),
        (("metrics", "generator_rss_bytes", "p95", 128 * 1_048_576 + 1), "generator_rss_p95"),
        (("metrics", "joint_frame_completion_latency_ms", "p99", 70.0001), "joint_latency_p99"),
        (("events", "processing_frame_loss", None, 1), "processing_frame_loss"),
        (("events", "processing_local_loss", None, 1), "processing_local_loss"),
        (
            ("events", "processing_sequence_regressions", None, 1),
            "processing_sequence_regression",
        ),
        (("events", "scan_server_frame_loss", None, 1), "scan_server_frame_loss"),
        (("events", "scan_subscriber_missing_lanes", None, 1), "scan_subscriber_missing"),
        (("events", "container_restarts", None, 1), "container_restart"),
        (("events", "oom_events", None, 1), "oom_event"),
        (("events", "thermal_throttling_events", None, 1), "thermal_throttling_event"),
    ],
)
def test_run_threshold_and_zero_event_failures(
    mutation: tuple[str, str, str | None, float], code: str
) -> None:
    run = passing_run(86_400, "actual")
    first, second, third, value = mutation
    target = run[first][second]
    if third is None:
        run[first][second] = value
    else:
        target[third] = value

    result = evaluate_run(run)

    assert code in _section(result, "evaluation")["failure_codes"]


def test_missing_samples_are_not_zero_filled_or_accepted() -> None:
    run = passing_run(600, "actual")
    run["metrics"]["generator_cpu_percent"] = {
        "sample_count": 3_599,
        "missing_count": 1,
        "p95": 0.0,
    }
    run["metrics"]["sensor_frame_completion_latency_ms"][0] = {
        "sensor_id": "lidar_1",
        "sample_count": 35_999,
        "missing_count": 1,
        "p99": 0.0,
    }
    run["metrics"]["service_status"][1]["sample_count"] = 3_599
    run["metrics"]["service_status"][1]["healthy_count"] = 3_599

    result = evaluate_run(run)

    assert result["evaluation"] == {
        "passed": False,
        "failure_codes": [
            "cpu_sample_count",
            "sensor_latency_sample_count",
            "service_status_sample_count",
        ],
    }


def test_every_sensor_and_fused_processing_measurement_must_be_good() -> None:
    run = passing_run(600, "actual")
    run["metrics"]["processing_status"][1]["good_count"] = 3_599

    assert _section(evaluate_run(run), "evaluation")["failure_codes"] == ["processing_not_good"]


def test_processing_measurement_count_is_not_required_to_equal_3600() -> None:
    run = passing_run(600, "actual")
    for status in run["metrics"]["processing_status"]:
        status["sample_count"] = 3_123
        status["good_count"] = 3_123
    run["metrics"]["processing_measurement_freshness_s"] = {
        "measurement_count": 3_123,
        "gap_count": 3_124,
        "maximum_gap_s": 1.4,
    }
    run["metrics"]["processing_delivery_age_ms"]["sample_count"] = 3_123
    run["semantics"]["lidar_processing"]["measurement_count"] = 3_123
    run["semantics"]["lidar_processing"]["complete_measurement_count"] = 3_123

    assert evaluate_run(run)["evaluation"] == {"passed": True, "failure_codes": []}


def test_semantic_failure_domain_distinguishes_simulator_and_lidar_processing() -> None:
    simulator_failure = passing_run(600, "actual")
    evidence = simulator_failure["semantics"]["simulator"]["sensor_scan_evidence"][0]
    evidence["surface_hit_count"] = 0
    evidence["no_hit_count"] += 360_000

    simulator_result = evaluate_run(simulator_failure)

    assert _section(simulator_result, "semantics")["failure_domain"] == "simulator"
    assert _section(simulator_result, "evaluation")["failure_codes"] == ["simulator_semantics"]

    processing_failure = passing_run(600, "actual")
    processing_failure["semantics"]["lidar_processing"]["fusion_range_violation_count"] = 1

    processing_result = evaluate_run(processing_failure)

    assert _section(processing_result, "semantics")["failure_domain"] == "lidar-processing"
    assert _section(processing_result, "evaluation")["failure_codes"] == [
        "lidar_processing_semantics"
    ]


def test_two_semantic_failures_are_reported_as_indeterminate() -> None:
    run = passing_run(600, "actual")
    run["semantics"]["simulator"]["evidence_complete"] = False
    run["semantics"]["lidar_processing"]["height_order_violation_count"] = 1

    result = evaluate_run(run)

    assert _section(result, "semantics")["failure_domain"] == "indeterminate"
    assert _section(result, "evaluation")["failure_codes"] == [
        "lidar_processing_semantics",
        "simulator_semantics",
    ]


def test_accelerated_processing_semantics_require_both_phase_directions() -> None:
    run = passing_run(600, "actual")
    run["semantics"]["lidar_processing"]["collecting_direction_match_count"] = 3

    result = evaluate_run(run)

    assert _section(result, "semantics")["failure_domain"] == "lidar-processing"
    assert _section(result, "evaluation")["failure_codes"] == ["lidar_processing_semantics"]


def test_processing_measurements_require_two_second_freshness() -> None:
    run = passing_run(600, "actual")
    run["metrics"]["processing_measurement_freshness_s"]["maximum_gap_s"] = 2.0001

    assert _section(evaluate_run(run), "evaluation")["failure_codes"] == ["processing_freshness"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("sample_count", 3_599),
        ("missing_count", 1),
        ("maximum", 2_000.0001),
        ("maximum", None),
    ],
)
def test_processing_measurements_require_complete_bounded_delivery_age(
    field: str, value: int | float | None
) -> None:
    run = passing_run(600, "actual")
    run["metrics"]["processing_delivery_age_ms"][field] = value

    assert _section(evaluate_run(run), "evaluation")["failure_codes"] == [
        "processing_measurement_age"
    ]


@pytest.mark.parametrize(
    "field",
    [
        "sensor_frame_completion_latency_ms",
        "processing_status",
        "service_status",
        "cgroup_throttling",
    ],
)
def test_run_evaluator_enforces_schema_array_order(field: str) -> None:
    run = passing_run(600, "actual")
    run["metrics"][field].reverse()

    with pytest.raises(ResultError, match="ordered"):
        evaluate_run(run)


def test_missing_event_and_observation_evidence_fails() -> None:
    run = passing_run(600, "actual")
    run["events"]["evidence_complete"] = False
    run["observation"]["evidence_complete"] = False
    run["scenario"]["evidence_complete"] = False

    result = evaluate_run(run)

    assert _section(result, "evaluation")["failure_codes"] == [
        "event_evidence_missing",
        "observation_evidence_missing",
        "scenario_evidence_missing",
    ]


def test_actual_observation_requires_clean_end_to_end_delivery() -> None:
    run = passing_run(600, "actual")
    run["observation"]["received_records"] -= 1

    assert _section(evaluate_run(run), "evaluation")["failure_codes"] == ["observation_delivery"]


def test_noop_observation_requires_all_counters_to_remain_zero() -> None:
    run = passing_run(600, "noop")
    run["observation"]["sent_records"] = 1

    assert _section(evaluate_run(run), "evaluation")["failure_codes"] == [
        "observation_noop_activity"
    ]


def test_matrix_passes_with_exact_four_combinations_and_five_point_deltas() -> None:
    documents = [
        passing_run(86_400, "noop", cpu_p95=60.0),
        passing_run(600, "actual", cpu_p95=65.0),
        passing_run(86_400, "actual", cpu_p95=65.0),
        passing_run(600, "noop", cpu_p95=60.0),
    ]

    result = evaluate_matrix(documents)

    assert result["evaluation"] == {"passed": True, "failure_codes": []}
    comparisons = cast(list[dict[str, object]], result["observation_cpu_comparisons"])
    assert [item["delta_percentage_points"] for item in comparisons] == [
        5.0,
        5.0,
    ]
    assert all(item["scenario_schedule_matches"] is True for item in comparisons)


def test_matrix_requires_matching_actual_and_noop_scenario_schedules() -> None:
    documents = [
        passing_run(duration, mode) for duration in (600, 86_400) for mode in ("actual", "noop")
    ]
    documents[0]["scenario"]["schedule_sha256"] = "9" * 64

    result = evaluate_matrix(documents)

    assert result["evaluation"] == {
        "passed": False,
        "failure_codes": ["scenario_schedule_mismatch"],
    }
    comparisons = cast(list[dict[str, object]], result["observation_cpu_comparisons"])
    assert comparisons[0]["scenario_schedule_matches"] is False


def test_matrix_rejects_duplicate_combination() -> None:
    documents = [
        passing_run(600, "actual"),
        passing_run(600, "actual"),
        passing_run(600, "noop"),
        passing_run(86_400, "noop"),
    ]

    with pytest.raises(ResultError, match="duplicate matrix run"):
        evaluate_matrix(documents)


def test_matrix_fails_for_delta_and_pair_identity_mismatch() -> None:
    documents = [
        passing_run(600, "actual", cpu_p95=66.0),
        passing_run(600, "noop", cpu_p95=60.0),
        passing_run(86_400, "actual"),
        passing_run(86_400, "noop"),
    ]
    documents[0]["identity"]["seed"] += 1

    result = evaluate_matrix(documents)

    assert result["evaluation"] == {
        "passed": False,
        "failure_codes": ["comparison_identity_mismatch", "observation_cpu_delta"],
    }
    comparisons = cast(list[dict[str, object]], result["observation_cpu_comparisons"])
    assert comparisons[0]["passed"] is False


def test_matrix_requires_identical_device_for_actual_noop_pair() -> None:
    documents = [
        passing_run(duration, mode) for duration in (600, 86_400) for mode in ("actual", "noop")
    ]
    documents[0]["device"]["cpu_governor"] = "performance"

    result = evaluate_matrix(documents)

    assert result["evaluation"] == {
        "passed": False,
        "failure_codes": ["comparison_identity_mismatch"],
    }


def test_matrix_requires_one_identity_and_device_across_both_durations() -> None:
    documents = [
        passing_run(duration, mode) for duration in (600, 86_400) for mode in ("actual", "noop")
    ]
    for document in documents:
        if document["workload"]["mean_fill_duration_s"] == 86_400:
            document["identity"]["generator_image_digest"] = f"sha256:{'9' * 64}"

    result = evaluate_matrix(documents)

    assert result["evaluation"] == {
        "passed": False,
        "failure_codes": ["comparison_identity_mismatch"],
    }


def test_matrix_marks_unavailable_cpu_delta_as_failed_result() -> None:
    documents = [
        passing_run(duration, mode) for duration in (600, 86_400) for mode in ("actual", "noop")
    ]
    documents[0]["metrics"]["generator_cpu_percent"] = {
        "sample_count": 0,
        "missing_count": 3_600,
        "p95": None,
    }

    result = evaluate_matrix(documents)

    assert result["evaluation"] == {
        "passed": False,
        "failure_codes": ["observation_cpu_delta_unavailable", "run_failed"],
    }
    comparisons = cast(list[dict[str, object]], result["observation_cpu_comparisons"])
    assert comparisons[0]["delta_percentage_points"] is None


def test_matrix_does_not_compare_cpu_percentile_from_incomplete_series() -> None:
    documents = [
        passing_run(duration, mode) for duration in (600, 86_400) for mode in ("actual", "noop")
    ]
    documents[0]["metrics"]["generator_cpu_percent"] = {
        "sample_count": 3_599,
        "missing_count": 1,
        "p95": 60.0,
    }

    result = evaluate_matrix(documents)

    assert result["evaluation"] == {
        "passed": False,
        "failure_codes": ["observation_cpu_delta_unavailable", "run_failed"],
    }


def test_matrix_cli_writes_result_and_returns_verdict(tmp_path: Path) -> None:
    paths = []
    for duration in (600, 86_400):
        for mode in ("actual", "noop"):
            path = tmp_path / f"{duration}-{mode}.json"
            path.write_text(json.dumps(passing_run(duration, mode)), encoding="utf-8")
            paths.append(path)
    output = tmp_path / "matrix.json"
    arguments = [item for path in paths for item in ("--run-result", str(path))]

    assert main([*arguments, "--output", str(output)]) == 0
    assert json.loads(output.read_text())["schema_version"] == "matrix-result.v1"


def test_matrix_cli_returns_contract_error_without_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "invalid.json"
    path.write_text("{}", encoding="utf-8")

    assert main(["--run-result", str(path)] * 4) == 2
    assert "long validation evaluation failed" in capsys.readouterr().err


def test_matrix_cli_returns_one_for_threshold_failure(tmp_path: Path) -> None:
    paths = []
    for duration in (600, 86_400):
        for mode in ("actual", "noop"):
            cpu_p95 = 76.0 if (duration, mode) == (600, "actual") else 60.0
            path = tmp_path / f"{duration}-{mode}.json"
            path.write_text(
                json.dumps(passing_run(duration, mode, cpu_p95=cpu_p95)),
                encoding="utf-8",
            )
            paths.append(path)
    arguments = [item for path in paths for item in ("--run-result", str(path))]

    assert main(arguments) == 1


def test_matrix_cli_does_not_overwrite_an_input_result(tmp_path: Path) -> None:
    paths = []
    for duration in (600, 86_400):
        for mode in ("actual", "noop"):
            path = tmp_path / f"{duration}-{mode}.json"
            path.write_text(json.dumps(passing_run(duration, mode)), encoding="utf-8")
            paths.append(path)
    before = paths[0].read_bytes()
    arguments = [item for path in paths for item in ("--run-result", str(path))]

    assert main([*arguments, "--output", str(paths[0])]) == 2
    assert paths[0].read_bytes() == before


def test_evaluation_does_not_mutate_input() -> None:
    run = passing_run(600, "actual")
    original = copy.deepcopy(run)
    run["evaluation"] = {"passed": False, "failure_codes": ["oom_event"]}

    assert _section(evaluate_run(run), "evaluation")["passed"] is True
    assert run != original
    assert run["evaluation"] == {"passed": False, "failure_codes": ["oom_event"]}
