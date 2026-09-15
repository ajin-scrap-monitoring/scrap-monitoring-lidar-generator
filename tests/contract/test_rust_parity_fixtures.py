"""Independent invariant and integrity checks for the Rust migration fixtures."""

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from tools.generate_rust_parity_fixtures import (
    FIXTURE_DIRECTORY,
    FLOAT_ABSOLUTE_TOLERANCE,
    FLOAT_RELATIVE_TOLERANCE,
    MAX_FIXTURE_BYTES,
    PYTHON_SEEDED_FLOAT_ABSOLUTE_TOLERANCE,
    _compare,
    _require_public_references,
    build_fixtures,
    check_fixtures,
)

from scrap_monitoring_lidar_simulator.configuration import load_generator_inputs
from scrap_monitoring_lidar_simulator.wire import lidar_pb2

_ROOT = Path(__file__).parents[2]


def _json(name: str) -> Any:
    return json.loads((FIXTURE_DIRECTORY / name).read_bytes())


@pytest.fixture(scope="module")
def regenerated() -> dict[str, bytes]:
    return build_fixtures()


def test_parity_fixtures_regenerate_from_current_public_inputs(
    regenerated: dict[str, bytes],
) -> None:
    check_fixtures(FIXTURE_DIRECTORY, regenerated)

    assert len(regenerated) == 9
    assert sum(map(len, regenerated.values())) <= MAX_FIXTURE_BYTES


def test_manifest_separates_scripted_parity_from_python_random_baseline() -> None:
    metadata = _json("metadata.json")
    classes = {name: details["comparison_class"] for name, details in metadata["fixtures"].items()}

    assert [name for name, kind in classes.items() if kind == "python-seeded-reference"] == [
        "scenario-seeded-baseline.json"
    ]
    assert sum(kind == "rng-independent" for kind in classes.values()) == 7
    assert metadata["comparison"]["hq_ticks_and_decoded_wire_values"] == "exact"
    assert "not a cross-language canonical form" in metadata["comparison"]["protobuf_hex"]
    assert metadata["comparison"]["float_absolute_tolerance"] == FLOAT_ABSOLUTE_TOLERANCE
    assert metadata["comparison"]["float_relative_tolerance"] == FLOAT_RELATIVE_TOLERANCE
    assert (
        metadata["comparison"]["python_seeded_float_absolute_tolerance"]
        == PYTHON_SEEDED_FLOAT_ABSOLUTE_TOLERANCE
    )
    for source, fingerprint in metadata["source_sha256"].items():
        assert not Path(source).is_absolute()
        assert ".." not in Path(source).parts
        assert "internal" not in Path(source).parts
        assert fingerprint == hashlib.sha256((_ROOT / source).read_bytes()).hexdigest()


def test_configuration_cases_capture_strict_integer_and_json_behavior() -> None:
    cases = {case["name"]: case["expected"] for case in _json("configuration-cases.json")["cases"]}

    assert cases["maximum-seed"] == {"accepted": True}
    assert cases["maximum-quality-frequency"] == {"accepted": True}
    assert cases["numeric-version"] == {"accepted": True}
    assert cases["large-noise"] == {"accepted": True}
    assert cases["maximum-diagnostics-limit"] == {"accepted": True}
    assert not cases["diagnostics-limit-overflow"]["accepted"]
    for name in ("boolean-seed", "fractional-seed", "seed-overflow", "boolean-version"):
        assert not cases[name]["accepted"]
    assert cases["duplicate-field"]["error"] == "duplicate JSON field: seed"
    assert cases["sampling-over-frame-limit"]["error"] == (
        "$.measurement.sample_rate_hz must produce at most 32768 points per rotation"
    )
    assert not cases["unknown-nested-field"]["accepted"]
    assert not cases["noncanonical-quality-key"]["accepted"]


@pytest.mark.parametrize("field", ["environment_path", "quality_profile_path"])
def test_fixture_generation_rejects_non_public_references(field: str) -> None:
    document = json.loads((_ROOT / "examples" / "generator.v2.json").read_bytes())
    document[field] = "../docs/internal/private.json"

    with pytest.raises(ValueError, match="Rust parity fixtures require examples"):
        _require_public_references(document)


