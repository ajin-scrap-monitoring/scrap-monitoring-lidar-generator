"""JSON Schema tests for public-safe aggregate results."""

import copy
import json
from pathlib import Path
from typing import Any, cast

import jsonschema
import pytest

from .evaluator import MATRIX_FAILURE_CODES, RUN_FAILURE_CODES, evaluate_matrix
from .fixtures import passing_run

_SCHEMA_DIRECTORY = Path(__file__).with_name("schemas")


def _schema(name: str) -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads((_SCHEMA_DIRECTORY / name).read_text(encoding="utf-8")),
    )


def test_run_result_schema_accepts_complete_aggregate() -> None:
    jsonschema.Draft202012Validator(_schema("run-result.v1.schema.json")).validate(
        passing_run(86_400, "actual")
    )


def test_matrix_result_schema_accepts_evaluator_output() -> None:
    result = evaluate_matrix(
        [passing_run(duration, mode) for duration in (600, 86_400) for mode in ("actual", "noop")]
    )

    jsonschema.Draft202012Validator(_schema("matrix-result.v1.schema.json")).validate(result)


def test_schema_failure_codes_match_evaluator_contract() -> None:
    run_schema = cast(dict[str, Any], _schema("run-result.v1.schema.json"))
    matrix_schema = cast(dict[str, Any], _schema("matrix-result.v1.schema.json"))

    assert (
        set(run_schema["$defs"]["evaluation"]["properties"]["failure_codes"]["items"]["enum"])
        == RUN_FAILURE_CODES
    )
    assert (
        set(matrix_schema["$defs"]["evaluation"]["properties"]["failure_codes"]["items"]["enum"])
        == MATRIX_FAILURE_CODES
    )


@pytest.mark.parametrize("field", ["endpoint", "host", "socket_path", "raw_measurements"])
def test_run_schema_rejects_private_or_raw_top_level_fields(field: str) -> None:
    document = passing_run(600, "actual")
    document[field] = "private-value"

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(_schema("run-result.v1.schema.json")).validate(document)


def test_run_schema_rejects_raw_latency_samples() -> None:
    document = copy.deepcopy(passing_run(600, "actual"))
    document["metrics"]["joint_frame_completion_latency_ms"]["samples"] = [1.0]

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(_schema("run-result.v1.schema.json")).validate(document)


def test_run_schema_bounds_processing_delivery_age_samples() -> None:
    document = copy.deepcopy(passing_run(600, "actual"))
    document["metrics"]["processing_delivery_age_ms"]["sample_count"] = 7_201

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(_schema("run-result.v1.schema.json")).validate(document)


@pytest.mark.parametrize("field", ["surface", "heights", "transitions"])
def test_run_schema_rejects_raw_scenario_payloads(field: str) -> None:
    document = copy.deepcopy(passing_run(600, "actual"))
    document["scenario"][field] = []

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(_schema("run-result.v1.schema.json")).validate(document)


def test_run_schema_rejects_invalid_scenario_schedule_digest() -> None:
    document = copy.deepcopy(passing_run(600, "actual"))
    document["scenario"]["schedule_sha256"] = "not-a-sha256"

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(_schema("run-result.v1.schema.json")).validate(document)
