"""Tests for bounded live collection helpers."""

import argparse
import asyncio
import json
import stat
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import grpc
import pytest

from . import helper
from .fixtures import passing_run
from .helper import (
    CgroupSnapshot,
    CollectionError,
    RunCollector,
    fingerprint_files,
    read_cgroup_snapshot,
)


def _device() -> dict[str, object]:
    return {
        "model": "Raspberry Pi 5 Model B Rev 1.0",
        "architecture": "aarch64",
        "os_release": "Ubuntu 24.04.3 LTS",
        "kernel": "6.8.0-test",
        "docker_version": "28.4.0",
        "logical_cpu_count": 4,
        "effective_cpu_count": 4,
        "memory_bytes": 8 * 1_073_741_824,
        "cpu_governor": "ondemand",
        "cooling": "active cooler",
        "runtime_constraints": [
            {
                "component": component,
                "effective_cpu_count": 4,
                "cpu_quota_cores": None,
                "memory_limit_bytes": None,
            }
            for component in ("generator", "processing", "helper")
        ],
    }


def _collector(mode: str = "actual") -> RunCollector:
    return RunCollector(
        generator_source_commit="1" * 40,
        generator_image_digest=f"sha256:{'2' * 64}",
        processing_source_commit="3" * 40,
        processing_image_digest=f"sha256:{'4' * 64}",
        config_fingerprint_sha256="5" * 64,
        processing_config_sha256="6" * 64,
        seed=9,
        mean_fill_duration_s=600,
        observation_mode=mode,
        device=_device(),
    )


def _measurement(identity: str = "measurement-a", sequence: int = 1) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "measurement_id": identity,
        "measurement_cycle_id": f"cycle-{identity}",
        "site_id": "synthetic-site",
        "edge_id": "synthetic-edge",
        "measured_at": "2026-09-14T12:00:00+00:00",
        "calibration_version": "synthetic-v1",
        "config_revision": "synthetic-v1",
        "fill_ratio": 0.5,
        "fill_percent": 50.0,
        "sensors": [
            {
                "sensor_id": "lidar_1",
                "sequence": sequence,
                "valid_sample_ratio": 1.0,
                "coverage_ratio": 1.0,
                "section_fill_ratio": 0.48,
                "median_height_mm": 4_500.0,
                "p90_height_mm": 6_000.0,
                "state": "GOOD",
            },
            {
                "sensor_id": "lidar_2",
                "sequence": sequence,
                "valid_sample_ratio": 1.0,
                "coverage_ratio": 1.0,
                "section_fill_ratio": 0.52,
                "median_height_mm": 4_600.0,
                "p90_height_mm": 6_100.0,
                "state": "GOOD",
            },
        ],
        "quality": {"state": "GOOD", "confidence": 1.0, "reason_codes": []},
    }


