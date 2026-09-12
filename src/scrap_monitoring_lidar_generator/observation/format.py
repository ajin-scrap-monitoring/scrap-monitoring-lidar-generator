"""Versioned JSON Lines records for load-model observation."""

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from scrap_monitoring_lidar_generator.scenario import (
    ScenarioModelSnapshot,
    ScenarioPhase,
    ScenarioSnapshot,
    SurfaceModelSnapshot,
)

if TYPE_CHECKING:
    from scrap_monitoring_lidar_generator.configuration import GeneratorInputs

OBSERVATION_VERSION = 1
MAX_OBSERVATION_LINE_BYTES = 1_048_576
_MAX_SEED = 18_446_744_073_709_551_615
_MAX_SEQUENCE = 9_007_199_254_740_991
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
_SCENE_FIELDS = frozenset(
    {
        "coordinate_system",
        "length_unit",
        "angle_unit",
        "boundary_xy_m",
        "floor_z_m",
        "top_z_m",
        "inlet_positions_xy_m",
        "sensors",
    }
)
_SENSOR_FIELDS = frozenset({"sensor_id", "p0_m", "u0", "u90"})
_RECORD_FIELDS = frozenset(
    {
        "observation_version",
        "type",
        "sequence",
        "run_id",
        "scenario",
        "surface",
    }
)
_HEADER_FIELDS = frozenset(
    {
        "observation_version",
        "type",
        "environment_id",
        "run_id",
        "input_fingerprint_sha256",
        "seed",
        "scene",
    }
)


class ObservationFormatError(ValueError):
    """Raised when a version 1 observation record is malformed."""


@dataclass(frozen=True, slots=True)
class ObservationSensor:
    """One sensor pose in the shared right-handed coordinate system."""

    sensor_id: str
    p0_m: tuple[float, float, float]
    u0: tuple[float, float, float]
    u90: tuple[float, float, float]

    def __post_init__(self) -> None:
        _require_non_empty_string(self.sensor_id, "sensor_id")
        _validate_vector(self.p0_m, 3, "sensor p0_m")
        _validate_vector(self.u0, 3, "sensor u0")
        _validate_vector(self.u90, 3, "sensor u90")


@dataclass(frozen=True, slots=True)
class ObservationScene:
    """Static scene values required to render an observation stream."""

    boundary_xy_m: tuple[tuple[float, float], ...]
    floor_z_m: float
    top_z_m: float
    inlet_positions_xy_m: tuple[tuple[float, float], ...]
    sensors: tuple[ObservationSensor, ...]

    def __post_init__(self) -> None:
        if len(self.boundary_xy_m) < 3:
            raise ValueError("observation boundary must contain at least 3 coordinates")
        for coordinate in self.boundary_xy_m:
            _validate_vector(coordinate, 2, "boundary coordinate")
        if not math.isfinite(self.floor_z_m) or not math.isfinite(self.top_z_m):
            raise ValueError("observation vertical bounds must be finite")
        if self.top_z_m <= self.floor_z_m:
            raise ValueError("observation top must be above floor")
        if not self.inlet_positions_xy_m:
            raise ValueError("observation scene must contain at least 1 inlet")
        for coordinate in self.inlet_positions_xy_m:
            _validate_vector(coordinate, 2, "inlet coordinate")
        if not self.sensors:
            raise ValueError("observation scene must contain at least 1 sensor")
        sensor_ids = tuple(sensor.sensor_id for sensor in self.sensors)
        if len(sensor_ids) != len(set(sensor_ids)):
            raise ValueError("observation sensor identifiers must be unique")

    @classmethod
    def from_inputs(cls, inputs: GeneratorInputs) -> ObservationScene:
        """Copy renderable static values from validated generator inputs."""
        return cls(
            boundary_xy_m=inputs.environment.boundary_xy_m,
            floor_z_m=inputs.environment.floor_z_m,
            top_z_m=inputs.environment.top_z_m,
            inlet_positions_xy_m=inputs.generator.scenario.inlet_positions_xy_m,
            sensors=tuple(
                ObservationSensor(
                    sensor_id=sensor.sensor_id,
                    p0_m=sensor.p0_m,
                    u0=sensor.u0,
                    u90=sensor.u90,
                )
                for sensor in inputs.environment.sensors
            ),
        )


