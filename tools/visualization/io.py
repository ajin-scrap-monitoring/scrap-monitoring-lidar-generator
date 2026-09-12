"""Load and validate recorded observation JSON Lines."""

from pathlib import Path

from scrap_monitoring_lidar_generator.observation import (
    ObservationFormatError,
    ObservationRecord,
    decode_observation_line,
)


def load_observation_records(path: Path) -> tuple[ObservationRecord, ...]:
    """Read monotonically ordered observation records from a UTF-8 file."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ObservationFormatError(f"cannot read observation file: {path}") from error

    records: list[ObservationRecord] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = decode_observation_line(line)
        except ObservationFormatError as error:
            raise ObservationFormatError(f"observation line {line_number}: {error}") from error
        if records:
            previous = records[-1]
            if record.environment_id != previous.environment_id:
                raise ObservationFormatError("observation environment_id changed within one file")
            if record.run_id != previous.run_id:
                raise ObservationFormatError("observation run_id changed within one file")
            if record.input_fingerprint_sha256 != previous.input_fingerprint_sha256:
                raise ObservationFormatError(
                    "observation input fingerprint changed within one file"
                )
            if record.seed != previous.seed:
                raise ObservationFormatError("observation seed changed within one file")
            if record.snapshot.state.elapsed_s < previous.snapshot.state.elapsed_s:
                raise ObservationFormatError("observation simulation time must be monotonic")
        records.append(record)
    if not records:
        raise ObservationFormatError("observation file contains no records")
    return tuple(records)
