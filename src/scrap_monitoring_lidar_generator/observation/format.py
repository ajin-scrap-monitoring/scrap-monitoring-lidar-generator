"""Versioned JSON Lines records for the optional load-model observer."""

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from scrap_monitoring_lidar_generator.scenario import (
    ScenarioModelSnapshot,
    ScenarioPhase,
    ScenarioSnapshot,
    SurfaceModelSnapshot,
)

OBSERVATION_VERSION = 1
_MAX_SEED = 18_446_744_073_709_551_615
_SCENARIO_FIELDS = frozenset(
    {
        "elapsed_s",
        "surface_updated_at_s",
        "cycle_index",
        "phase",
        "phase_started_at_s",
        "phase_ends_at_s",
        "phase_duration_s",
        "rate_factor",
        "target_fill_ratio",
        "surface_fill_ratio",
        "surface_volume_m3",
        "current_inlet_index",
    }
)
_SURFACE_FIELDS = frozenset(
    {
        "cell_size_m",
        "x_coordinates_m",
        "y_coordinates_m",
        "heights_m",
    }
)
_RECORD_FIELDS = frozenset(
    {
        "observation_version",
        "type",
        "environment_id",
        "run_id",
        "input_fingerprint_sha256",
        "seed",
        "scenario",
        "surface",
    }
)


class ObservationFormatError(ValueError):
    """Raised when a version 1 observation record is malformed."""


