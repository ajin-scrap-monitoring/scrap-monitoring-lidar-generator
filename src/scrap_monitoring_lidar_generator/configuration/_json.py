"""Strict JSON parsing and primitive validation helpers."""

import json
import math
from pathlib import Path
from typing import Any, Never

from scrap_monitoring_lidar_generator.configuration.errors import ConfigurationError


def read_document(path: str | Path, description: str) -> str:
    """Read a UTF-8 configuration document."""
    source = Path(path)
    try:
        return source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ConfigurationError(f"cannot read {description}: {source}") from error


def parse_document(document: str) -> Any:
    """Parse strict JSON while rejecting duplicate fields and non-finite numbers."""
    try:
        return json.loads(
            document,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_non_finite_constant,
        )
    except ConfigurationError:
        raise
    except json.JSONDecodeError as error:
        raise ConfigurationError(
            f"invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}"
        ) from error


def require_exact_fields(value: dict[str, Any], expected: frozenset[str], path: str) -> None:
    """Require exactly the named object fields."""
    actual = frozenset(value)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing:
        raise ConfigurationError(f"{path} is missing fields: {', '.join(missing)}")
    if unexpected:
        raise ConfigurationError(f"{path} contains unexpected fields: {', '.join(unexpected)}")


def require_object(value: Any, path: str) -> dict[str, Any]:
    """Require a JSON object."""
    if not isinstance(value, dict):
        raise ConfigurationError(f"{path} must be an object")
    return value


def require_array(value: Any, path: str) -> list[Any]:
    """Require a JSON array."""
    if not isinstance(value, list):
        raise ConfigurationError(f"{path} must be an array")
    return value


def require_non_empty_string(value: Any, path: str) -> str:
    """Require a non-empty string."""
    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"{path} must be a non-empty string")
    return value


def require_literal(value: Any, expected: int | str, path: str) -> None:
    """Require an exact scalar value without boolean and integer equivalence."""
    if isinstance(value, bool) or value != expected:
        raise ConfigurationError(f"{path} must be {expected!r}")


def require_number(value: Any, path: str) -> float:
    """Require a finite JSON number and convert it to float."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigurationError(f"{path} must be a number")
    try:
        result = float(value)
    except OverflowError as error:
        raise ConfigurationError(f"{path} must be finite") from error
    if not math.isfinite(result):
        raise ConfigurationError(f"{path} must be finite")
    return result


def require_integer(
    value: Any,
    path: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Require an integer within optional inclusive bounds."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{path} must be an integer")
    if minimum is not None and value < minimum:
        raise ConfigurationError(f"{path} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigurationError(f"{path} must be at most {maximum}")
    return value


def require_boolean(value: Any, path: str) -> bool:
    """Require a boolean."""
    if not isinstance(value, bool):
        raise ConfigurationError(f"{path} must be a boolean")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_non_finite_constant(value: str) -> Never:
    raise ConfigurationError(f"non-finite JSON number is not allowed: {value}")
