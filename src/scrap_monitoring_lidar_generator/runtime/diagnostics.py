"""Bounded local diagnostics for reference generation results."""

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Protocol, TextIO

from scrap_monitoring_lidar_generator.configuration import GeneratorInputs
from scrap_monitoring_lidar_generator.measurement import MeasurementResult
from scrap_monitoring_lidar_generator.scenario import ScenarioSimulator

_MAX_SEED = 18_446_744_073_709_551_615


class MeasurementDiagnosticsSink(Protocol):
    """Receive completed results without influencing generation state."""

    def record(self, result: MeasurementResult, scenario: ScenarioSimulator) -> None:
        """Record one result and its completion-time scenario state."""
        ...


class JsonLinesDiagnosticsWriter:
    """Write bounded reference snapshots to a new owner-readable JSON Lines file."""

    __slots__ = (
        "_closed",
        "_environment_id",
        "_input_fingerprint",
        "_output_directory",
        "_recorded_counts",
        "_sample_scan_limit_per_sensor",
        "_seed",
        "_sensor_ids",
        "_stream",
        "_stream_path",
    )

    def __init__(
        self,
        *,
        output_directory: Path,
        environment_id: str,
        input_fingerprint: str,
        seed: int,
        sensor_ids: tuple[str, ...],
        sample_scan_limit_per_sensor: int,
    ) -> None:
        if not environment_id:
            raise ValueError("diagnostics environment_id must be non-empty")
        if len(input_fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in input_fingerprint
        ):
            raise ValueError("diagnostics input fingerprint must be lowercase SHA-256 hex")
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= _MAX_SEED:
            raise ValueError("diagnostics seed must be an unsigned 64-bit integer")
        if not sensor_ids or any(not sensor_id for sensor_id in sensor_ids):
            raise ValueError("diagnostics sensor identifiers must be non-empty")
        if len(set(sensor_ids)) != len(sensor_ids):
            raise ValueError("diagnostics sensor identifiers must be unique")
        if (
            isinstance(sample_scan_limit_per_sensor, bool)
            or not isinstance(sample_scan_limit_per_sensor, int)
            or sample_scan_limit_per_sensor < 0
        ):
            raise ValueError("diagnostics sample limit must be a non-negative integer")

        self._output_directory = Path(output_directory)
        self._environment_id = environment_id
        self._input_fingerprint = input_fingerprint
        self._seed = seed
        self._sensor_ids = frozenset(sensor_ids)
        self._sample_scan_limit_per_sensor = sample_scan_limit_per_sensor
        self._recorded_counts = dict.fromkeys(sensor_ids, 0)
        self._stream: TextIO | None = None
        self._stream_path: Path | None = None
        self._closed = False

    @property
    def output_path(self) -> Path | None:
        """Return the allocated output file after the first recorded result."""
        return self._stream_path

    @property
    def recorded_counts(self) -> Mapping[str, int]:
        """Return an independent sensor-to-record-count mapping."""
        return self._recorded_counts.copy()

    def record(self, result: MeasurementResult, scenario: ScenarioSimulator) -> None:
        """Write one bounded reference snapshot and flush the complete JSON line."""
        if self._closed:
            raise RuntimeError("diagnostics writer is closed")
        sensor_id = result.sensor_id
        if sensor_id not in self._sensor_ids:
            raise ValueError("diagnostics result sensor_id is not configured")
        if self._recorded_counts[sensor_id] >= self._sample_scan_limit_per_sensor:
            return
        if scenario.elapsed_s != result.reference.completed_at_s:
            raise ValueError("diagnostics scenario time must match scan completion")

        stream = self._ensure_stream()
        document = self._build_document(result, scenario)
        stream.write(
            json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        )
        stream.write("\n")
        stream.flush()
        self._recorded_counts[sensor_id] += 1

    def close(self) -> None:
        """Close the output file if one was allocated."""
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        self._closed = True

    def __enter__(self) -> JsonLinesDiagnosticsWriter:
        if self._closed:
            raise RuntimeError("diagnostics writer is closed")
        return self

    def __exit__(self, *_arguments: object) -> None:
        self.close()

    def _ensure_stream(self) -> TextIO:
        if self._stream is not None:
            return self._stream
        self._output_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        sequence = 1
        while True:
            candidate = self._output_directory / f"reference-scans.v1.{sequence:04d}.jsonl"
            try:
                descriptor = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                sequence += 1
                continue
            self._stream = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
            self._stream_path = candidate
            return self._stream

    def _build_document(
        self,
        result: MeasurementResult,
        scenario: ScenarioSimulator,
    ) -> dict[str, object]:
        snapshot = scenario.snapshot
        surface = scenario.surface
        return {
            "diagnostics_version": 1,
            "environment_id": self._environment_id,
            "input_fingerprint_sha256": self._input_fingerprint,
            "seed": self._seed,
            "sensor_id": result.sensor_id,
            "scan_id": result.scan_id,
            "captured_elapsed_s": result.reference.captured_elapsed_s,
            "completed_at_s": result.reference.completed_at_s,
            "scenario": {
                "elapsed_s": snapshot.elapsed_s,
                "surface_updated_at_s": snapshot.surface_updated_at_s,
                "cycle_index": snapshot.cycle_index,
                "phase": snapshot.phase.value,
                "phase_started_at_s": snapshot.phase_started_at_s,
                "phase_ends_at_s": snapshot.phase_ends_at_s,
                "phase_duration_s": snapshot.phase_duration_s,
                "rate_factor": snapshot.rate_factor,
                "target_fill_ratio": snapshot.target_fill_ratio,
                "surface_fill_ratio": snapshot.surface_fill_ratio,
                "surface_volume_m3": snapshot.surface_volume_m3,
                "current_inlet_index": snapshot.current_inlet_index,
            },
            "surface": {
                "snapshot_at_s": snapshot.elapsed_s,
                "boundary_xy_m": [[vertex.x, vertex.y] for vertex in surface.boundary.vertices],
                "floor_z_m": surface.floor_z_m,
                "top_z_m": surface.top_z_m,
                "cell_size_m": surface.cell_size_m,
                "shape": list(surface.shape),
                "heights_m": surface.heights_m.tolist(),
            },
            "reference_points": [
                [
                    point.angle_deg,
                    point.distance_m,
                    None if point.hit_kind is None else point.hit_kind.value,
                ]
                for point in result.reference.scan.points
            ],
        }


def generator_input_fingerprint(inputs: GeneratorInputs) -> str:
    """Return a stable SHA-256 fingerprint of values that determine generated scans."""
    canonical_values = {
        "seed": inputs.generator.seed,
        "scenario": asdict(inputs.generator.scenario),
        "measurement": asdict(inputs.generator.measurement),
        "environment": asdict(inputs.environment),
        "quality_profile": asdict(inputs.quality_profile),
    }
    payload = json.dumps(
        canonical_values,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_diagnostics_writer(inputs: GeneratorInputs) -> JsonLinesDiagnosticsWriter:
    """Build a bounded writer from validated generator inputs."""
    config = inputs.generator.diagnostics
    return JsonLinesDiagnosticsWriter(
        output_directory=config.output_path,
        environment_id=inputs.environment.environment_id,
        input_fingerprint=generator_input_fingerprint(inputs),
        seed=inputs.generator.seed,
        sensor_ids=tuple(sensor.sensor_id for sensor in inputs.environment.sensors),
        sample_scan_limit_per_sensor=config.sample_scan_limit_per_sensor,
    )
