"""Validate edge generator, subscriber and status outputs."""

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from scrap_monitoring_lidar_simulator.configuration import load_environment


def _parse_key_values(line: str, prefix: str) -> dict[str, str]:
    if not line.startswith(prefix):
        raise ValueError(f"missing {prefix.strip()} result line")
    result = {}
    for item in line[len(prefix) :].split():
        key, separator, value = item.partition("=")
        if not separator or not key or not value or key in result:
            raise ValueError(f"invalid {prefix.strip()} result field: {item}")
        result[key] = value
    return result


def _find_last_line(lines: list[str], prefix: str) -> str:
    for line in reversed(lines):
        if line.startswith(prefix):
            return line
    raise ValueError(f"missing {prefix.strip()} result line")


def _integers(values: Mapping[str, str], fields: Sequence[str], name: str) -> dict[str, int]:
    try:
        result = {field: int(values[field]) for field in fields}
    except (KeyError, ValueError) as error:
        raise ValueError(f"{name} contains an invalid integer") from error
    if any(value < 0 for value in result.values()):
        raise ValueError(f"{name} counters must be non-negative")
    return result


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _mapping_of_ints(value: object, name: str) -> dict[str, int]:
    source = _mapping(value, name)
    if any(
        not isinstance(key, str) or isinstance(item, bool) or not isinstance(item, int)
        for key, item in source.items()
    ):
        raise ValueError(f"{name} must map strings to integers")
    return cast(dict[str, int], source)


@dataclass(frozen=True, slots=True)
class _GeneratorResult:
    run_id: str | None
    run: Mapping[str, int]
    stream: Mapping[str, int]
    observation: Mapping[str, int]


def _load_generator(path: Path) -> _GeneratorResult:
    lines = path.read_text(encoding="utf-8").splitlines()
    run_values = _parse_key_values(_find_last_line(lines, "run_id="), "")
    stream_values = _parse_key_values(_find_last_line(lines, "scan_stream "), "scan_stream ")
    observation_values = _parse_key_values(_find_last_line(lines, "observation="), "")
    return _GeneratorResult(
        run_id=run_values.get("run_id"),
        run=_integers(run_values, ("generated", "published"), "run"),
        stream=_integers(stream_values, ("published", "frame_loss", "subscribers"), "stream"),
        observation=_integers(
            observation_values,
            ("sent", "dropped", "connection_failures"),
            "observation",
        ),
    )


def _load_receiver(path: Path) -> Mapping[str, Any]:
    line = _find_last_line(path.read_text(encoding="utf-8").splitlines(), "validation_receiver=")
    return _mapping(json.loads(line.removeprefix("validation_receiver=")), "receiver")


def _validate_statuses(
    status_directory: Path,
    *,
    expected_sensor_ids: tuple[str, ...],
    edge_id: str,
    config_revision: str,
) -> None:
    for index, sensor_id in enumerate(expected_sensor_ids):
        service = f"lidar-driver-{'a' if index == 0 else 'b'}"
        status = _mapping(
            json.loads(
                (status_directory / service / f"{service}.json").read_text(encoding="utf-8")
            ),
            f"{service} status",
        )
        expected = {
            "schema_version": "1.0",
            "service": service,
            "sensor_id": sensor_id,
            "edge_id": edge_id,
            "config_revision": config_revision,
            "state": "HEALTHY",
        }
        if any(status.get(name) != value for name, value in expected.items()):
            raise ValueError(f"{service} status identity or state mismatch")
        if status.get("reason_codes") != [] or not status.get("instance_id"):
            raise ValueError(f"{service} status reported an error")


def _validate_generator(generator: _GeneratorResult, *, sensor_count: int) -> None:
    if generator.run["published"] != generator.stream["published"]:
        raise ValueError("run and scan stream published counts differ")
    if generator.run["generated"] - generator.run["published"] != sensor_count:
        raise ValueError("one scan per sensor was not reserved for scan_hz measurement")
    if generator.stream["frame_loss"] != 0:
        raise ValueError("scan stream reported frame loss")
    if generator.observation["dropped"] or generator.observation["connection_failures"]:
        raise ValueError("observation stream reported loss or connection failure")


