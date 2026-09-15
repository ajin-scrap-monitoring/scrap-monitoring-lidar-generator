"""Count valid observation records without retaining their payloads."""

import argparse
import asyncio
import hashlib
import json
import math
import os
import signal
import sys
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from .run_case import RunnerError, verify_repository_checkout

_MAX_RECORD_BYTES = 1_048_576
_DEFAULT_MAX_OBSERVATIONS = 7_200
_EXPECTED_SENSOR_IDS = {"lidar_1", "lidar_2"}
_STOP_DISCONNECT_GRACE_S = 5.0
_EXPECTED_TOTAL_DURATION_S = 3_900.0
_EXPECTED_OBSERVATION_INTERVAL_S = 1.0
_EXPECTED_OBSERVATIONS = 3_901
_FLOAT_TOLERANCE = 1e-9
_CONTRACT_ROOT = Path(__file__).resolve().parents[3] / "contracts" / "observation" / "v1"


def _load_contract_validator(name: str) -> Draft202012Validator:
    try:
        schema = json.loads((_CONTRACT_ROOT / name).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot load observation contract schema {name}") from error
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


_HEADER_VALIDATOR = _load_contract_validator("header.schema.json")
_OBSERVATION_VALIDATOR = _load_contract_validator("observation.schema.json")


class ReceiverError(ValueError):
    """Raised when receiver input or stream data violates the local contract."""


@dataclass(frozen=True, slots=True)
class ObservationExpectation:
    """Public-input-derived identity and cadence for one official receiver run."""

    generator_source_commit: str
    header_without_run_id: Mapping[str, object]
    scene_fingerprint_sha256: str
    expected_observations: int
    first_elapsed_s: float
    last_elapsed_s: float
    interval_s: float
    floor_z_m: float
    top_z_m: float
    capacity_m3: float
    inlet_count: int
    cell_size_m: float
    x_coordinates_m: tuple[float, ...]
    y_coordinates_m: tuple[float, ...]

    @classmethod
    def from_header(
        cls,
        header: Mapping[str, object],
        *,
        generator_source_commit: str,
        expected_observations: int,
        first_elapsed_s: float,
        last_elapsed_s: float,
        interval_s: float,
        cell_size_m: float,
    ) -> ObservationExpectation:
        if expected_observations <= 0:
            raise ReceiverError("expected observation count must be positive")
        if not 0.0 < first_elapsed_s <= last_elapsed_s:
            raise ReceiverError("expected observation time range is invalid")
        if not math.isfinite(interval_s) or interval_s <= 0.0:
            raise ReceiverError("expected observation interval must be finite and positive")
        if not math.isfinite(cell_size_m) or cell_size_m <= 0.0:
            raise ReceiverError("expected observation cell size must be finite and positive")
        comparable = dict(header)
        comparable.pop("run_id", None)
        scene = _mapping(comparable.get("scene"), "expected header.scene")
        boundary = _coordinate_pairs(scene.get("boundary_xy_m"), "expected boundary")
        inlets = _coordinate_pairs(scene.get("inlet_positions_xy_m"), "expected inlets")
        floor_z_m = _finite_number(scene.get("floor_z_m"), "expected floor_z_m")
        top_z_m = _finite_number(scene.get("top_z_m"), "expected top_z_m")
        if top_z_m <= floor_z_m:
            raise ReceiverError("expected scene top must be above its floor")
        x_coordinates_m = _grid_axis(
            min(point[0] for point in boundary),
            max(point[0] for point in boundary),
            cell_size_m,
        )
        y_coordinates_m = _grid_axis(
            min(point[1] for point in boundary),
            max(point[1] for point in boundary),
            cell_size_m,
        )
        scene_fingerprint = _canonical_sha256(scene)
        return cls(
            generator_source_commit=generator_source_commit,
            header_without_run_id=comparable,
            scene_fingerprint_sha256=scene_fingerprint,
            expected_observations=expected_observations,
            first_elapsed_s=first_elapsed_s,
            last_elapsed_s=last_elapsed_s,
            interval_s=interval_s,
            floor_z_m=floor_z_m,
            top_z_m=top_z_m,
            capacity_m3=_polygon_area(boundary) * (top_z_m - floor_z_m),
            inlet_count=len(inlets),
            cell_size_m=cell_size_m,
            x_coordinates_m=x_coordinates_m,
            y_coordinates_m=y_coordinates_m,
        )


def load_observation_expectation(
    repository: Path,
    generator_source_commit: str,
    mean_fill_duration_s: int,
) -> ObservationExpectation:
    """Derive exact receiver expectations from one verified public checkout."""
    try:
        verify_repository_checkout(repository, generator_source_commit)
        generator = _load_json_object(
            repository / "examples" / "generator.v2.json", "generator configuration"
        )
        environment = _load_json_object(
            repository / "examples" / "environment.v1.json", "environment configuration"
        )
        quality = _load_json_object(
            repository / "examples" / "quality-profile.v1.json", "quality configuration"
        )
        header = _expected_header(generator, environment, quality, mean_fill_duration_s)
        measurement = _mapping(generator.get("measurement"), "generator.measurement")
        scenario = _mapping(generator.get("scenario"), "generator.scenario")
        rotation_rate_hz = _finite_number(
            measurement.get("rotation_rate_hz"), "measurement.rotation_rate_hz"
        )
        cell_size_m = _finite_number(
            _mapping(scenario.get("surface"), "scenario.surface").get("cell_size_m"),
            "scenario.surface.cell_size_m",
        )
    except (KeyError, OSError, RunnerError, TypeError, ValueError) as error:
        raise ReceiverError("cannot derive observation expectations from public input") from error
    first_elapsed_s = 1.0 / rotation_rate_hz
    return ObservationExpectation.from_header(
        header,
        generator_source_commit=generator_source_commit,
        expected_observations=_EXPECTED_OBSERVATIONS,
        first_elapsed_s=first_elapsed_s,
        last_elapsed_s=_EXPECTED_TOTAL_DURATION_S,
        interval_s=_EXPECTED_OBSERVATION_INTERVAL_S,
        cell_size_m=cell_size_m,
    )


def _mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ReceiverError(f"{path} must be an object")
    return cast(Mapping[str, object], value)


def _nonempty_string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReceiverError(f"{path} must be a non-empty string")
    return value


def _positive_integer(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ReceiverError(f"{path} must be a positive integer")
    return value


def _nonnegative_integer(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReceiverError(f"{path} must be a non-negative integer")
    return value


def _finite_number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReceiverError(f"{path} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ReceiverError(f"{path} must be finite")
    return result


def _coordinate_pairs(value: object, path: str) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, list) or not value:
        raise ReceiverError(f"{path} must be a non-empty coordinate array")
    result: list[tuple[float, float]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, list) or len(raw) != 2:
            raise ReceiverError(f"{path}[{index}] must contain two coordinates")
        result.append(
            (
                _finite_number(raw[0], f"{path}[{index}][0]"),
                _finite_number(raw[1], f"{path}[{index}][1]"),
            )
        )
    return tuple(result)


def _number_array(value: object, path: str) -> list[float]:
    if not isinstance(value, list) or len(value) < 2:
        raise ReceiverError(f"{path} must contain at least two numbers")
    result = [_finite_number(item, f"{path}[]") for item in value]
    if any(right <= left for left, right in pairwise(result)):
        raise ReceiverError(f"{path} must be strictly increasing")
    return result


def _validate_height_rows(
    value: object,
    *,
    x_count: int,
    y_count: int,
    floor_z_m: float,
    top_z_m: float,
) -> None:
    if not isinstance(value, list) or len(value) != y_count:
        raise ReceiverError("surface.heights_m row count differs from the y grid")
    for row_index, raw_row in enumerate(value):
        if not isinstance(raw_row, list) or len(raw_row) != x_count:
            raise ReceiverError("surface.heights_m column count differs from the x grid")
        for column_index, raw_height in enumerate(raw_row):
            height = _finite_number(raw_height, f"surface.heights_m[{row_index}][{column_index}]")
            if not floor_z_m - _FLOAT_TOLERANCE <= height <= top_z_m + _FLOAT_TOLERANCE:
                raise ReceiverError("observation surface height is outside the scene bounds")


def _polygon_area(vertices: Sequence[tuple[float, float]]) -> float:
    area_twice = sum(
        first[0] * second[1] - second[0] * first[1]
        for first, second in zip(vertices, (*vertices[1:], vertices[0]), strict=True)
    )
    area = abs(area_twice) / 2.0
    if not math.isfinite(area) or area <= 0.0:
        raise ReceiverError("expected boundary must have a finite positive area")
    return area


def _grid_axis(start: float, end: float, cell_size_m: float) -> tuple[float, ...]:
    cell_count = max(1, math.ceil((end - start) / cell_size_m))
    return tuple(start + cell_size_m * index for index in range(cell_count + 1))


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_json_object(path: Path, label: str) -> Mapping[str, object]:
    try:
        return _mapping(json.loads(path.read_text(encoding="utf-8")), label)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReceiverError(f"cannot load {label}") from error


def _float_tree(value: object, path: str) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return _finite_number(value, path)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [_float_tree(item, f"{path}[]") for item in value]
    if isinstance(value, dict):
        return {str(key): _float_tree(item, f"{path}.{key}") for key, item in value.items()}
    raise ReceiverError(f"{path} contains an unsupported value")


def _quality_frequency_array(value: object, path: str) -> list[int]:
    frequencies = [0] * 256
    source = _mapping(value, path)
    for raw_quality, raw_frequency in source.items():
        try:
            quality = int(raw_quality)
        except (TypeError, ValueError) as error:
            raise ReceiverError(f"{path} contains an invalid quality key") from error
        if str(quality) != raw_quality or not 0 <= quality <= 255:
            raise ReceiverError(f"{path} contains an invalid quality key")
        if isinstance(raw_frequency, bool) or not isinstance(raw_frequency, int):
            raise ReceiverError(f"{path}.{raw_quality} must be an integer")
        if raw_frequency <= 0:
            raise ReceiverError(f"{path}.{raw_quality} must be positive")
        frequencies[quality] = raw_frequency
    return frequencies


def _normalized_environment(document: Mapping[str, object]) -> dict[str, object]:
    boundary = _coordinate_pairs(document.get("boundary_xy_m"), "environment.boundary_xy_m")
    sensors_value = document.get("sensors")
    if not isinstance(sensors_value, list) or not sensors_value:
        raise ReceiverError("environment.sensors must be a non-empty array")
    sensors: list[dict[str, object]] = []
    for index, raw_sensor in enumerate(sensors_value):
        sensor = _mapping(raw_sensor, f"environment.sensors[{index}]")
        sensors.append(
            {
                "sensor_id": _nonempty_string(
                    sensor.get("sensor_id"), f"environment.sensors[{index}].sensor_id"
                ),
                "p0_m": list(
                    _number_vector(sensor.get("p0_m"), 3, f"environment.sensors[{index}].p0_m")
                ),
                "u0": list(_number_vector(sensor.get("u0"), 3, f"environment.sensors[{index}].u0")),
                "u90": list(
                    _number_vector(sensor.get("u90"), 3, f"environment.sensors[{index}].u90")
                ),
            }
        )
    return {
        "environment_id": _nonempty_string(
            document.get("environment_id"), "environment.environment_id"
        ),
        "boundary_xy_m": [list(point) for point in boundary],
        "floor_z_m": _finite_number(document.get("floor_z_m"), "environment.floor_z_m"),
        "top_z_m": _finite_number(document.get("top_z_m"), "environment.top_z_m"),
        "sensors": sensors,
    }


def _normalized_quality(document: Mapping[str, object]) -> dict[str, object]:
    sensors_value = document.get("sensors")
    if not isinstance(sensors_value, list) or not sensors_value:
        raise ReceiverError("quality.sensors must be a non-empty array")
    sensors: list[dict[str, object]] = []
    for index, raw_sensor in enumerate(sensors_value):
        sensor = _mapping(raw_sensor, f"quality.sensors[{index}]")
        sensors.append(
            {
                "sensor_id": _nonempty_string(
                    sensor.get("sensor_id"), f"quality.sensors[{index}].sensor_id"
                ),
                "valid_distance_frequencies": _quality_frequency_array(
                    sensor.get("valid_distance_frequencies"),
                    f"quality.sensors[{index}].valid_distance_frequencies",
                ),
                "invalid_distance_frequencies": _quality_frequency_array(
                    sensor.get("invalid_distance_frequencies"),
                    f"quality.sensors[{index}].invalid_distance_frequencies",
                ),
            }
        )
    return {"sensors": sensors}


def _number_vector(value: object, length: int, path: str) -> tuple[float, ...]:
    if not isinstance(value, list) or len(value) != length:
        raise ReceiverError(f"{path} must contain {length} numbers")
    return tuple(_finite_number(item, f"{path}[]") for item in value)


def _expected_header(
    generator: Mapping[str, object],
    environment_document: Mapping[str, object],
    quality_document: Mapping[str, object],
    mean_fill_duration_s: int,
) -> dict[str, object]:
    if mean_fill_duration_s not in (600, 86_400):
        raise ReceiverError("mean fill duration must be 600 or 86400")
    environment = _normalized_environment(environment_document)
    scenario = _mapping(generator.get("scenario"), "generator.scenario")
    normalized_scenario = _mapping(_float_tree(dict(scenario), "generator.scenario"), "scenario")
    normalized_scenario = dict(normalized_scenario)
    normalized_scenario["mean_fill_duration_s"] = float(mean_fill_duration_s)
    measurement = _mapping(generator.get("measurement"), "generator.measurement")
    seed = generator.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**64 - 1:
        raise ReceiverError("generator.seed must be an unsigned 64-bit integer")
    fingerprint = _canonical_sha256(
        {
            "seed": seed,
            "scenario": normalized_scenario,
            "measurement": _float_tree(dict(measurement), "generator.measurement"),
            "environment": environment,
            "quality_profile": _normalized_quality(quality_document),
        }
    )
    inlets = _coordinate_pairs(
        scenario.get("inlet_positions_xy_m"), "generator.scenario.inlet_positions_xy_m"
    )
    scene = {
        "coordinate_system": "right-handed-z-up",
        "length_unit": "m",
        "angle_unit": "deg",
        "boundary_xy_m": environment["boundary_xy_m"],
        "floor_z_m": environment["floor_z_m"],
        "top_z_m": environment["top_z_m"],
        "inlet_positions_xy_m": [list(point) for point in inlets],
        "sensors": environment["sensors"],
    }
    return {
        "observation_version": 1,
        "type": "load_model_stream_header",
        "environment_id": environment["environment_id"],
        "run_id": "expected-runtime-run",
        "input_fingerprint_sha256": fingerprint,
        "seed": seed,
        "scene": scene,
    }


def _atomic_write_json(path: Path, document: Mapping[str, object], *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = json.dumps(document, allow_nan=False, sort_keys=True) + "\n"
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path)
    except FileExistsError as error:
        raise ReceiverError(f"output already exists: {path.name}") from error
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


class ObservationReceiver:
    """Validate one logical observation run across bounded TCP reconnects."""

    def __init__(
        self,
        *,
        expectation: ObservationExpectation,
        max_observations: int,
        max_connections: int,
    ) -> None:
        if max_observations <= 0 or max_connections <= 0:
            raise ReceiverError("receiver limits must be positive")
        if max_observations < expectation.expected_observations:
            raise ReceiverError("receiver record limit is below the expected observation count")
        self.expectation = expectation
        self.max_observations = max_observations
        self.max_connections = max_connections
        self.received_records = 0
        self.connection_count = 0
        self.completed_connections = 0
        self.active_connections = 0
        self.run_id: str | None = None
        self.last_sequence = 0
        self.first_elapsed_s: float | None = None
        self.last_elapsed_s: float | None = None
        self.last_cycle_index: int | None = None
        self.last_phase: str | None = None
        self.last_surface_updated_at_s: float | None = None
        self.last_surface_volume_m3: float | None = None
        self.last_surface_sha256: bytes | None = None
        self.surface_change_count = 0
        self.minimum_surface_volume_m3: float | None = None
        self.maximum_surface_volume_m3: float | None = None
        self.surface_stream_hasher = hashlib.sha256()
        self.header_fingerprint: bytes | None = None
        self.failure: ReceiverError | None = None
        self.stop = asyncio.Event()
        self._writers: set[asyncio.StreamWriter] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        self._writers.add(writer)
        self.active_connections += 1
        self.connection_count += 1
        try:
            await self._read_connection(reader)
        except (OSError, ReceiverError) as error:
            if self.failure is None:
                self.failure = (
                    error
                    if isinstance(error, ReceiverError)
                    else ReceiverError("observation connection I/O failed")
                )
            self.stop.set()
        finally:
            self.active_connections -= 1
            self._writers.discard(writer)
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
            if task is not None:
                self._tasks.discard(task)

    async def _read_connection(self, reader: asyncio.StreamReader) -> None:
        if self.active_connections != 1:
            raise ReceiverError("concurrent observation connections are not supported")
        if self.connection_count > self.max_connections:
            raise ReceiverError("observation connection limit exceeded")
        header_seen = False
        while not self.stop.is_set():
            line = await self._read_line(reader)
            if line is None:
                break
            document = self._decode(line)
            if not header_seen:
                self._accept_header(document)
                header_seen = True
            else:
                self._accept_observation(document)
        if not header_seen:
            raise ReceiverError("observation connection ended before its header")
        self.completed_connections += 1

    @staticmethod
    async def _read_line(reader: asyncio.StreamReader) -> bytes | None:
        try:
            line = await reader.readline()
        except ValueError as error:
            raise ReceiverError("observation record exceeds 1 MiB") from error
        if not line:
            return None
        if len(line) > _MAX_RECORD_BYTES or not line.endswith(b"\n"):
            raise ReceiverError("observation record exceeds 1 MiB or lacks LF framing")
        if line == b"\n":
            raise ReceiverError("observation record must not be empty")
        return line[:-1]

    @staticmethod
    def _decode(payload: bytes) -> Mapping[str, object]:
        try:
            document = _mapping(
                json.loads(payload, parse_constant=_reject_json_constant),
                "observation record",
            )
            _reject_nonfinite_numbers(document)
            return document
        except (RecursionError, UnicodeError, json.JSONDecodeError, ReceiverError) as error:
            raise ReceiverError("observation record is not valid UTF-8 JSON") from error

    def _accept_header(self, document: Mapping[str, object]) -> None:
        _validate_contract(_HEADER_VALIDATOR, document, "observation header")
        run_id = _nonempty_string(document["run_id"], "header.run_id")
        fingerprint = _nonempty_string(
            document["input_fingerprint_sha256"], "header.input_fingerprint_sha256"
        )
        if len(fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in fingerprint
        ):
            raise ReceiverError("header.input_fingerprint_sha256 must be lowercase SHA-256 hex")
        seed = document["seed"]
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**64 - 1:
            raise ReceiverError("header.seed is outside the unsigned 64-bit range")
        scene = _mapping(document["scene"], "header.scene")
        sensors = scene.get("sensors")
        if not isinstance(sensors, list):
            raise ReceiverError("header.scene.sensors must be an array")
        sensor_ids = {
            _nonempty_string(_mapping(sensor, "header sensor").get("sensor_id"), "sensor_id")
            for sensor in sensors
        }
        if sensor_ids != _EXPECTED_SENSOR_IDS or len(sensors) != 2:
            raise ReceiverError("observation header must contain lidar_1 and lidar_2 exactly once")
        comparable = dict(document)
        del comparable["run_id"]
        if comparable != self.expectation.header_without_run_id:
            raise ReceiverError("observation header differs from the expected public input")
        if _canonical_sha256(scene) != self.expectation.scene_fingerprint_sha256:
            raise ReceiverError("observation scene fingerprint differs from the expected input")
        canonical = json.dumps(document, allow_nan=False, sort_keys=True, separators=(",", ":"))
        header_fingerprint = hashlib.sha256(canonical.encode("utf-8")).digest()
        if self.run_id is None:
            self.run_id = run_id
            self.header_fingerprint = header_fingerprint
        elif self.run_id != run_id or self.header_fingerprint != header_fingerprint:
            raise ReceiverError("observation reconnect header identity changed")

    def _accept_observation(self, document: Mapping[str, object]) -> None:
        _validate_contract(_OBSERVATION_VALIDATOR, document, "observation record")
        if _nonempty_string(document["run_id"], "observation.run_id") != self.run_id:
            raise ReceiverError("observation run_id differs from its header")
        sequence = _positive_integer(document["sequence"], "observation.sequence")
        if sequence > 9_007_199_254_740_991:
            raise ReceiverError("observation.sequence exceeds the JSON-safe integer range")
        expected_sequence = self.last_sequence + 1
        if sequence != expected_sequence:
            raise ReceiverError("observation sequence is not continuous from one")
        if self.received_records >= self.max_observations:
            raise ReceiverError("observation record limit exceeded")
        surface_payload = json.dumps(
            document["surface"],
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        surface_sha256 = hashlib.sha256(surface_payload).digest()
        elapsed_s = self._validate_observation_semantics(document, sequence, surface_sha256)
        self.surface_stream_hasher.update(sequence.to_bytes(8, byteorder="big"))
        self.surface_stream_hasher.update(len(surface_payload).to_bytes(8, byteorder="big"))
        self.surface_stream_hasher.update(surface_payload)
        if self.first_elapsed_s is None:
            self.first_elapsed_s = elapsed_s
        self.last_elapsed_s = elapsed_s
        self.last_sequence = sequence
        self.received_records += 1

    def _validate_observation_semantics(
        self,
        document: Mapping[str, object],
        sequence: int,
        surface_sha256: bytes,
    ) -> float:
        state = _mapping(document.get("scenario"), "observation.scenario")
        surface = _mapping(document.get("surface"), "observation.surface")
        elapsed_s = _finite_number(state.get("elapsed_s"), "scenario.elapsed_s")
        surface_updated_at_s = _finite_number(
            state.get("surface_updated_at_s"), "scenario.surface_updated_at_s"
        )
        phase_started_at_s = _finite_number(
            state.get("phase_started_at_s"), "scenario.phase_started_at_s"
        )
        phase_ends_at_s = _finite_number(state.get("phase_ends_at_s"), "scenario.phase_ends_at_s")
        phase_duration_s = _finite_number(
            state.get("phase_duration_s"), "scenario.phase_duration_s"
        )
        expected_elapsed_s = (
            self.expectation.first_elapsed_s
            if sequence == 1
            else (sequence - 1) * self.expectation.interval_s
        )
        if not math.isclose(
            elapsed_s,
            expected_elapsed_s,
            rel_tol=1e-12,
            abs_tol=_FLOAT_TOLERANCE,
        ):
            raise ReceiverError("observation simulation time differs from the one-second cadence")
        if elapsed_s > self.expectation.last_elapsed_s + _FLOAT_TOLERANCE:
            raise ReceiverError("observation simulation time exceeds the validation duration")
        if not (
            surface_updated_at_s <= elapsed_s + _FLOAT_TOLERANCE
            and phase_started_at_s <= elapsed_s + _FLOAT_TOLERANCE
            and elapsed_s <= phase_ends_at_s + _FLOAT_TOLERANCE
            and math.isclose(
                phase_ends_at_s - phase_started_at_s,
                phase_duration_s,
                rel_tol=1e-12,
                abs_tol=_FLOAT_TOLERANCE,
            )
        ):
            raise ReceiverError("observation scenario time fields are inconsistent")
        phase = _nonempty_string(state.get("phase"), "scenario.phase")
        if phase not in ("filling", "collecting"):
            raise ReceiverError("observation phase is invalid")
        cycle_index = _nonnegative_integer(state.get("cycle_index"), "scenario.cycle_index")
        current_inlet_index = state.get("current_inlet_index")
        if phase == "filling":
            if (
                isinstance(current_inlet_index, bool)
                or not isinstance(current_inlet_index, int)
                or not 0 <= current_inlet_index < self.expectation.inlet_count
            ):
                raise ReceiverError("filling observation has an invalid inlet index")
        elif current_inlet_index is not None:
            raise ReceiverError("collecting observation must not have an inlet index")
        self._validate_phase_progression(cycle_index, phase)
        cell_size_m = _finite_number(surface.get("cell_size_m"), "surface.cell_size_m")
        if not math.isclose(
            cell_size_m,
            self.expectation.cell_size_m,
            rel_tol=1e-12,
            abs_tol=_FLOAT_TOLERANCE,
        ):
            raise ReceiverError("observation surface cell size differs from public input")
        x_coordinates = _number_array(surface.get("x_coordinates_m"), "surface.x_coordinates_m")
        y_coordinates = _number_array(surface.get("y_coordinates_m"), "surface.y_coordinates_m")
        self._validate_axis(
            x_coordinates,
            self.expectation.x_coordinates_m,
            "x",
        )
        self._validate_axis(
            y_coordinates,
            self.expectation.y_coordinates_m,
            "y",
        )
        _validate_height_rows(
            surface.get("heights_m"),
            x_count=len(x_coordinates),
            y_count=len(y_coordinates),
            floor_z_m=self.expectation.floor_z_m,
            top_z_m=self.expectation.top_z_m,
        )
        self._validate_surface_progression(
            phase=phase,
            cycle_index=cycle_index,
            surface_updated_at_s=surface_updated_at_s,
            surface_volume_m3=_finite_number(
                state.get("surface_volume_m3"), "scenario.surface_volume_m3"
            ),
            surface_fill_ratio=_finite_number(
                state.get("surface_fill_ratio"), "scenario.surface_fill_ratio"
            ),
            surface_sha256=surface_sha256,
        )
        self.last_cycle_index = cycle_index
        self.last_phase = phase
        return elapsed_s

    def _validate_surface_progression(
        self,
        *,
        phase: str,
        cycle_index: int,
        surface_updated_at_s: float,
        surface_volume_m3: float,
        surface_fill_ratio: float,
        surface_sha256: bytes,
    ) -> None:
        expected_fill_ratio = surface_volume_m3 / self.expectation.capacity_m3
        if not math.isclose(
            surface_fill_ratio,
            expected_fill_ratio,
            rel_tol=1e-10,
            abs_tol=_FLOAT_TOLERANCE,
        ):
            raise ReceiverError("observation surface volume and fill ratio are inconsistent")
        volume_tolerance_m3 = max(1.0, self.expectation.capacity_m3) * 1e-10
        if self.last_surface_updated_at_s is not None:
            if surface_updated_at_s <= self.last_surface_updated_at_s + _FLOAT_TOLERANCE:
                raise ReceiverError("observation surface update time did not advance")
            if surface_sha256 == self.last_surface_sha256:
                raise ReceiverError("observation surface payload did not change")
            if self.last_surface_volume_m3 is None:
                raise ReceiverError("observation surface volume history is incomplete")
            if cycle_index == self.last_cycle_index and phase == self.last_phase:
                self._validate_same_phase_volume_change(
                    surface_volume_m3,
                    phase,
                    volume_tolerance_m3,
                )
            self.surface_change_count += 1
        self.last_surface_updated_at_s = surface_updated_at_s
        self.last_surface_volume_m3 = surface_volume_m3
        self.last_surface_sha256 = surface_sha256
        self.minimum_surface_volume_m3 = min(
            surface_volume_m3,
            self.minimum_surface_volume_m3
            if self.minimum_surface_volume_m3 is not None
            else surface_volume_m3,
        )
        self.maximum_surface_volume_m3 = max(
            surface_volume_m3,
            self.maximum_surface_volume_m3
            if self.maximum_surface_volume_m3 is not None
            else surface_volume_m3,
        )

    def _validate_same_phase_volume_change(
        self,
        current_volume_m3: float,
        phase: str,
        tolerance_m3: float,
    ) -> None:
        if self.last_surface_volume_m3 is None:
            raise ReceiverError("observation surface volume history is incomplete")
        volume_change_m3 = current_volume_m3 - self.last_surface_volume_m3
        if phase == "filling" and volume_change_m3 <= tolerance_m3:
            raise ReceiverError("filling observation surface volume did not increase")
        if phase == "collecting" and volume_change_m3 >= -tolerance_m3:
            raise ReceiverError("collecting observation surface volume did not decrease")

    def _validate_phase_progression(self, cycle_index: int, phase: str) -> None:
        if self.last_cycle_index is None:
            if cycle_index != 0 or phase != "filling":
                raise ReceiverError("observation stream must begin in filling cycle zero")
        elif phase == self.last_phase:
            if cycle_index != self.last_cycle_index:
                raise ReceiverError("observation cycle changed without a phase transition")
        elif self.last_phase == "filling" and phase == "collecting":
            if cycle_index != self.last_cycle_index:
                raise ReceiverError("collecting transition changed the cycle index")
        elif self.last_phase == "collecting" and phase == "filling":
            if cycle_index != self.last_cycle_index + 1:
                raise ReceiverError("filling transition did not advance the cycle index")
        else:
            raise ReceiverError("observation phase progression is invalid")

    @staticmethod
    def _validate_axis(actual: list[float], expected: tuple[float, ...], label: str) -> None:
        if len(actual) != len(expected) or any(
            not math.isclose(value, reference, rel_tol=1e-12, abs_tol=_FLOAT_TOLERANCE)
            for value, reference in zip(actual, expected, strict=True)
        ):
            raise ReceiverError(f"observation surface {label} grid differs from public input")

    async def close_clients(self) -> None:
        for writer in tuple(self._writers):
            writer.close()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    def result(self) -> dict[str, object]:
        if self.failure is not None:
            raise self.failure
        if self.active_connections != 0:
            raise ReceiverError("receiver was stopped with an active observation connection")
        if self.completed_connections == 0 or self.received_records == 0:
            raise ReceiverError("receiver did not complete a connection with observations")
        if self.received_records != self.expectation.expected_observations:
            raise ReceiverError("receiver did not receive the exact expected observation count")
        if self.first_elapsed_s is None or self.last_elapsed_s is None:
            raise ReceiverError("receiver did not retain observation time boundaries")
        if not math.isclose(
            self.last_elapsed_s,
            self.expectation.last_elapsed_s,
            rel_tol=1e-12,
            abs_tol=_FLOAT_TOLERANCE,
        ):
            raise ReceiverError("receiver did not reach the expected simulation end")
        expected_changes = self.expectation.expected_observations - 1
        if self.surface_change_count != expected_changes:
            raise ReceiverError("receiver did not verify every expected surface change")
        if self.minimum_surface_volume_m3 is None or self.maximum_surface_volume_m3 is None:
            raise ReceiverError("receiver did not retain surface volume boundaries")
        if (
            expected_changes > 0
            and self.maximum_surface_volume_m3 - self.minimum_surface_volume_m3
            <= max(1.0, self.expectation.capacity_m3) * 1e-10
        ):
            raise ReceiverError("receiver did not observe a changing surface volume")
        return {
            "schema_version": "long-validation-observation-result.v1",
            "run_id": self.run_id,
            "generator_source_commit": self.expectation.generator_source_commit,
            "environment_id": self.expectation.header_without_run_id["environment_id"],
            "input_fingerprint_sha256": self.expectation.header_without_run_id[
                "input_fingerprint_sha256"
            ],
            "seed": self.expectation.header_without_run_id["seed"],
            "scene_fingerprint_sha256": self.expectation.scene_fingerprint_sha256,
            "received_records": self.received_records,
            "first_sequence": 1,
            "last_sequence": self.last_sequence,
            "first_elapsed_s": self.first_elapsed_s,
            "last_elapsed_s": self.last_elapsed_s,
            "surface_stream_sha256": self.surface_stream_hasher.hexdigest(),
            "surface_change_count": self.surface_change_count,
            "minimum_surface_volume_m3": self.minimum_surface_volume_m3,
            "maximum_surface_volume_m3": self.maximum_surface_volume_m3,
        }


async def _watch_stop_file(path: Path, receiver: ObservationReceiver) -> None:
    while not receiver.stop.is_set():
        if path.is_file():
            deadline = asyncio.get_running_loop().time() + _STOP_DISCONNECT_GRACE_S
            while (
                receiver.active_connections
                and receiver.failure is None
                and asyncio.get_running_loop().time() < deadline
            ):
                await asyncio.sleep(0.05)
            receiver.stop.set()
            return
        await asyncio.sleep(0.2)


def _reject_json_constant(value: str) -> object:
    raise ReceiverError(f"non-finite JSON constant is not allowed: {value}")


def _validate_contract(
    validator: Draft202012Validator,
    document: Mapping[str, object],
    label: str,
) -> None:
    try:
        validator.validate(document)
    except ValidationError as error:
        raise ReceiverError(f"{label} differs from the version 1 contract") from error


def _reject_nonfinite_numbers(value: object) -> None:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, float) and not math.isfinite(current):
            raise ReceiverError("non-finite JSON number is not allowed")
        if isinstance(current, dict):
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)


def _validate_receiver_paths(arguments: argparse.Namespace) -> None:
    if len({arguments.output, arguments.ready_file, arguments.stop_file}) != 3:
        raise ReceiverError("receiver control and output paths must be distinct")
    for path in (arguments.output, arguments.ready_file):
        if path.exists() or path.is_symlink():
            raise ReceiverError(f"output already exists: {path.name}")
    if arguments.stop_file.exists() or arguments.stop_file.is_symlink():
        raise ReceiverError("stop file must not exist when the receiver starts")


async def run_receiver(
    arguments: argparse.Namespace,
    *,
    expectation: ObservationExpectation | None = None,
) -> dict[str, object]:
    """Run until the coordinator creates the stop file or sends a termination signal."""
    _validate_receiver_paths(arguments)
    if expectation is None:
        expectation = load_observation_expectation(
            arguments.repository,
            arguments.generator_source_commit,
            arguments.mean_fill_duration_s,
        )
    receiver = ObservationReceiver(
        expectation=expectation,
        max_observations=arguments.max_observations,
        max_connections=arguments.max_connections,
    )
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(signal_number, receiver.stop.set)
    server = await asyncio.start_server(
        receiver.handle,
        arguments.bind_host,
        arguments.port,
        limit=_MAX_RECORD_BYTES + 1,
    )
    if server.sockets is None or len(server.sockets) != 1:
        server.close()
        await server.wait_closed()
        raise ReceiverError("receiver requires exactly one listening socket")
    selected_port = int(server.sockets[0].getsockname()[1])
    _atomic_write_json(
        arguments.ready_file,
        {
            "schema_version": "long-validation-observation-ready.v1",
            "port": selected_port,
        },
        mode=0o600,
    )
    stop_task = asyncio.create_task(_watch_stop_file(arguments.stop_file, receiver))
    try:
        await receiver.stop.wait()
    finally:
        server.close()
        await server.wait_closed()
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)
        for signal_number in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.remove_signal_handler(signal_number)
    stopped_with_active_connection = receiver.active_connections != 0
    if stopped_with_active_connection:
        await receiver.close_clients()
    if stopped_with_active_connection and receiver.failure is None:
        raise ReceiverError("receiver was stopped with an active observation connection")
    return receiver.result()


def _positive_int(value: str) -> int:
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return result


def _port(value: str) -> int:
    result = int(value)
    if not 0 <= result <= 65_535:
        raise argparse.ArgumentTypeError("port must be from 0 through 65535")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Count validated observation JSON Lines without retaining payloads.",
        epilog=(
            "Start this receiver before the edge case. After the edge handoff request appears, "
            "create --stop-file, wait for --output, and copy that JSON atomically to the edge "
            "handoff path as observation-result.staged.json."
        ),
    )
    parser.add_argument("--bind-host", required=True)
    parser.add_argument("--port", type=_port, default=17_000)
    parser.add_argument("--ready-file", required=True, type=Path)
    parser.add_argument("--stop-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--generator-source-commit", required=True)
    parser.add_argument("--mean-fill-duration-s", required=True, type=int, choices=(600, 86_400))
    parser.add_argument("--max-observations", type=_positive_int, default=_DEFAULT_MAX_OBSERVATIONS)
    parser.add_argument("--max-connections", type=_positive_int, default=64)
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path(__file__).resolve().parents[3],
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        result = asyncio.run(run_receiver(arguments))
        _atomic_write_json(arguments.output, result, mode=0o644)
    except (OSError, ReceiverError) as error:
        print(f"observation receiver failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