@dataclass(frozen=True, slots=True)
class ObservationRecord:
    """One immutable observation record with its model snapshot."""

    environment_id: str
    run_id: str
    input_fingerprint_sha256: str
    seed: int
    snapshot: ScenarioModelSnapshot

    def __post_init__(self) -> None:
        _require_non_empty_string(self.environment_id, "environment_id")
        _require_non_empty_string(self.run_id, "run_id")
        _require_fingerprint(self.input_fingerprint_sha256)
        if (
            isinstance(self.seed, bool)
            or not isinstance(self.seed, int)
            or not 0 <= self.seed <= _MAX_SEED
        ):
            raise ValueError("observation seed must be an unsigned 64-bit integer")
        if not isinstance(self.snapshot, ScenarioModelSnapshot):
            raise ValueError("observation snapshot must be a ScenarioModelSnapshot")

    @classmethod
    def from_snapshot(
        cls,
        snapshot: ScenarioModelSnapshot,
        *,
        environment_id: str,
        run_id: str,
        input_fingerprint_sha256: str,
        seed: int,
    ) -> ObservationRecord:
        """Attach run metadata to an immutable model snapshot."""
        return cls(
            environment_id=environment_id,
            run_id=run_id,
            input_fingerprint_sha256=input_fingerprint_sha256,
            seed=seed,
            snapshot=snapshot,
        )

    def to_document(self) -> dict[str, object]:
        """Return the JSON-compatible version 1 representation."""
        state = self.snapshot.state
        surface = self.snapshot.surface
        return {
            "observation_version": OBSERVATION_VERSION,
            "type": "load_model_observation",
            "environment_id": self.environment_id,
            "run_id": self.run_id,
            "input_fingerprint_sha256": self.input_fingerprint_sha256,
            "seed": self.seed,
            "scenario": {
                "elapsed_s": state.elapsed_s,
                "surface_updated_at_s": state.surface_updated_at_s,
                "cycle_index": state.cycle_index,
                "phase": state.phase.value,
                "phase_started_at_s": state.phase_started_at_s,
                "phase_ends_at_s": state.phase_ends_at_s,
                "phase_duration_s": state.phase_duration_s,
                "rate_factor": state.rate_factor,
                "target_fill_ratio": state.target_fill_ratio,
                "surface_fill_ratio": state.surface_fill_ratio,
                "surface_volume_m3": state.surface_volume_m3,
                "current_inlet_index": state.current_inlet_index,
            },
            "surface": {
                "cell_size_m": surface.cell_size_m,
                "x_coordinates_m": surface.x_coordinates_m.tolist(),
                "y_coordinates_m": surface.y_coordinates_m.tolist(),
                "heights_m": surface.heights_m.tolist(),
            },
        }

    @classmethod
    def from_document(cls, value: Mapping[str, Any]) -> ObservationRecord:
        """Parse one strict version 1 JSON object."""
        _require_exact_fields(value, _RECORD_FIELDS, "observation")
        if (
            isinstance(value["observation_version"], bool)
            or value["observation_version"] != OBSERVATION_VERSION
        ):
            raise ObservationFormatError("observation_version must be 1")
        if value["type"] != "load_model_observation":
            raise ObservationFormatError("observation type is not load_model_observation")

        scenario_value = _require_mapping(value["scenario"], "observation.scenario")
        _require_exact_fields(scenario_value, _SCENARIO_FIELDS, "observation.scenario")
        phase_value = _require_string(scenario_value["phase"], "observation.scenario.phase")
        try:
            phase = ScenarioPhase(phase_value)
        except ValueError as error:
            raise ObservationFormatError("observation.scenario.phase is invalid") from error

        current_inlet = scenario_value["current_inlet_index"]
        if current_inlet is not None:
            current_inlet = _require_integer(
                current_inlet,
                "observation.scenario.current_inlet_index",
                minimum=0,
            )
        state = ScenarioSnapshot(
            elapsed_s=_require_non_negative_number(
                scenario_value["elapsed_s"], "observation.scenario.elapsed_s"
            ),
            surface_updated_at_s=_require_non_negative_number(
                scenario_value["surface_updated_at_s"],
                "observation.scenario.surface_updated_at_s",
            ),
            cycle_index=_require_integer(
                scenario_value["cycle_index"],
                "observation.scenario.cycle_index",
                minimum=0,
            ),
            phase=phase,
            phase_started_at_s=_require_non_negative_number(
                scenario_value["phase_started_at_s"],
                "observation.scenario.phase_started_at_s",
            ),
            phase_ends_at_s=_require_non_negative_number(
                scenario_value["phase_ends_at_s"],
                "observation.scenario.phase_ends_at_s",
            ),
            phase_duration_s=_require_positive_number(
                scenario_value["phase_duration_s"],
                "observation.scenario.phase_duration_s",
            ),
            rate_factor=_require_positive_number(
                scenario_value["rate_factor"],
                "observation.scenario.rate_factor",
            ),
            target_fill_ratio=_require_ratio(
                scenario_value["target_fill_ratio"],
                "observation.scenario.target_fill_ratio",
            ),
            surface_fill_ratio=_require_ratio(
                scenario_value["surface_fill_ratio"],
                "observation.scenario.surface_fill_ratio",
            ),
            surface_volume_m3=_require_non_negative_number(
                scenario_value["surface_volume_m3"],
                "observation.scenario.surface_volume_m3",
            ),
            current_inlet_index=current_inlet,
        )

        surface_value = _require_mapping(value["surface"], "observation.surface")
        _require_exact_fields(surface_value, _SURFACE_FIELDS, "observation.surface")
        x_coordinates_m = _require_number_vector(
            surface_value["x_coordinates_m"],
            "observation.surface.x_coordinates_m",
        )
        y_coordinates_m = _require_number_vector(
            surface_value["y_coordinates_m"],
            "observation.surface.y_coordinates_m",
        )
        heights_m = _require_number_matrix(
            surface_value["heights_m"],
            "observation.surface.heights_m",
        )
        try:
            surface = SurfaceModelSnapshot(
                x_coordinates_m=x_coordinates_m,
                y_coordinates_m=y_coordinates_m,
                heights_m=heights_m,
                cell_size_m=_require_number(
                    surface_value["cell_size_m"],
                    "observation.surface.cell_size_m",
                ),
            )
        except ValueError as error:
            raise ObservationFormatError(str(error)) from error

        fingerprint = _require_string(
            value["input_fingerprint_sha256"],
            "observation.input_fingerprint_sha256",
        )
        try:
            _require_fingerprint(fingerprint)
        except ValueError as error:
            raise ObservationFormatError(str(error)) from error
        return cls(
            environment_id=_require_string(value["environment_id"], "observation.environment_id"),
            run_id=_require_string(value["run_id"], "observation.run_id"),
            input_fingerprint_sha256=fingerprint,
            seed=_require_integer(value["seed"], "observation.seed", minimum=0, maximum=_MAX_SEED),
            snapshot=ScenarioModelSnapshot(state=state, surface=surface),
        )