def test_height_field_fixture_preserves_volume_and_grid_orientation() -> None:
    fixture = _json("height-field-operations.json")
    inputs = load_generator_inputs(_ROOT / "examples" / "generator.v2.json")
    floor = inputs.environment.floor_z_m
    top = inputs.environment.top_z_m
    boundary = inputs.environment.boundary_xy_m
    cell_size = inputs.generator.scenario.surface.cell_size_m
    expected_shape = (
        math.ceil((max(y for _, y in boundary) - min(y for _, y in boundary)) / cell_size) + 1,
        math.ceil((max(x for x, _ in boundary) - min(x for x, _ in boundary)) / cell_size) + 1,
    )
    weights = np.array(fixture["node_area_m2"])

    assert fixture["grid_order"] == "y-major"
    assert weights.shape == expected_shape
    assert np.sum(weights) == pytest.approx(fixture["surface_area_m2"], abs=1e-10)
    assert fixture["capacity_m3"] == pytest.approx(fixture["surface_area_m2"] * (top - floor))
    for operation, expected_ratio in zip(
        fixture["operations"], (0.3, 0.3, 0.3, 0.2, 0.0), strict=True
    ):
        state = operation["state"]
        heights = np.array(state["heights_m"])
        assert heights.shape == expected_shape
        assert np.all(np.isfinite(heights))
        assert np.all((heights >= floor) & (heights <= top))
        assert np.sum(weights * (heights - floor)) == pytest.approx(state["volume_m3"], abs=1e-10)
        assert state["fill_ratio"] == pytest.approx(expected_ratio, abs=1e-10)
    operations = fixture["operations"]
    assert operations[1]["state"]["heights_m"] != operations[2]["state"]["heights_m"]
    assert 1 <= operations[1]["result"]["iterations"] <= 32
    assert operations[-1]["change"]["applied_m3"] < operations[-1]["change"]["requested_m3"]


def test_scripted_scenario_captures_surface_and_phase_boundaries() -> None:
    fixture = _json("scenario-scripted.json")
    states = {entry["snapshot"]["elapsed_s"]: entry["snapshot"] for entry in fixture["states"]}

    assert states[0.49]["surface_volume_m3"] == 0.0
    assert states[0.5]["surface_volume_m3"] > 0.0
    assert states[0.51]["surface_volume_m3"] == states[0.5]["surface_volume_m3"]
    transitions = [
        state for state in states.values() if state["phase_started_at_s"] == state["elapsed_s"]
    ]
    assert [(state["phase"], state["cycle_index"]) for state in transitions] == [
        ("filling", 0),
        ("collecting", 0),
        ("filling", 1),
    ]
    assert transitions[1]["surface_fill_ratio"] == pytest.approx(
        transitions[1]["target_fill_ratio"]
    )
    assert transitions[2]["surface_volume_m3"] == pytest.approx(0.0, abs=1e-10)
    assert transitions[1]["elapsed_s"] % 0.5 != 0.0
    assert transitions[2]["elapsed_s"] % 0.5 != 0.0
    scans = fixture["within_scan_event"]["scans"]
    assert [scan["sensor_id"] for scan in scans] == ["lidar_1", "lidar_2"]
    for scan in scans:
        assert 0.5 in scan["point_elapsed_times_s"]
        assert scan["point_elapsed_times_s"][0] < 0.5 < scan["point_elapsed_times_s"][-1]
        assert scan["completed_at_s"] == 1.0
    event_samples = fixture["within_scan_event"]["event_samples"]
    assert [sample["sensor_id"] for sample in event_samples] == ["lidar_1", "lidar_2"]
    for sample in event_samples:
        assert (
            sample["angle_deg"]
            == fixture["within_scan_event"]["event_angles_deg"][sample["sensor_id"]]
        )
        assert sample["prior_hit_kind"] == "floor"
        assert sample["runtime_hit_kind"] == "surface"
        assert sample["prior_distance_m"] - sample["runtime_distance_m"] > 0.1