def _test_measurement_validator(value: object) -> bytes:
    measurement = helper._mapping(value, "measurement")
    required = {
        "schema_version",
        "measurement_id",
        "measurement_cycle_id",
        "site_id",
        "edge_id",
        "measured_at",
        "calibration_version",
        "config_revision",
        "fill_ratio",
        "fill_percent",
        "sensors",
        "quality",
    }
    if not required.issubset(measurement):
        raise ValueError("invalid measurement envelope")
    return json.dumps(
        measurement,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _telemetry(mode: str = "actual", *, expected_sample_count: int = 36_000) -> dict[str, object]:
    started_ns = 400_000_000_000
    ended_ns = started_ns + 3_600_000_000_000
    deadlines = [started_ns + 100_000_000, started_ns + 200_000_000, ended_ns]
    sensor_latencies = ([1_000_000, 2_000_000, 3_000_000], [4_000_000, 5_000_000, 6_000_000])
    sensors = []
    for sensor_id, latencies in zip(("lidar_1", "lidar_2"), sensor_latencies, strict=True):
        samples = [
            {
                "sequence": 10 + index,
                "deadline_monotonic_ns": deadline,
                "published_monotonic_ns": deadline + latency,
                "latency_ns": latency,
            }
            for index, (deadline, latency) in enumerate(zip(deadlines, latencies, strict=True))
        ]
        sensors.append(
            {
                "sensor_id": sensor_id,
                "sample_count": len(samples),
                "missing_sample_count": 0,
                "first_sequence": 10,
                "last_sequence": 12,
                "samples": samples,
            }
        )
    batch_samples = [
        {
            "deadline_monotonic_ns": deadline,
            "published_monotonic_ns": deadline + latency,
            "latency_ns": latency,
        }
        for deadline, latency in zip(deadlines, sensor_latencies[1], strict=True)
    ]
    return {
        "schema_version": "edge-validation-telemetry.v1",
        "run_id": "run-a",
        "observation_mode": "no-op" if mode == "noop" else "actual",
        "warmup_duration_s": 300,
        "measurement_duration_s": 3_600,
        "measurement_started_monotonic_ns": started_ns,
        "measurement_ended_monotonic_ns": ended_ns,
        "expected_sensor_ids": ["lidar_1", "lidar_2"],
        "sample_capacity": 40_000,
        "expected_sample_count": expected_sample_count,
        "generated_scans": 72_602,
        "completed_batch_count": 3,
        "dropped_sample_count": 0,
        "overflowed": False,
        "observation": {
            "accepted_records": 3 if mode == "actual" else 0,
            "sent_records": 3 if mode == "actual" else 0,
            "dropped_records": 0,
            "connection_failures": 0,
        },
        "scan_stream": [
            {
                "sensor_id": sensor_id,
                "published_frames": 36_300,
                "frame_loss": 0,
                "subscribers": 0,
                "subscriber_seen": True,
            }
            for sensor_id in ("lidar_1", "lidar_2")
        ],
        "scan_semantics": [
            {
                "sensor_id": sensor_id,
                "sampled_scan_count": 3_600,
                "reference_sample_count": 11_520_000,
                "no_hit_count": 5_760_000,
                "floor_hit_count": 360_000,
                "wall_hit_count": 5_040_000,
                "surface_hit_count": 360_000,
                "measured_valid_count": 5_760_000,
                "measured_invalid_count": 5_760_000,
                "measured_without_reference_count": 0,
                "reference_hit_without_measurement_count": 0,
                "reference_change_count": 3_599,
            }
            for sensor_id in ("lidar_1", "lidar_2")
        ],
        "scenario": json.loads(
            (Path(__file__).with_name("data") / "scenario-telemetry.v1.json").read_text(
                encoding="utf-8"
            )
        ),
        "sensors": sensors,
        "batch_max_samples": batch_samples,
    }


def test_collector_aggregates_bounded_series_without_raw_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(helper, "EXPECTED_RESOURCE_SAMPLES", 2)
    monkeypatch.setattr(helper, "EXPECTED_LATENCY_SAMPLES", 3)
    monkeypatch.setattr(helper, "EXPECTED_STATUS_SAMPLES", 2)
    collector = _collector()
    started_ns = 400_000_000_000
    collector.set_processing_measurement_window(started_ns, started_ns + 3_600_000_000_000)
    for index in range(2):
        collector.record_resource(
            generator_cpu_percent=float(index + 1),
            generator_rss_bytes=100 + index,
            generator_memory_current_bytes=110 + index,
            processing_cpu_percent=3.0 + index,
            processing_rss_bytes=120 + index,
            processing_memory_current_bytes=130 + index,
            system_load_1m=0.5 + index,
            device_temperature_c=50.0 + index,
            helper_cpu_percent=0.1 + index,
            helper_rss_bytes=90 + index,
        )
        collector.record_service_status(generator_healthy=True, processing_healthy=True)
        collector.record_processing_measurement(
            _measurement(f"measurement-{index}", index + 1),
            received_monotonic_ns=started_ns + (index + 1) * 1_000_000_000,
            received_at_utc=datetime(2026, 9, 14, 12, 0, tzinfo=UTC),
        )
    collector.ingest_runtime_telemetry(_telemetry(expected_sample_count=3))
    collector.set_observation_received(3, "run-a")
    collector.set_event_counts(
        unclassified_loss_windows=0,
        container_restarts=0,
        oom_events=0,
        thermal_throttling_events=0,
    )

    result = collector.build_result()
    metrics = cast(dict[str, Any], result["metrics"])

    assert metrics["generator_cpu_percent"] == {
        "sample_count": 2,
        "missing_count": 0,
        "p95": 2.0,
    }
    assert metrics["joint_frame_completion_latency_ms"]["p99"] == 6.0
    assert metrics["processing_delivery_age_ms"] == {
        "sample_count": 2,
        "missing_count": 0,
        "maximum": 0.0,
    }
    assert cast(dict[str, Any], result["events"])["scan_subscriber_missing_lanes"] == 0
    assert cast(dict[str, Any], result["observation"])["received_records"] == 3
    assert result["scenario"] == {
        "initial_phase": "filling",
        "initial_cycle_index": 0,
        "final_phase": "filling",
        "final_cycle_index": 1,
        "transition_count": 2,
        "filling_to_collecting_count": 1,
        "collecting_to_filling_count": 1,
        "transition_latency_sample_count": 4,
        "maximum_transition_latency_ms": 5.0,
        "schedule_sha256": "f358fd081396659f79a89b1eb7e557ce7b058bc5bd264707d1d5bc28fbb9e247",
        "evidence_complete": True,
    }
    serialized = json.dumps(result)
    assert "measurement-a" not in serialized
    assert "deadline_monotonic_ns" not in serialized
    assert "published_monotonic_ns" not in serialized
    assert "reference_points" not in serialized
    assert "heights" not in serialized


def test_collector_marks_empty_series_and_missing_evidence_as_failures() -> None:
    result = _collector().build_result()
    metrics = cast(dict[str, Any], result["metrics"])
    evaluation = cast(dict[str, Any], result["evaluation"])

    assert metrics["generator_cpu_percent"]["p95"] is None
    assert metrics["generator_cpu_percent"]["missing_count"] == 3_600
    assert "cpu_sample_count" in evaluation["failure_codes"]
    assert "event_evidence_missing" in evaluation["failure_codes"]
    assert "observation_evidence_missing" in evaluation["failure_codes"]


def test_processing_measurement_rejects_wrong_sensor_set() -> None:
    collector = _collector()
    collector.set_processing_measurement_window(1, 10)
    measurement = _measurement()
    sensors = cast(list[dict[str, object]], measurement["sensors"])
    sensors[1]["sensor_id"] = "lidar_1"

    with pytest.raises(CollectionError, match="duplicate processing sensor state"):
        collector.record_processing_measurement(
            measurement,
            received_monotonic_ns=2,
            received_at_utc=datetime(2026, 9, 14, 12, 0, tzinfo=UTC),
        )


def test_processing_measurement_records_each_sensor_sequence_regression() -> None:
    collector = _collector()
    collector.set_processing_measurement_window(1, 10)
    received_at = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    collector.record_processing_measurement(
        _measurement("measurement-1", 7),
        received_monotonic_ns=2,
        received_at_utc=received_at,
    )
    collector.record_processing_measurement(
        _measurement("measurement-2", 7),
        received_monotonic_ns=3,
        received_at_utc=received_at,
    )

    events = cast(dict[str, Any], collector.build_result()["events"])
    evaluation = cast(dict[str, Any], collector.build_result()["evaluation"])

    assert events["processing_sequence_regressions"] == 2
    assert "processing_sequence_regression" in evaluation["failure_codes"]


def test_processing_semantics_uses_robust_filling_and_collecting_trends() -> None:
    collector = _collector()
    collector.set_processing_measurement_window(0, 40_000_000_000)
    collector._scenario_phase_intervals = [
        (0, 20_000_000_000, "filling"),
        (20_000_000_000, 40_000_000_000, "collecting"),
    ]
    received_at = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    values = [(second, 0.10 + second * 0.01) for second in range(2, 19)] + [
        (second, 0.70 - (second - 20) * 0.01) for second in range(22, 39)
    ]
    for sequence, (second, fill_ratio) in enumerate(values, start=1):
        measurement = _measurement(f"measurement-{sequence}", sequence)
        measurement["fill_ratio"] = fill_ratio
        measurement["fill_percent"] = fill_ratio * 100.0
        sensors = cast(list[dict[str, object]], measurement["sensors"])
        sensors[0]["section_fill_ratio"] = fill_ratio - 0.01
        sensors[1]["section_fill_ratio"] = fill_ratio + 0.01
        collector.record_processing_measurement(
            measurement,
            received_monotonic_ns=second * 1_000_000_000,
            received_at_utc=received_at,
        )

    semantics = collector._processing_semantics_document()

    assert semantics["measurement_count"] == len(values)
    assert semantics["complete_measurement_count"] == len(values)
    assert semantics["filling_segment_count"] == 1
    assert semantics["filling_direction_match_count"] == 1
    assert semantics["collecting_segment_count"] == 1
    assert semantics["collecting_direction_match_count"] == 1


def test_rejected_future_measurement_does_not_change_aggregates() -> None:
    collector = _collector()
    collector.set_processing_measurement_window(1, 10)

    with pytest.raises(CollectionError, match="too far in the future"):
        collector.record_processing_measurement(
            _measurement(),
            received_monotonic_ns=2,
            received_at_utc=datetime(2026, 9, 14, 11, 59, 58, tzinfo=UTC),
        )

    result = collector.build_result()
    metrics = cast(dict[str, Any], result["metrics"])
    statuses = cast(list[dict[str, Any]], metrics["processing_status"])

    assert all(status["sample_count"] == 0 for status in statuses)
    assert metrics["processing_delivery_age_ms"]["sample_count"] == 0


def test_runtime_telemetry_rejects_missing_subscriber_evidence() -> None:
    collector = _collector()
    telemetry = _telemetry()
    lanes = cast(list[dict[str, object]], telemetry["scan_stream"])
    lanes[0]["subscriber_seen"] = False

    collector.ingest_runtime_telemetry(telemetry)
    collector.set_observation_received(3, "run-a")
    collector.set_event_counts(
        unclassified_loss_windows=0,
        container_restarts=0,
        oom_events=0,
        thermal_throttling_events=0,
    )

    evaluation = cast(dict[str, Any], collector.build_result()["evaluation"])
    assert "scan_subscriber_missing" in evaluation["failure_codes"]


def test_runtime_telemetry_rejects_discontinuous_scenario_transition() -> None:
    collector = _collector()
    telemetry = _telemetry()
    scenario = cast(dict[str, Any], telemetry["scenario"])
    scenario["transitions"][1]["from"] = {"cycle_index": 7, "phase": "collecting"}

    with pytest.raises(CollectionError, match="state is discontinuous"):
        collector.ingest_runtime_telemetry(telemetry)


def test_observation_evidence_must_match_the_runtime_run() -> None:
    collector = _collector()
    collector.ingest_runtime_telemetry(_telemetry())

    with pytest.raises(CollectionError, match="run_id differs"):
        collector.set_observation_received(3, "another-run")


def test_cgroup_snapshot_uses_unique_process_smaps_rollup_rss(tmp_path: Path) -> None:
    cgroup = tmp_path / "cgroup"
    child = cgroup / "child"
    proc = tmp_path / "proc"
    child.mkdir(parents=True)
    (cgroup / "cpu.stat").write_text(
        "usage_usec 123\nnr_throttled 2\nthrottled_usec 7\n", encoding="ascii"
    )
    (cgroup / "memory.current").write_text("4096\n", encoding="ascii")
    (cgroup / "cgroup.procs").write_text("101\n", encoding="ascii")
    (child / "cgroup.procs").write_text("101\n102\n", encoding="ascii")
    for process_id, resident_kib in ((101, 3), (102, 5)):
        directory = proc / str(process_id)
        directory.mkdir(parents=True)
        (directory / "smaps_rollup").write_text(
            f"00400000-00452000 r--p 00000000 00:00 0 [rollup]\nRss: {resident_kib} kB\n",
            encoding="ascii",
        )

    snapshot = read_cgroup_snapshot(cgroup, monotonic_ns=1_000, proc_root=proc)

    assert snapshot.cpu_usage_usec == 123
    assert snapshot.rss_bytes == 8 * 1_024
    assert snapshot.memory_current_bytes == 4_096
    assert snapshot.nr_throttled == 2
    assert snapshot.throttled_usec == 7


def test_cgroup_interval_uses_actual_monotonic_delta() -> None:
    previous = CgroupSnapshot(1_000_000_000, 1_000, 1, 1, 0, 0)
    current = CgroupSnapshot(3_000_000_000, 1_500_000, 1, 1, 0, 0)

    assert helper._cpu_percent(previous, current) == pytest.approx(74.95)


def test_resource_sampler_does_not_backfill_missed_deadlines() -> None:
    deadline_ns = 10_000_000_000
    interval_ns = 1_000_000_000
    tolerance_ns = 250_000_000

    assert not helper._sample_deadline_was_missed(
        deadline_ns + tolerance_ns,
        deadline_ns,
        interval_ns,
    )
    assert helper._sample_deadline_was_missed(
        deadline_ns + tolerance_ns + 1,
        deadline_ns,
        interval_ns,
    )


def test_fingerprint_uses_names_and_contents_but_not_absolute_paths(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")

    assert fingerprint_files([second, first]) == fingerprint_files([first, second])
    assert str(tmp_path) not in fingerprint_files([first, second])


def test_clock_file_is_atomic_private_and_fresh(tmp_path: Path) -> None:
    path = tmp_path / "clock.json"

    helper._write_clock(path)
    document = json.loads(path.read_text(encoding="utf-8"))

    assert document["synchronized"] is True
    assert document["offset_ms"] == 0.0
    assert document["reported_at"].endswith("+00:00")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_status_sampler_uses_generator_and_processing_runtime_layouts(tmp_path: Path) -> None:
    generator = tmp_path / "generator-status"
    processing = tmp_path / "processing-status"
    now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    timestamp = now.isoformat()
    for service in ("lidar-driver-a", "lidar-driver-b"):
        directory = generator / service
        directory.mkdir(parents=True)
        (directory / f"{service}.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "service": service,
                    "state": "HEALTHY",
                    "reported_at": timestamp,
                    "last_progress_at": timestamp,
                }
            ),
            encoding="utf-8",
        )
    processing.mkdir()
    (processing / "lidar-processing.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "service": "lidar-processing",
                "state": "HEALTHY",
                "reported_at": timestamp,
                "last_progress_at": timestamp,
                "frame_loss": 7,
                "local_loss_count": 3,
            }
        ),
        encoding="utf-8",
    )

    assert helper._sample_status_files(generator, processing, now=now) == (True, True, 7, 3)


