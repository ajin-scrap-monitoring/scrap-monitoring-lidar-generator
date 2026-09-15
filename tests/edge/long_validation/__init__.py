"""Bounded collection and evaluation helpers for the edge soak-test matrix."""

from .evaluator import (
    EXPECTED_LATENCY_SAMPLES,
    EXPECTED_RESOURCE_SAMPLES,
    EXPECTED_STATUS_SAMPLES,
    evaluate_matrix,
    evaluate_run,
    nearest_rank,
)

__all__ = [
    "EXPECTED_LATENCY_SAMPLES",
    "EXPECTED_RESOURCE_SAMPLES",
    "EXPECTED_STATUS_SAMPLES",
    "evaluate_matrix",
    "evaluate_run",
    "nearest_rank",
]