def test_rotation_fixture_retains_fractional_sample_counts_and_hq_ticks() -> None:
    fixture = _json("rotation-and-hq.json")
    fractional = next(case for case in fixture["rotations"] if case["sample_rate_hz"] == 5.0)
    scans = fractional["scans"]

    assert [len(scan["angles_deg"]) for scan in scans] == [3, 2, 3, 2]
    assert [time for scan in scans for time in scan["point_elapsed_times_s"]] == [
        index / 5.0 for index in range(10)
    ]
    assert fixture["angle_quantization"]["hq_ticks"][-1] == 0
    assert fixture["distance_quantization"]["hq_ticks"][-2] == 4938
    for kind in ("angle_quantization", "distance_quantization"):
        assert all(type(tick) is int for tick in fixture[kind]["hq_ticks"])
    initial = fixture["seeded_initial_angles_deg"]
    assert initial["lidar_1"] != initial["lidar_2"]


def test_spatial_fixtures_encode_half_open_events_and_nearest_candidates() -> None:
    fixture = _json("distortion-events.json")

    assert len(fixture["sensors"]) == 2
    for sensor in fixture["sensors"]:
        distance = sensor["reference"]["distances_m"][0]
        assert sensor["falling"]["distances_m"] == pytest.approx(
            [distance, distance - 0.5, distance - 0.75, distance, distance]
        )
        assert sensor["collection"]["distances_m"] == pytest.approx(
            [distance, distance, distance - 0.5, distance, distance]
        )
        assert sensor["voids"]["distances_m"] == pytest.approx(
            [distance, distance + 0.1, distance + 0.1, distance, distance]
        )
        assert sensor["falling"]["floor_hit_distances_m"] == [distance] * 5
        assert sensor["voids"]["blocked_distances_m"] == [distance] * 5
        assert sensor["dropout"]["active_mask"] == [False, True, False, True, False]


def test_scan_fixture_binary_matches_exact_wire_values_and_stable_sort() -> None:
    fixture = _json("scan-frames.json")
    records = fixture["records"]

    assert [record["frame"] for record in records[:2]] == [None, None]
    assert [record["frame"]["sequence"] for record in records[2:]] == [1, 1, 2, 2, 3, 3]
    for record in [*records[2:], fixture["stable_sort_case"]]:
        frame = lidar_pb2.ScanFrame.FromString(bytes.fromhex(record["protobuf_hex"]))
        expected = record["frame"]
        assert frame.SerializeToString(deterministic=True).hex() == record["protobuf_hex"]
        for field in frame.DESCRIPTOR.fields:
            if field.name != "samples":
                assert getattr(frame, field.name) == expected[field.name]
        assert [
            {
                "angle_mdeg": point.angle_mdeg,
                "distance_mm": point.distance_mm,
                "quality": point.quality,
            }
            for point in frame.samples
        ] == expected["samples"]
    samples = fixture["stable_sort_case"]["frame"]["samples"]
    assert samples[0] == {"angle_mdeg": 0, "distance_mm": 0, "quality": 6}
    assert samples[1]["angle_mdeg"] == samples[2]["angle_mdeg"]
    assert [sample["distance_mm"] for sample in samples[1:3]] == [2000, 3000]
    assert [sample["quality"] for sample in samples[1:3]] == [12, 20]
    assert samples[3]["distance_mm"] == 1234
    assert samples[3]["quality"] == 63


def test_seeded_reference_contains_both_sensors_and_complete_cycle() -> None:
    fixture = _json("scenario-seeded-baseline.json")
    records = fixture["records"]

    assert {record["snapshot"]["phase"] for record in records} == {"filling", "collecting"}
    assert {record["snapshot"]["cycle_index"] for record in records} == {0, 1}
    for record in records:
        assert [scan["reference"]["sensor_id"] for scan in record["scans"]] == [
            "lidar_1",
            "lidar_2",
        ]
        for scan in record["scans"]:
            assert len(scan["quality_bytes"]) == len(scan["distances_m"])
            assert set(scan["quality_bytes"]) <= {0, 24, 48, 80}
    spatial_events = fixture["spatial_events"]
    assert spatial_events["generated_through_s"] == 6.0
    assert len(spatial_events["falling_material"]) >= 1
    assert len(spatial_events["voids"]) >= 1
    assert len(spatial_events["collection_occlusion"]) >= 1
    for event_name in ("falling_material", "voids", "collection_occlusion"):
        assert all(
            event["started_at_s"] < event["ends_at_s"] for event in spatial_events[event_name]
        )