def test_status_sampler_rejects_stale_healthy_snapshot(tmp_path: Path) -> None:
    now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    stale = now - timedelta(seconds=31)
    status = {
        "schema_version": "1.0",
        "service": "lidar-processing",
        "state": "HEALTHY",
        "reported_at": stale.isoformat(),
        "last_progress_at": stale.isoformat(),
    }

    assert not helper._status_is_current_and_healthy(status, "lidar-processing", now)


def test_resource_collection_preserves_warmup_processing_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Collector:
        def record_cgroup_interval(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        def record_service_status(self, **kwargs: object) -> None:
            del kwargs

    async def no_sleep(_deadline_ns: int) -> None:
        return None

    snapshots = iter((0, 1_000_000_000))
    status = iter(((True, True, 5, 2), (True, True, 5, 2)))
    sample = CgroupSnapshot(
        monotonic_ns=0,
        cpu_usage_usec=0,
        rss_bytes=1,
        memory_current_bytes=1,
        nr_throttled=0,
        throttled_usec=0,
    )
    monkeypatch.setattr(helper, "_sleep_until_ns", no_sleep)
    monkeypatch.setattr(time, "monotonic_ns", lambda: next(snapshots))
    monkeypatch.setattr(helper, "_sample_status_files", lambda *args, **kwargs: next(status))
    monkeypatch.setattr(helper, "_read_target_snapshot", lambda *args, **kwargs: sample)
    monkeypatch.setattr(helper, "_helper_cpu_percent", lambda *args: 0.0)
    monkeypatch.setattr(helper, "_read_process_rss_bytes", lambda *args: 1)
    monkeypatch.setattr(helper, "_temperature_c", lambda *args: 40.0)

    result = asyncio.run(
        helper._collect_resource_samples(
            cast(Any, Collector()),
            cast(
                Any,
                SimpleNamespace(
                    generator=object(),
                    processing=object(),
                    temperature_path=Path("/temperature"),
                ),
            ),
            generator_status_dir=Path("/generator"),
            processing_status_dir=Path("/processing"),
            proc_root=Path("/proc"),
            measurement_start_ns=0,
            sample_count=1,
            sample_interval_s=1.0,
        )
    )

    assert result == (0, 5, 2)


def test_control_rejects_uncontracted_private_identity_fields(tmp_path: Path) -> None:
    cgroup_root = tmp_path / "cgroup"
    cgroup_root.mkdir()
    control = {
        "schema_version": "long-validation-control.v1",
        "start_at_monotonic_ns": 123,
        "identity": {
            "generator_source_commit": "1" * 40,
            "generator_image_digest": f"sha256:{'2' * 64}",
            "processing_source_commit": "3" * 40,
            "processing_image_digest": f"sha256:{'4' * 64}",
            "config_fingerprint_sha256": "5" * 64,
            "processing_config_sha256": "6" * 64,
            "seed": 9,
            "private_hostname": "not-allowed",
        },
        "expected_measurement_identity": {
            "site_id": "synthetic-site",
            "edge_id": "synthetic-edge",
            "config_revision": "synthetic-v1",
            "calibration_version": "synthetic-v1",
        },
        "device": _device(),
        "workload": {"mean_fill_duration_s": 600, "observation_mode": "noop"},
        "generator": {"host_pid": 1, "cgroup_path": str(cgroup_root / "generator")},
        "processing": {"host_pid": 2, "cgroup_path": str(cgroup_root / "processing")},
        "runtime_telemetry_path": str(tmp_path / "telemetry.json"),
        "temperature_path": str(tmp_path / "temperature"),
        "observation_result_path": None,
        "lifecycle_result_path": str(tmp_path / "lifecycle.json"),
    }
    path = tmp_path / "control.json"
    path.write_text(json.dumps(control), encoding="utf-8")
    loaded = helper._load_control(path, expected_start_ns=123, cgroup_root=cgroup_root)

    with pytest.raises(CollectionError, match=r"control\.identity fields differ"):
        helper._collector_from_control(loaded)


def test_control_rejects_cgroup_path_outside_read_only_root(tmp_path: Path) -> None:
    cgroup_root = tmp_path / "cgroup"
    cgroup_root.mkdir()

    with pytest.raises(CollectionError, match="below the configured cgroup root"):
        helper._process_target(
            {"host_pid": 1, "cgroup_path": str(tmp_path / "outside")},
            "control.generator",
            cgroup_root,
        )


def test_measurement_sink_generic_grpc_contract(tmp_path: Path) -> None:
    async def exercise() -> None:
        sink = helper._MeasurementSink(_test_measurement_validator)
        collector = _collector()
        now = time.monotonic_ns()
        sink.configure(
            collector,
            measurement_started_ns=now - 1_000_000_000,
            measurement_ended_ns=now + 1_000_000_000,
            expected_identity={
                "site_id": "synthetic-site",
                "edge_id": "synthetic-edge",
                "config_revision": "synthetic-v1",
                "calibration_version": "synthetic-v1",
            },
        )
        socket = tmp_path / "measurement.sock"
        server = await helper._start_measurement_server(socket, sink)
        assert stat.S_IMODE(socket.stat().st_mode) == 0o660
        payload = json.dumps(_measurement()).encode()
        request = b"\x0a" + helper._encode_varint(len(payload)) + payload
        try:
            async with grpc.aio.insecure_channel(
                f"unix:{socket}", options=(("grpc.default_authority", "localhost"),)
            ) as channel:
                enqueue = channel.unary_unary(
                    "/ajin.edge.delivery.v1.MeasurementSink/Enqueue",
                    request_serializer=lambda value: value,
                    response_deserializer=lambda value: value,
                )
                first = await enqueue(request)
                second = await enqueue(request)
                conflicting_payload = json.dumps(_measurement() | {"quality": {"state": "INVALID"}})
                conflicting_request = (
                    b"\x0a"
                    + helper._encode_varint(len(conflicting_payload.encode()))
                    + conflicting_payload.encode()
                )
                with pytest.raises(grpc.aio.AioRpcError) as conflict:
                    await enqueue(conflicting_request)
            assert first == b"\x0a\rmeasurement-a"
            assert second == b"\x0a\rmeasurement-a\x10\x01"
            assert conflict.value.code() == grpc.StatusCode.ALREADY_EXISTS
        finally:
            await server.stop(grace=0)

    asyncio.run(exercise())


def test_measurement_sink_rejects_payload_outside_processing_contract(tmp_path: Path) -> None:
    async def exercise() -> None:
        sink = helper._MeasurementSink(_test_measurement_validator)
        collector = _collector()
        now = time.monotonic_ns()
        sink.configure(
            collector,
            measurement_started_ns=now - 1_000_000_000,
            measurement_ended_ns=now + 1_000_000_000,
            expected_identity={
                "site_id": "synthetic-site",
                "edge_id": "synthetic-edge",
                "config_revision": "synthetic-v1",
                "calibration_version": "synthetic-v1",
            },
        )
        socket = tmp_path / "measurement.sock"
        server = await helper._start_measurement_server(socket, sink)
        payload = json.dumps(
            {
                "measurement_id": "looks-good-but-is-not-a-contract-envelope",
                "sensors": [
                    {"sensor_id": "lidar_1", "state": "GOOD"},
                    {"sensor_id": "lidar_2", "state": "GOOD"},
                ],
                "quality": {"state": "GOOD"},
            }
        ).encode()
        request = b"\x0a" + helper._encode_varint(len(payload)) + payload
        try:
            async with grpc.aio.insecure_channel(
                f"unix:{socket}", options=(("grpc.default_authority", "localhost"),)
            ) as channel:
                enqueue = channel.unary_unary(
                    "/ajin.edge.delivery.v1.MeasurementSink/Enqueue",
                    request_serializer=lambda value: value,
                    response_deserializer=lambda value: value,
                )
                with pytest.raises(grpc.aio.AioRpcError) as invalid:
                    await enqueue(request)
            assert invalid.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        finally:
            await server.stop(grace=0)

    asyncio.run(exercise())


def _helper_cli_arguments(tmp_path: Path) -> list[str]:
    return [
        "--control",
        str(tmp_path / "control.json"),
        "--measurement-socket",
        str(tmp_path / "measurement.sock"),
        "--generator-status-dir",
        str(tmp_path / "generator-status"),
        "--processing-status-dir",
        str(tmp_path / "processing-status"),
        "--clock-file",
        str(tmp_path / "clock.json"),
        "--ready-file",
        str(tmp_path / "ready.json"),
        "--output",
        str(tmp_path / "result.json"),
    ]


def test_helper_cli_writes_successful_aggregate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run(arguments: argparse.Namespace) -> dict[str, object]:
        assert arguments.setup_lead_s == 30.0
        assert arguments.control_timeout_s == 20.0
        return cast(dict[str, object], passing_run(600, "actual"))

    monkeypatch.setattr(helper, "_run_helper", fake_run)

    assert helper.main(_helper_cli_arguments(tmp_path)) == 0
    assert json.loads((tmp_path / "result.json").read_text())["schema_version"] == "run-result.v1"


def test_helper_cli_returns_two_for_collection_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fail(_arguments: argparse.Namespace) -> dict[str, object]:
        raise CollectionError("test failure")

    monkeypatch.setattr(helper, "_run_helper", fail)

    assert helper.main(_helper_cli_arguments(tmp_path)) == 2
    assert "long validation helper failed: test failure" in capsys.readouterr().err


def test_helper_cli_returns_one_for_failed_aggregate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run(_arguments: argparse.Namespace) -> dict[str, object]:
        result = cast(dict[str, object], passing_run(600, "actual"))
        result["evaluation"] = {"passed": False, "failure_codes": ["oom_event"]}
        return result

    monkeypatch.setattr(helper, "_run_helper", fake_run)

    assert helper.main(_helper_cli_arguments(tmp_path)) == 1


def test_helper_cli_rejects_nonpositive_duration(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as error:
        helper.main([*_helper_cli_arguments(tmp_path), "--measure-s", "0"])

    assert error.value.code == 2


def test_helper_rejects_aliased_output_paths(tmp_path: Path) -> None:
    arguments = helper.build_parser().parse_args(_helper_cli_arguments(tmp_path))
    arguments.output = tmp_path / "child" / ".." / "ready.json"

    with pytest.raises(CollectionError, match="must be distinct"):
        helper._validate_cli_paths(arguments)
