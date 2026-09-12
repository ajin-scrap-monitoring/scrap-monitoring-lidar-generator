"""Tests for bounded local reference diagnostics."""

import json
import stat
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from scrap_monitoring_lidar_generator.configuration import GeneratorInputs, load_generator_inputs
from scrap_monitoring_lidar_generator.runtime import (
    JsonLinesDiagnosticsWriter,
    build_diagnostics_writer,
    build_measurement_generation_runtime,
    generator_input_fingerprint,
)

_EXAMPLES = Path(__file__).parents[2] / "examples"
_RUN_ID = "run-a"
_RUN_STARTED_AT_UTC_US = 1_800_000_000_000_000


def _inputs(
    temporary_directory: Path,
    *,
    sample_scan_limit_per_sensor: int = 2,
) -> GeneratorInputs:
    inputs = load_generator_inputs(_EXAMPLES / "generator.v1.json")
    diagnostics = replace(
        inputs.generator.diagnostics,
        output_path=temporary_directory / "diagnostics",
        sample_scan_limit_per_sensor=sample_scan_limit_per_sensor,
    )
    return replace(inputs, generator=replace(inputs.generator, diagnostics=diagnostics))


def _documents(path: Path) -> list[dict[str, Any]]:
    return [
        cast(dict[str, Any], json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def _writer(inputs: GeneratorInputs) -> JsonLinesDiagnosticsWriter:
    return build_diagnostics_writer(
        inputs,
        run_id=_RUN_ID,
        run_started_at_utc_us=_RUN_STARTED_AT_UTC_US,
    )


def test_runtime_writes_bounded_reference_diagnostics_without_changing_results(
    tmp_path: Path,
) -> None:
    inputs = _inputs(tmp_path)
    writer = _writer(inputs)
    runtime = build_measurement_generation_runtime(inputs, diagnostics_sink=writer)
    plain_runtime = build_measurement_generation_runtime(inputs)

    with writer:
        recorded_results = [runtime.next_completed_scans()[0] for _ in range(3)]
    plain_results = [plain_runtime.next_completed_scans()[0] for _ in range(3)]

    output_path = writer.output_path
    assert output_path is not None
    assert output_path.name == "reference-scans.v2.0001.jsonl"
    assert stat.S_IMODE(output_path.stat().st_mode) == 0o600
    assert writer.recorded_counts == {"lidar_1": 2, "lidar_2": 2}
    documents = _documents(output_path)
    assert len(documents) == 4
    first_document = documents[0]
    first_result = recorded_results[0]
    assert first_document["diagnostics_version"] == 2
    assert first_document["environment_id"] == inputs.environment.environment_id
    assert first_document["input_fingerprint_sha256"] == generator_input_fingerprint(inputs)
    assert first_document["seed"] == inputs.generator.seed
    assert first_document["run_id"] == _RUN_ID
    assert first_document["run_started_at_utc_us"] == _RUN_STARTED_AT_UTC_US
    assert first_document["sensor_id"] == first_result.sensor_id
    assert first_document["scan_id"] == first_result.scan_id
    assert first_document["captured_at"] == _RUN_STARTED_AT_UTC_US
    assert first_document["captured_elapsed_s"] == first_result.reference.captured_elapsed_s
    assert first_document["completed_at_s"] == first_result.reference.completed_at_s
    assert first_document["scenario"]["elapsed_s"] == first_result.reference.completed_at_s
    assert first_document["surface"]["snapshot_at_s"] == first_result.reference.completed_at_s
    assert first_document["surface"]["shape"] == [23, 17]
    assert len(first_document["surface"]["heights_m"]) == 23
    assert len(first_document["reference_points"]) == len(first_result.reference.scan.points)
    assert "measured_points" not in first_document
    assert documents[1]["captured_at"] == _RUN_STARTED_AT_UTC_US
    assert documents[2]["captured_at"] == _RUN_STARTED_AT_UTC_US + 100_000

    for recorded, plain in zip(recorded_results, plain_results, strict=True):
        assert recorded.reference.scan == plain.reference.scan
        assert np.array_equal(
            recorded.measured.scan.distances_m,
            plain.measured.scan.distances_m,
        )
        assert np.array_equal(recorded.measured.scan.qualities, plain.measured.scan.qualities)


def test_writer_allocates_a_new_file_without_overwriting_an_existing_run(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path, sample_scan_limit_per_sensor=1)
    first_writer = _writer(inputs)
    first_runtime = build_measurement_generation_runtime(inputs, diagnostics_sink=first_writer)
    with first_writer:
        first_runtime.next_completed_scans()
    first_path = first_writer.output_path
    assert first_path is not None
    original_contents = first_path.read_bytes()

    second_writer = _writer(inputs)
    second_runtime = build_measurement_generation_runtime(inputs, diagnostics_sink=second_writer)
    with second_writer:
        second_runtime.next_completed_scans()

    assert second_writer.output_path == first_path.with_name("reference-scans.v2.0002.jsonl")
    assert first_path.read_bytes() == original_contents


def test_zero_sample_limit_does_not_create_a_diagnostics_file(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path, sample_scan_limit_per_sensor=0)
    writer = _writer(inputs)
    runtime = build_measurement_generation_runtime(inputs, diagnostics_sink=writer)

    with writer:
        runtime.next_completed_scans()

    assert writer.output_path is None
    assert not inputs.generator.diagnostics.output_path.exists()


def test_writer_rejects_unknown_sensor_stale_scenario_and_use_after_close(
    tmp_path: Path,
) -> None:
    inputs = _inputs(tmp_path)
    runtime = build_measurement_generation_runtime(inputs)
    first = runtime.next_completed_scans()[0]
    unknown_sensor_writer = JsonLinesDiagnosticsWriter(
        output_directory=tmp_path / "unknown",
        environment_id="environment-a",
        input_fingerprint="0" * 64,
        seed=1,
        run_id=_RUN_ID,
        run_started_at_utc_us=_RUN_STARTED_AT_UTC_US,
        sensor_ids=("sensor-b",),
        sample_scan_limit_per_sensor=1,
    )
    with pytest.raises(ValueError, match="not configured"):
        unknown_sensor_writer.record(first, runtime.scenario)

    runtime.next_completed_scans()
    writer = _writer(inputs)
    with pytest.raises(ValueError, match="scenario time"):
        writer.record(first, runtime.scenario)
    writer.close()
    with pytest.raises(RuntimeError, match="closed"):
        writer.record(first, runtime.scenario)
    with pytest.raises(RuntimeError, match="closed"):
        writer.__enter__()


def test_input_fingerprint_uses_generation_values_but_not_runtime_delivery_settings(
    tmp_path: Path,
) -> None:
    inputs = _inputs(tmp_path)
    relocated = replace(
        inputs,
        generator=replace(
            inputs.generator,
            environment_path=tmp_path / "relocated-environment.json",
            quality_profile_path=tmp_path / "relocated-quality.json",
            diagnostics=replace(
                inputs.generator.diagnostics,
                enabled=not inputs.generator.diagnostics.enabled,
                output_path=tmp_path / "elsewhere",
                sample_scan_limit_per_sensor=999,
            ),
            transport=replace(
                inputs.generator.transport,
                host="relocated-receiver",
                port=9_001,
                ack_timeout_s=4.0,
            ),
        ),
    )
    changed_seed = replace(
        inputs,
        generator=replace(inputs.generator, seed=inputs.generator.seed + 1),
    )
    changed_measurement = replace(
        inputs,
        generator=replace(
            inputs.generator,
            measurement=replace(
                inputs.generator.measurement,
                rotation_rate_hz=inputs.generator.measurement.rotation_rate_hz + 1,
            ),
        ),
    )

    fingerprint = generator_input_fingerprint(inputs)
    assert generator_input_fingerprint(relocated) == fingerprint
    assert generator_input_fingerprint(changed_seed) != fingerprint
    assert generator_input_fingerprint(changed_measurement) != fingerprint


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"environment_id": ""}, "environment_id"),
        ({"input_fingerprint": "A" * 64}, "fingerprint"),
        ({"seed": -1}, "unsigned 64-bit"),
        ({"seed": 18_446_744_073_709_551_616}, "unsigned 64-bit"),
        ({"run_id": ""}, "run_id"),
        ({"run_started_at_utc_us": -1}, "run start UTC"),
        ({"run_started_at_utc_us": True}, "run start UTC"),
        ({"sensor_ids": ()}, "identifiers"),
        ({"sensor_ids": ("sensor-a", "sensor-a")}, "unique"),
        ({"sample_scan_limit_per_sensor": -1}, "sample limit"),
    ],
)
def test_writer_rejects_invalid_settings(
    tmp_path: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    arguments: dict[str, object] = {
        "output_directory": tmp_path,
        "environment_id": "environment-a",
        "input_fingerprint": "0" * 64,
        "seed": 1,
        "run_id": _RUN_ID,
        "run_started_at_utc_us": _RUN_STARTED_AT_UTC_US,
        "sensor_ids": ("sensor-a",),
        "sample_scan_limit_per_sensor": 1,
    }
    arguments.update(overrides)

    with pytest.raises(ValueError, match=message):
        JsonLinesDiagnosticsWriter(**arguments)  # type: ignore[arg-type]