@pytest.mark.parametrize("tampered", ["data", "provenance", "missing", "oversized", "manifest"])
def test_fixture_check_rejects_stale_data_and_metadata(
    tmp_path: Path,
    regenerated: dict[str, bytes],
    tampered: str,
) -> None:
    for name, data in regenerated.items():
        if tampered == "missing" and name == "scan-frames.json":
            continue
        (tmp_path / name).write_bytes(data)
    if tampered == "data":
        (tmp_path / "scan-frames.json").write_text("{}\n")
    elif tampered == "provenance":
        metadata = json.loads(regenerated["metadata.json"])
        metadata["source_sha256"]["uv.lock"] = "0" * 64
        (tmp_path / "metadata.json").write_text(json.dumps(metadata))
    elif tampered == "oversized":
        (tmp_path / "scan-frames.json").write_bytes(b" " * MAX_FIXTURE_BYTES)
    elif tampered == "manifest":
        metadata = json.loads(regenerated["metadata.json"])
        metadata["fixtures"]["ghost.json"] = {
            "sha256": "0" * 64,
            "bytes": 0,
            "comparison_class": "rng-independent",
        }
        (tmp_path / "metadata.json").write_text(json.dumps(metadata))

    with pytest.raises(ValueError, match="Rust parity fixture"):
        check_fixtures(tmp_path, regenerated)


def test_parity_comparison_uses_tolerance_only_for_floats() -> None:
    _compare(1.0, 1.0 + FLOAT_ABSOLUTE_TOLERANCE / 2.0, "distance_m")
    with pytest.raises(ValueError, match="float changed"):
        _compare(1.0, 1.0 + FLOAT_ABSOLUTE_TOLERANCE * 2.0, "distance_m")
    with pytest.raises(ValueError, match="value changed"):
        _compare(1_800_000_000_000_000_000, 1_800_000_000_000_000_001, "timestamp_ns")
    with pytest.raises(ValueError, match="value type changed"):
        _compare(1, True, "sequence")


def test_python_seeded_comparison_allows_only_the_host_libm_tolerance() -> None:
    _compare(
        1.0,
        1.0 + PYTHON_SEEDED_FLOAT_ABSOLUTE_TOLERANCE / 2.0,
        "height_m",
        abs_tol=PYTHON_SEEDED_FLOAT_ABSOLUTE_TOLERANCE,
    )
    with pytest.raises(ValueError, match="float changed"):
        _compare(
            1.0,
            1.0 + PYTHON_SEEDED_FLOAT_ABSOLUTE_TOLERANCE * 2.0,
            "height_m",
            abs_tol=PYTHON_SEEDED_FLOAT_ABSOLUTE_TOLERANCE,
        )


def test_fixture_check_routes_float_tolerance_by_comparison_class(
    tmp_path: Path,
    regenerated: dict[str, bytes],
) -> None:
    def write_fixture_set(
        directory: Path,
        name: str,
        document: dict[str, Any],
    ) -> None:
        directory.mkdir()
        for fixture_name, data in regenerated.items():
            (directory / fixture_name).write_bytes(data)
        encoded = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
        (directory / name).write_bytes(encoded)
        metadata = json.loads(regenerated["metadata.json"])
        metadata["fixtures"][name]["sha256"] = hashlib.sha256(encoded).hexdigest()
        metadata["fixtures"][name]["bytes"] = len(encoded)
        (directory / "metadata.json").write_text(json.dumps(metadata))

    seeded_name = "scenario-seeded-baseline.json"
    seeded = json.loads(regenerated[seeded_name])
    seeded["records"][2]["inlet_heights_m"][0] += PYTHON_SEEDED_FLOAT_ABSOLUTE_TOLERANCE / 2.0
    seeded_directory = tmp_path / "seeded"
    write_fixture_set(seeded_directory, seeded_name, seeded)
    check_fixtures(seeded_directory, regenerated)

    independent_name = "scenario-scripted.json"
    independent = json.loads(regenerated[independent_name])
    independent["states"][0]["snapshot"]["phase_duration_s"] += FLOAT_ABSOLUTE_TOLERANCE * 2.0
    independent_directory = tmp_path / "independent"
    write_fixture_set(independent_directory, independent_name, independent)
    with pytest.raises(ValueError, match="float changed"):
        check_fixtures(independent_directory, regenerated)
