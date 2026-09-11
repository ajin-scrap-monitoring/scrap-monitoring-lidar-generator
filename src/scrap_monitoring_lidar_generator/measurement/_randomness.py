"""Stable sensor-specific random stream derivation."""

import hashlib

MAX_SEED = 18_446_744_073_709_551_615


def require_seed(seed: int, name: str) -> int:
    """Validate and return an unsigned 64-bit seed."""
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= MAX_SEED:
        raise ValueError(f"{name} must be an unsigned 64-bit integer")
    return seed


def derive_sensor_seed(seed: int, sensor_id: str, stream_name: str) -> int:
    """Derive one stable 128-bit seed from a run seed, sensor, and responsibility."""
    require_seed(seed, "measurement seed")
    if not sensor_id:
        raise ValueError("measurement sensor_id must be non-empty")
    stream_bytes = stream_name.encode("ascii")
    sensor_bytes = sensor_id.encode("utf-8")
    payload = (
        b"measurement\0"
        + seed.to_bytes(8, byteorder="big")
        + len(sensor_bytes).to_bytes(8, byteorder="big")
        + sensor_bytes
        + stream_bytes
    )
    return int.from_bytes(hashlib.sha256(payload).digest()[:16], byteorder="big")