def _validate_receiver(
    receiver: Mapping[str, Any],
    *,
    environment_id: str,
    run_id: str | None,
    sensor_ids: tuple[str, ...],
    published: int,
    observations_sent: int,
) -> int:
    if receiver.get("environment_id") != environment_id:
        raise ValueError("receiver environment identity mismatch")
    if receiver.get("run_id") != run_id:
        raise ValueError("observation run identity mismatch")
    if receiver.get("expected_sensor_ids") != list(sensor_ids) or receiver.get("errors") != []:
        raise ValueError("receiver sensor identity or errors mismatch")
    subscriptions = _mapping_of_ints(receiver.get("scan_subscriptions"), "scan_subscriptions")
    unique = _mapping_of_ints(receiver.get("scan_unique"), "scan_unique")
    if set(subscriptions) != set(sensor_ids) or any(value < 1 for value in subscriptions.values()):
        raise ValueError("receiver did not subscribe to both sensors")
    if set(unique) != set(sensor_ids) or any(value < 1 for value in unique.values()):
        raise ValueError("receiver did not receive both sensors")
    if receiver.get("scan_duplicates") != 0 or receiver.get("scan_gaps") != 0:
        raise ValueError("receiver observed duplicate or missing scan sequences")
    received = sum(unique.values())
    if receiver.get("scan_received") != received or received > published:
        raise ValueError("receiver scan count is inconsistent with published frames")
    _validate_observations(receiver, sent=observations_sent)
    return received


def _validate_observations(receiver: Mapping[str, Any], *, sent: int) -> None:
    if not isinstance(receiver.get("observations"), int) or receiver["observations"] < 1:
        raise ValueError("receiver did not receive an observation")
    if receiver.get("observation_gaps") != 0:
        raise ValueError("receiver observed an observation sequence gap")
    if receiver["observations"] != sent:
        raise ValueError("receiver and generator observation counts differ")


def validate_result(
    *,
    environment_path: Path,
    generator_log_path: Path,
    receiver_log_path: Path,
    status_directory: Path,
) -> str:
    """Validate clean two-sensor delivery and return a compact report."""
    environment = load_environment(environment_path)
    sensor_ids = tuple(sensor.sensor_id for sensor in environment.sensors)
    generator = _load_generator(generator_log_path)
    receiver = _load_receiver(receiver_log_path)
    _validate_generator(generator, sensor_count=len(sensor_ids))
    received = _validate_receiver(
        receiver,
        environment_id=environment.environment_id,
        run_id=generator.run_id,
        sensor_ids=sensor_ids,
        published=generator.run["published"],
        observations_sent=generator.observation["sent"],
    )
    _validate_statuses(
        status_directory,
        expected_sensor_ids=sensor_ids,
        edge_id=str(receiver.get("edge_id")),
        config_revision=str(receiver.get("config_revision")),
    )
    return (
        f"edge_validation=passed sensors=2 generated={generator.run['generated']} "
        f"published={generator.run['published']} received={received} "
        f"observations={receiver['observations']}"
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the result checker argument parser."""
    parser = argparse.ArgumentParser(description="Check an edge release validation result.")
    parser.add_argument("--environment", required=True, type=Path)
    parser.add_argument("--generator-log", required=True, type=Path)
    parser.add_argument("--receiver-log", required=True, type=Path)
    parser.add_argument("--status-dir", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Check result files and print the successful summary."""
    arguments = build_parser().parse_args(argv)
    try:
        report = validate_result(
            environment_path=arguments.environment,
            generator_log_path=arguments.generator_log,
            receiver_log_path=arguments.receiver_log,
            status_directory=arguments.status_dir,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"edge_validation=failed reason={error}")
        return 1
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
