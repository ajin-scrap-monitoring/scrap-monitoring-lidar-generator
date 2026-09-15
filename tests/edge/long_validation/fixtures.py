"""Compact aggregate fixtures for long-validation helper tests."""

from typing import Any

from .evaluator import (
    EXPECTED_LATENCY_SAMPLES,
    EXPECTED_OBSERVATION_RECORDS,
    EXPECTED_RESOURCE_SAMPLES,
    EXPECTED_SENSOR_IDS,
    EXPECTED_STATUS_SAMPLES,
)


def passing_run(
    mean_fill_duration_s: int,
    observation_mode: str,
    *,
    cpu_p95: float = 60.0,
) -> dict[str, Any]:
    """Return one complete, public-safe aggregate result."""
    observation_count = EXPECTED_OBSERVATION_RECORDS if observation_mode == "actual" else 0
    accelerated = mean_fill_duration_s == 600
    p95_resource = {
        "sample_count": EXPECTED_RESOURCE_SAMPLES,
        "missing_count": 0,
        "p95": 1.0,
    }
    return {
        "schema_version": "run-result.v1",
        "identity": {
            "generator_source_commit": "1" * 40,
            "generator_image_digest": f"sha256:{'2' * 64}",
            "processing_source_commit": "3" * 40,
            "processing_image_digest": f"sha256:{'4' * 64}",
            "config_fingerprint_sha256": "5" * 64,
            "processing_config_sha256": "6" * 64,
            "seed": 20260914,
        },
        "device": {
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
        },
        "workload": {
            "mean_fill_duration_s": mean_fill_duration_s,
            "observation_mode": observation_mode,
            "warmup_duration_s": 300,
            "measurement_duration_s": 3_600,
            "sensor_ids": list(EXPECTED_SENSOR_IDS),
        },
        "metrics": {
            "generator_cpu_percent": {
                "sample_count": EXPECTED_RESOURCE_SAMPLES,
                "missing_count": 0,
                "p95": cpu_p95,
            },
            "generator_rss_bytes": {
                "sample_count": EXPECTED_RESOURCE_SAMPLES,
                "missing_count": 0,
                "p95": 80 * 1_048_576,
            },
            "processing_cpu_percent": p95_resource.copy(),
            "processing_rss_bytes": p95_resource.copy(),
            "generator_cgroup_memory_current_bytes": p95_resource.copy(),
            "processing_cgroup_memory_current_bytes": p95_resource.copy(),
            "system_load_1m": p95_resource.copy(),
            "device_temperature_c": {
                "sample_count": EXPECTED_RESOURCE_SAMPLES,
                "missing_count": 0,
                "maximum": 55.0,
            },
            "helper_cpu_percent": p95_resource.copy(),
            "helper_rss_bytes": p95_resource.copy(),
            "cgroup_throttling": [
                {
                    "component": "generator",
                    "nr_throttled_delta": 0,
                    "throttled_usec_delta": 0,
                },
                {
                    "component": "processing",
                    "nr_throttled_delta": 0,
                    "throttled_usec_delta": 0,
                },
            ],
            "joint_frame_completion_latency_ms": {
                "sample_count": EXPECTED_LATENCY_SAMPLES,
                "missing_count": 0,
                "p99": 60.0,
            },
            "sensor_frame_completion_latency_ms": [
                {
                    "sensor_id": sensor_id,
                    "sample_count": EXPECTED_LATENCY_SAMPLES,
                    "missing_count": 0,
                    "p99": 55.0,
                }
                for sensor_id in EXPECTED_SENSOR_IDS
            ],
            "processing_status": [
                {
                    "result_id": result_id,
                    "sample_count": EXPECTED_STATUS_SAMPLES,
                    "good_count": EXPECTED_STATUS_SAMPLES,
                }
                for result_id in (*EXPECTED_SENSOR_IDS, "fused")
            ],
            "processing_measurement_freshness_s": {
                "measurement_count": EXPECTED_STATUS_SAMPLES,
                "gap_count": EXPECTED_STATUS_SAMPLES + 1,
                "maximum_gap_s": 1.1,
            },
            "processing_delivery_age_ms": {
                "sample_count": EXPECTED_STATUS_SAMPLES,
                "missing_count": 0,
                "maximum": 50.0,
            },
            "service_status": [
                {
                    "component": component,
                    "sample_count": EXPECTED_STATUS_SAMPLES,
                    "healthy_count": EXPECTED_STATUS_SAMPLES,
                }
                for component in ("generator", "processing")
            ],
        },
        "events": {
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
            "evidence_complete": True,
        },
        "observation": {
            "sent_records": observation_count,
            "received_records": observation_count,
            "dropped_records": 0,
            "connection_failures": 0,
            "evidence_complete": True,
        },
        "scenario": {
            "initial_phase": "filling",
            "initial_cycle_index": 0,
            "final_phase": "filling",
            "final_cycle_index": 5 if accelerated else 0,
            "transition_count": 10 if accelerated else 0,
            "filling_to_collecting_count": 5 if accelerated else 0,
            "collecting_to_filling_count": 5 if accelerated else 0,
            "transition_latency_sample_count": 20 if accelerated else 0,
            "maximum_transition_latency_ms": 60.0 if accelerated else None,
            "schedule_sha256": ("7" if accelerated else "8") * 64,
            "evidence_complete": True,
        },
        "semantics": {
            "simulator": {
                "sensor_scan_evidence": [
                    {
                        "sensor_id": sensor_id,
                        "sampled_scan_count": EXPECTED_STATUS_SAMPLES,
                        "reference_sample_count": EXPECTED_STATUS_SAMPLES * 3_200,
                        "no_hit_count": EXPECTED_STATUS_SAMPLES * 1_600,
                        "floor_hit_count": EXPECTED_STATUS_SAMPLES * 100,
                        "wall_hit_count": EXPECTED_STATUS_SAMPLES * 1_400,
                        "surface_hit_count": EXPECTED_STATUS_SAMPLES * 100,
                        "measured_valid_count": EXPECTED_STATUS_SAMPLES * 1_600,
                        "measured_invalid_count": EXPECTED_STATUS_SAMPLES * 1_600,
                        "measured_without_reference_count": 0,
                        "reference_hit_without_measurement_count": 0,
                        "reference_change_count": EXPECTED_STATUS_SAMPLES - 1,
                    }
                    for sensor_id in EXPECTED_SENSOR_IDS
                ],
                "evidence_complete": True,
                "passed": True,
            },
            "lidar_processing": {
                "measurement_count": EXPECTED_STATUS_SAMPLES,
                "complete_measurement_count": EXPECTED_STATUS_SAMPLES,
                "missing_value_count": 0,
                "height_range_violation_count": 0,
                "height_order_violation_count": 0,
                "fusion_range_violation_count": 0,
                "quality_ratio_violation_count": 0,
                "filling_segment_count": 6 if accelerated else 1,
                "filling_direction_match_count": 6 if accelerated else 1,
                "collecting_segment_count": 5 if accelerated else 0,
                "collecting_direction_match_count": 5 if accelerated else 0,
                "evidence_complete": True,
                "passed": True,
            },
            "failure_domain": "none",
        },
        "evaluation": {"passed": True, "failure_codes": []},
    }