@dataclass(frozen=True, slots=True)
class ObservationStreamHeader:
    """Static run and scene context sent once for each TCP connection."""

    environment_id: str
    run_id: str
    input_fingerprint_sha256: str
    seed: int
    scene: ObservationScene

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
        if not isinstance(self.scene, ObservationScene):
            raise ValueError("observation scene must be an ObservationScene")

    def to_document(self) -> dict[str, object]:
        """Return the JSON-compatible version 1 header representation."""
        return {
            "observation_version": OBSERVATION_VERSION,
            "type": "load_model_stream_header",
            "environment_id": self.environment_id,
            "run_id": self.run_id,
            "input_fingerprint_sha256": self.input_fingerprint_sha256,
            "seed": self.seed,
            "scene": _scene_document(self.scene),
        }

    @classmethod
    def from_document(cls, value: Mapping[str, Any]) -> ObservationStreamHeader:
        """Parse one strict version 1 stream header object."""
        _require_exact_fields(value, _HEADER_FIELDS, "observation header")
        _require_version_and_type(value, "load_model_stream_header", "observation header")
        fingerprint = _require_string(
            value["input_fingerprint_sha256"],
            "observation header.input_fingerprint_sha256",
        )
        try:
            _require_fingerprint(fingerprint)
            return cls(
                environment_id=_require_string(
                    value["environment_id"], "observation header.environment_id"
                ),
                run_id=_require_string(value["run_id"], "observation header.run_id"),
                input_fingerprint_sha256=fingerprint,
                seed=_require_integer(
                    value["seed"], "observation header.seed", minimum=0, maximum=_MAX_SEED
                ),
                scene=_parse_scene(value["scene"]),
            )
        except ValueError as error:
            raise ObservationFormatError(str(error)) from error


