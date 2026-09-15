"""Contract and distributable fixture validation tests."""

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator

from scrap_monitoring_lidar_generator.configuration import load_generator_inputs
from scrap_monitoring_lidar_generator.edge_integration import build_synthetic_processing_config
from scrap_monitoring_lidar_generator.observation import (
    decode_observation_header_line,
    decode_observation_line,
)
from scrap_monitoring_lidar_generator.wire import lidar_pb2

_ROOT = Path(__file__).parents[2]
_ENVIRONMENT_SCHEMA = _ROOT / "contracts" / "environment" / "v1" / "environment.schema.json"
_GENERATOR_SCHEMA = _ROOT / "contracts" / "v2" / "generator.schema.json"
_QUALITY_SCHEMA = _ROOT / "contracts" / "quality" / "v1" / "quality-profile.schema.json"
_OBSERVATION = _ROOT / "contracts" / "observation" / "v1"
_LIDAR = _ROOT / "contracts" / "lidar" / "v1"
_HANDOFF = _ROOT / "edge-platform-integration"


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_current_json_schemas_are_valid_draft_2020_12() -> None:
    for path in (_ENVIRONMENT_SCHEMA, _GENERATOR_SCHEMA, _QUALITY_SCHEMA):
        Draft202012Validator.check_schema(_json(path))


def test_public_examples_match_current_json_schemas() -> None:
    pairs = (
        (_ENVIRONMENT_SCHEMA, _ROOT / "examples" / "environment.v1.json"),
        (_GENERATOR_SCHEMA, _ROOT / "examples" / "generator.v2.json"),
        (_QUALITY_SCHEMA, _ROOT / "examples" / "quality-profile.v1.json"),
    )
    for schema_path, example_path in pairs:
        Draft202012Validator(_json(schema_path)).validate(_json(example_path))


def test_generator_schema_enforces_the_diagnostics_sample_limit() -> None:
    validator = Draft202012Validator(_json(_GENERATOR_SCHEMA))
    document = _json(_ROOT / "examples" / "generator.v2.json")
    document["diagnostics"]["sample_scan_limit_per_sensor"] = 16
    assert validator.is_valid(document)
    document["diagnostics"]["sample_scan_limit_per_sensor"] = 17
    assert not validator.is_valid(document)


def test_observation_fixture_matches_contract() -> None:
    header_schema = _json(_OBSERVATION / "header.schema.json")
    observation_schema = _json(_OBSERVATION / "observation.schema.json")
    lines = (_OBSERVATION / "fixtures" / "observation.v1.jsonl").read_text().splitlines()

    assert len(lines) == 2
    Draft202012Validator(header_schema).validate(json.loads(lines[0]))
    Draft202012Validator(observation_schema).validate(json.loads(lines[1]))
    header = decode_observation_header_line(lines[0])
    record = decode_observation_line(lines[1])
    assert header.run_id == record.run_id
    assert header.scene.sensors[0].sensor_id == "sensor-a"
    assert record.sequence == 1


def test_pinned_proto_and_handoff_copy_match_source_metadata() -> None:
    source = _json(_LIDAR / "upstream.json")
    proto = (_LIDAR / "lidar.proto").read_bytes()

    assert source["commit"] == _json(_HANDOFF / "SOURCE.json")["commit"]
    assert hashlib.sha256(proto).hexdigest() == source["sha256"]
    assert (_HANDOFF / "v1" / "lidar.proto").read_bytes() == proto
    assert lidar_pb2.DESCRIPTOR.package == "ajin.edge.lidar.v1"
    assert set(lidar_pb2.DESCRIPTOR.services_by_name) == {"LidarScanSource"}
    assert [
        method.name for method in lidar_pb2.DESCRIPTOR.services_by_name["LidarScanSource"].methods
    ] == ["SubscribeScans"]
    assert set(lidar_pb2.ScanFrame.DESCRIPTOR.fields_by_name) == {
        "schema_version",
        "edge_id",
        "sensor_id",
        "sequence",
        "acquired_at_unix_ms",
        "acquired_monotonic_ns",
        "sdk_status",
        "scan_hz",
        "samples",
        "instance_id",
        "config_revision",
    }


def test_handoff_processing_fixture_is_current_exporter_output() -> None:
    expected = build_synthetic_processing_config(
        load_generator_inputs(_ROOT / "examples" / "generator.v2.json"),
        socket_directory=PurePosixPath("/sockets"),
        site_id="synthetic-site",
        edge_id="synthetic-edge",
        config_revision="synthetic-r1",
    )

    assert _json(_HANDOFF / "v1" / "processing.synthetic.json") == expected


def test_handoff_contains_only_current_integration_artifacts() -> None:
    assert {
        path.relative_to(_HANDOFF).as_posix() for path in _HANDOFF.rglob("*") if path.is_file()
    } == {
        "README.md",
        "SOURCE.json",
        "v1/lidar.proto",
        "v1/processing.synthetic.json",
    }