def encode_observation_line(record: ObservationRecord) -> str:
    """Encode one observation record as a compact JSON line."""
    return json.dumps(
        record.to_document(), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )


def decode_observation_line(line: str) -> ObservationRecord:
    """Decode one JSON Lines record with strict object and number checks."""
    try:
        value = json.loads(
            line,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_non_finite_constant,
        )
    except (json.JSONDecodeError, ObservationFormatError) as error:
        raise ObservationFormatError(f"invalid observation JSON: {error}") from error
    if not isinstance(value, dict):
        raise ObservationFormatError("observation line must contain an object")
    return ObservationRecord.from_document(value)


def _require_exact_fields(value: Mapping[str, Any], expected: frozenset[str], path: str) -> None:
    actual = frozenset(value)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing:
        raise ObservationFormatError(f"{path} is missing fields: {', '.join(missing)}")
    if unexpected:
        raise ObservationFormatError(f"{path} contains unexpected fields: {', '.join(unexpected)}")


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ObservationFormatError(f"{path} must be an object")
    return value


def _require_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ObservationFormatError(f"{path} must be a non-empty string")
    return value


def _require_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ObservationFormatError(f"{path} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ObservationFormatError(f"{path} must be finite")
    return result


def _require_non_negative_number(value: Any, path: str) -> float:
    result = _require_number(value, path)
    if result < 0.0:
        raise ObservationFormatError(f"{path} must be non-negative")
    return result


def _require_positive_number(value: Any, path: str) -> float:
    result = _require_number(value, path)
    if result <= 0.0:
        raise ObservationFormatError(f"{path} must be positive")
    return result


def _require_ratio(value: Any, path: str) -> float:
    result = _require_number(value, path)
    if not 0.0 <= result <= 1.0:
        raise ObservationFormatError(f"{path} must be between 0 and 1")
    return result


def _require_integer(
    value: Any,
    path: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ObservationFormatError(f"{path} must be an integer")
    if minimum is not None and value < minimum:
        raise ObservationFormatError(f"{path} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ObservationFormatError(f"{path} must be at most {maximum}")
    return value


def _require_number_vector(value: Any, path: str) -> np.ndarray:
    if not isinstance(value, list) or len(value) < 2:
        raise ObservationFormatError(f"{path} must contain at least 2 numbers")
    result = np.asarray(
        [_require_number(item, f"{path}[{index}]") for index, item in enumerate(value)]
    )
    return result


def _require_number_matrix(value: Any, path: str) -> np.ndarray:
    if not isinstance(value, list) or not value:
        raise ObservationFormatError(f"{path} must contain at least 1 row")
    rows = []
    for row_index, row in enumerate(value):
        if not isinstance(row, list) or not row:
            raise ObservationFormatError(f"{path}[{row_index}] must contain numbers")
        rows.append(
            [
                _require_number(item, f"{path}[{row_index}][{column_index}]")
                for column_index, item in enumerate(row)
            ]
        )
    width = len(rows[0])
    if any(len(row) != width for row in rows):
        raise ObservationFormatError(f"{path} rows must have equal lengths")
    return np.asarray(rows, dtype=np.float64)


def _require_non_empty_string(value: str, path: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"observation {path} must be a non-empty string")


def _require_fingerprint(value: str) -> None:
    _require_non_empty_string(value, "input fingerprint")
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("observation input fingerprint must be lowercase SHA-256 hex")


def _reject_non_finite_constant(value: str) -> None:
    raise ObservationFormatError(f"non-finite JSON number is not allowed: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ObservationFormatError(f"duplicate observation field: {key}")
        result[key] = value
    return result