@dataclass(frozen=True, slots=True)
class ObservationRecord:
    """One immutable observation record with its model snapshot."""

    sequence: int
    run_id: str
    snapshot: ScenarioModelSnapshot

    def __post_init__(self) -> None:
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or not 1 <= self.sequence <= _MAX_SEQUENCE
        ):
            raise ValueError("observation sequence must be a positive JSON-safe integer")
        _require_non_empty_string(self.run_id, "run_id")
        if not isinstance(self.snapshot, ScenarioModelSnapshot):
            raise ValueError("observation snapshot must be a ScenarioModelSnapshot")

    @classmethod
    def from_snapshot(
        cls,
        snapshot: ScenarioModelSnapshot,
        *,
        sequence: int,
        run_id: str,
    ) -> ObservationRecord:
        """Attach run metadata to an immutable model snapshot."""
        return cls(
            sequence=sequence,
            run_id=run_id,
            snapshot=snapshot,
        )

    def to_document(self) -> dict[str, object]:
        """Return the JSON-compatible version 1 representation."""
        state = self.snapshot.state
        surface = self.snapshot.surface
        return {
            "observation_version": OBSERVATION_VERSION,
            "type": "load_model_observation",
            "sequence": self.sequence,
            "run_id": self.run_id,
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
        _require_version_and_type(value, "load_model_observation", "observation")

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

        return cls(
            sequence=_require_integer(
                value["sequence"],
                "observation.sequence",
                minimum=1,
                maximum=_MAX_SEQUENCE,
            ),
            run_id=_require_string(value["run_id"], "observation.run_id"),
            snapshot=ScenarioModelSnapshot(state=state, surface=surface),
        )


def encode_observation_line(record: ObservationRecord) -> str:
    """Encode one observation record as a compact JSON line."""
    return json.dumps(
        record.to_document(), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )


def encode_observation_header_line(header: ObservationStreamHeader) -> str:
    """Encode one stream header as a compact JSON line."""
    return json.dumps(
        header.to_document(), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )


def encode_observation_frame(
    record: ObservationRecord | ObservationStreamHeader,
    *,
    max_line_bytes: int = MAX_OBSERVATION_LINE_BYTES,
) -> bytes:
    """Encode one LF-terminated record within the wire-size bound."""
    if isinstance(max_line_bytes, bool) or not isinstance(max_line_bytes, int):
        raise ValueError("observation line bound must be an integer")
    if not 1 <= max_line_bytes <= MAX_OBSERVATION_LINE_BYTES:
        raise ValueError(
            f"observation line bound must be between 1 and {MAX_OBSERVATION_LINE_BYTES} bytes"
        )
    frame = (
        json.dumps(
            record.to_document(), ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
        + b"\n"
    )
    if len(frame) > max_line_bytes:
        raise ObservationFormatError(
            f"observation line exceeds {max_line_bytes} bytes: {len(frame)}"
        )
    return frame


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


def decode_observation_header_line(line: str) -> ObservationStreamHeader:
    """Decode one JSON Lines stream header with strict object and number checks."""
    try:
        value = json.loads(
            line,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_non_finite_constant,
        )
    except (json.JSONDecodeError, ObservationFormatError) as error:
        raise ObservationFormatError(f"invalid observation header JSON: {error}") from error
    if not isinstance(value, dict):
        raise ObservationFormatError("observation header line must contain an object")
    return ObservationStreamHeader.from_document(value)


def _scene_document(scene: ObservationScene) -> dict[str, object]:
    return {
        "coordinate_system": "right-handed-z-up",
        "length_unit": "m",
        "angle_unit": "deg",
        "boundary_xy_m": [list(coordinate) for coordinate in scene.boundary_xy_m],
        "floor_z_m": scene.floor_z_m,
        "top_z_m": scene.top_z_m,
        "inlet_positions_xy_m": [list(coordinate) for coordinate in scene.inlet_positions_xy_m],
        "sensors": [
            {
                "sensor_id": sensor.sensor_id,
                "p0_m": list(sensor.p0_m),
                "u0": list(sensor.u0),
                "u90": list(sensor.u90),
            }
            for sensor in scene.sensors
        ],
    }


def _parse_scene(value: Any) -> ObservationScene:
    scene = _require_mapping(value, "observation header.scene")
    _require_exact_fields(scene, _SCENE_FIELDS, "observation header.scene")
    if scene["coordinate_system"] != "right-handed-z-up":
        raise ObservationFormatError(
            "observation header.scene.coordinate_system must be right-handed-z-up"
        )
    if scene["length_unit"] != "m":
        raise ObservationFormatError("observation header.scene.length_unit must be m")
    if scene["angle_unit"] != "deg":
        raise ObservationFormatError("observation header.scene.angle_unit must be deg")
    sensor_values = _require_list(scene["sensors"], "observation header.scene.sensors")
    if not sensor_values:
        raise ObservationFormatError("observation header.scene.sensors must not be empty")
    return ObservationScene(
        boundary_xy_m=_require_coordinate2_list(
            scene["boundary_xy_m"],
            "observation header.scene.boundary_xy_m",
            minimum_items=3,
        ),
        floor_z_m=_require_number(scene["floor_z_m"], "observation header.scene.floor_z_m"),
        top_z_m=_require_number(scene["top_z_m"], "observation header.scene.top_z_m"),
        inlet_positions_xy_m=_require_coordinate2_list(
            scene["inlet_positions_xy_m"],
            "observation header.scene.inlet_positions_xy_m",
            minimum_items=1,
        ),
        sensors=tuple(
            _parse_observation_sensor(sensor, index) for index, sensor in enumerate(sensor_values)
        ),
    )


def _require_version_and_type(value: Mapping[str, Any], expected_type: str, path: str) -> None:
    if (
        isinstance(value["observation_version"], bool)
        or value["observation_version"] != OBSERVATION_VERSION
    ):
        raise ObservationFormatError(f"{path}.observation_version must be 1")
    if value["type"] != expected_type:
        raise ObservationFormatError(f"{path}.type must be {expected_type}")


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


def _require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ObservationFormatError(f"{path} must be an array")
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


def _require_coordinate2_list(
    value: Any,
    path: str,
    *,
    minimum_items: int,
) -> tuple[tuple[float, float], ...]:
    items = _require_list(value, path)
    if len(items) < minimum_items:
        raise ObservationFormatError(f"{path} must contain at least {minimum_items} coordinates")
    coordinates: list[tuple[float, float]] = []
    for index, item in enumerate(items):
        coordinates.append(_require_coordinate2(item, f"{path}[{index}]"))
    return tuple(coordinates)


def _parse_observation_sensor(value: Any, index: int) -> ObservationSensor:
    path = f"observation header.scene.sensors[{index}]"
    sensor = _require_mapping(value, path)
    _require_exact_fields(sensor, _SENSOR_FIELDS, path)
    return ObservationSensor(
        sensor_id=_require_string(sensor["sensor_id"], f"{path}.sensor_id"),
        p0_m=_require_coordinate3(sensor["p0_m"], f"{path}.p0_m"),
        u0=_require_coordinate3(sensor["u0"], f"{path}.u0"),
        u90=_require_coordinate3(sensor["u90"], f"{path}.u90"),
    )


def _require_coordinate2(value: Any, path: str) -> tuple[float, float]:
    coordinate = _require_list(value, path)
    if len(coordinate) != 2:
        raise ObservationFormatError(f"{path} must contain exactly 2 numbers")
    return (
        _require_number(coordinate[0], f"{path}[0]"),
        _require_number(coordinate[1], f"{path}[1]"),
    )


def _require_coordinate3(value: Any, path: str) -> tuple[float, float, float]:
    coordinate = _require_list(value, path)
    if len(coordinate) != 3:
        raise ObservationFormatError(f"{path} must contain exactly 3 numbers")
    return (
        _require_number(coordinate[0], f"{path}[0]"),
        _require_number(coordinate[1], f"{path}[1]"),
        _require_number(coordinate[2], f"{path}[2]"),
    )


def _require_non_empty_string(value: str, path: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"observation {path} must be a non-empty string")


def _validate_vector(value: tuple[float, ...], size: int, path: str) -> None:
    if len(value) != size or any(not math.isfinite(component) for component in value):
        raise ValueError(f"observation {path} must contain exactly {size} finite numbers")


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
