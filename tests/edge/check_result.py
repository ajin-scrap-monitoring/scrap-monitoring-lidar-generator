"""Validate edge generator and receiver logs."""

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from scrap_monitoring_lidar_generator.configuration import load_environment


def _parse_key_values(line: str, prefix: str) -> dict[str, str]:
    if not line.startswith(prefix):
        raise ValueError(f"missing {prefix.strip()} result line")
    result: dict[str, str] = {}
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
    result: dict[str, int] = {}
    for field in fields:
        try:
            value = int(values[field])
        except (KeyError, ValueError) as error:
            raise ValueError(f"{name} field {field} must be an integer") from error
        if value < 0:
            raise ValueError(f"{name} field {field} must be non-negative")
        result[field] = value
    return result


def _require_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _mapping_of_ints(value: object, name: str) -> dict[str, int]:
    source = _require_mapping(value, name)
    result: dict[str, int] = {}
    for key, item in source.items():
        if not isinstance(key, str) or isinstance(item, bool) or not isinstance(item, int):
            raise ValueError(f"{name} must map strings to integers")
        result[key] = item
    return result


def validate_result(
    *,
    environment_path: Path,
    generator_log_path: Path,
    receiver_log_path: Path,
    max_pending_frames: int,
) -> str:
    """Validate clean two-sensor delivery and return a compact report."""
    environment = load_environment(environment_path)
    expected_sensor_ids = tuple(sensor.sensor_id for sensor in environment.sensors)
    if len(expected_sensor_ids) != 2:
        raise ValueError("edge validation requires exactly 2 configured sensors")
    if max_pending_frames < 0:
        raise ValueError("max pending frames must be non-negative")

    generator_lines = generator_log_path.read_text(encoding="utf-8").splitlines()
    run_values = _parse_key_values(_find_last_line(generator_lines, "run_id="), "")
    transport_values = _parse_key_values(
        _find_last_line(generator_lines, "transport "), "transport "
    )
    observation_values = _parse_key_values(_find_last_line(generator_lines, "observation="), "")
    run = _integers(run_values, ("generated", "acknowledged", "pending"), "run")
    transport = _integers(
        transport_values,
        (
            "enqueued",
            "sent",
            "acknowledged",
            "rejected",
            "expired",
            "capacity_discarded",
            "oversized",
            "connection_failures",
            "pending_frames",
            "pending_bytes",
        ),
        "transport",
    )
    observation = _integers(
        observation_values,
        ("sent", "dropped", "connection_failures"),
        "observation",
    )

    if run["generated"] != transport["enqueued"]:
        raise ValueError("generated and enqueued scan counts differ")
    if run["acknowledged"] != transport["acknowledged"]:
        raise ValueError("run and transport acknowledged counts differ")
    if run["pending"] != transport["pending_frames"]:
        raise ValueError("run and transport pending counts differ")
    if transport["enqueued"] - transport["acknowledged"] != transport["pending_frames"]:
        raise ValueError("transport pending count does not match enqueued minus acknowledged")
    failure_fields = (
        "rejected",
        "expired",
        "capacity_discarded",
        "oversized",
        "connection_failures",
    )
    if any(transport[field] != 0 for field in failure_fields):
        raise ValueError("scan transport reported rejection, discard or connection failure")
    if transport["pending_frames"] > max_pending_frames:
        raise ValueError("scan transport pending count exceeds the validation bound")
    if (transport["pending_frames"] == 0) != (transport["pending_bytes"] == 0):
        raise ValueError("pending frame and byte counters are inconsistent")
    if observation["dropped"] != 0 or observation["connection_failures"] != 0:
        raise ValueError("observation transport reported a drop or connection failure")

    receiver_lines = receiver_log_path.read_text(encoding="utf-8").splitlines()
    receiver_line = _find_last_line(receiver_lines, "validation_receiver=")
    document = _require_mapping(
        json.loads(receiver_line.removeprefix("validation_receiver=")),
        "receiver result",
    )
    if document.get("environment_id") != environment.environment_id:
        raise ValueError("receiver environment_id differs from the configured environment")
    if document.get("run_id") != run_values.get("run_id"):
        raise ValueError("receiver run_id differs from the generator run_id")
    if document.get("expected_sensor_ids") != list(expected_sensor_ids):
        raise ValueError("receiver expected sensors differ from the configured sensors")
    errors = document.get("errors")
    if errors != []:
        raise ValueError(f"receiver reported errors: {errors}")

    connections = _mapping_of_ints(document.get("scan_connections"), "scan_connections")
    unique = _mapping_of_ints(document.get("scan_unique"), "scan_unique")
    if set(connections) != set(expected_sensor_ids) or any(
        value < 1 for value in connections.values()
    ):
        raise ValueError("receiver did not accept a scan connection for each sensor")
    if set(unique) != set(expected_sensor_ids) or any(value < 1 for value in unique.values()):
        raise ValueError("receiver did not receive scans from each sensor")
    unique_total = sum(unique.values())
    if not transport["acknowledged"] <= unique_total <= transport["enqueued"]:
        raise ValueError("receiver unique scan count is outside the sender delivery range")
    if document.get("scan_duplicates") != 0:
        raise ValueError("receiver observed a duplicate scan")
    if document.get("scan_received") != unique_total:
        raise ValueError("receiver scan count differs from its unique scan count")
    if document.get("scan_gaps") != 0:
        raise ValueError("receiver observed a scan sequence gap")
    if (
        not isinstance(document.get("observation_headers"), int)
        or document["observation_headers"] < 1
    ):
        raise ValueError("receiver did not receive an observation header")
    if not isinstance(document.get("observations"), int) or document["observations"] < 1:
        raise ValueError("receiver did not receive a dynamic observation")
    if document.get("observation_gaps") != 0:
        raise ValueError("receiver observed an observation sequence gap")
    if document["observations"] != observation["sent"]:
        raise ValueError("receiver and sender observation counts differ")

    return (
        f"edge_validation=passed sensors=2 generated={run['generated']} "
        f"acknowledged={run['acknowledged']} pending={run['pending']} "
        f"observations={document['observations']}"
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the result checker argument parser."""
    parser = argparse.ArgumentParser(description="Check an edge release validation result.")
    parser.add_argument("--environment", required=True, type=Path)
    parser.add_argument("--generator-log", required=True, type=Path)
    parser.add_argument("--receiver-log", required=True, type=Path)
    parser.add_argument("--max-pending-frames", type=int, default=2)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Check result files and print the successful summary."""
    arguments = build_parser().parse_args(argv)
    try:
        report = validate_result(
            environment_path=arguments.environment,
            generator_log_path=arguments.generator_log,
            receiver_log_path=arguments.receiver_log,
            max_pending_frames=arguments.max_pending_frames,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"edge_validation=failed reason={error}")
        return 1
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
