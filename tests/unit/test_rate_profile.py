"""Tests for exact-average smooth rate profiles."""

import random
from itertools import pairwise

import pytest

from scrap_monitoring_lidar_generator.scenario import (
    SmoothRateProfile,
    SmoothRateSegment,
    create_smooth_rate_profile,
)


def test_random_profile_stays_bounded_and_preserves_average() -> None:
    profile = create_smooth_rate_profile(
        duration_s=37.0,
        factor_range=(0.4, 1.8),
        change_duration_s_range=(2.0, 4.0),
        rng=random.Random(1234),
    )

    factors = [profile.factor_at(37.0 * index / 1000) for index in range(1001)]

    assert min(factors) >= 0.4
    assert max(factors) <= 1.8
    assert any(factor != 1.0 for factor in factors)
    assert profile.integrated_factor_between(0.0, 37.0) == pytest.approx(37.0)


def test_integrals_are_additive_across_rate_segments() -> None:
    profile = create_smooth_rate_profile(
        duration_s=23.0,
        factor_range=(0.5, 1.5),
        change_duration_s_range=(1.5, 3.0),
        rng=random.Random(9876),
    )

    partitions = (0.0, 1.25, 4.75, 11.0, 19.5, 23.0)
    total = sum(
        profile.integrated_factor_between(start, end) for start, end in pairwise(partitions)
    )

    assert total == pytest.approx(23.0)


def test_same_random_state_produces_identical_profile() -> None:
    profiles = [
        create_smooth_rate_profile(
            duration_s=20.0,
            factor_range=(0.2, 1.4),
            change_duration_s_range=(1.0, 2.0),
            rng=random.Random(42),
        )
        for _ in range(2)
    ]

    assert profiles[0] == profiles[1]


def test_one_sided_factor_range_produces_flat_exact_profile() -> None:
    profile = create_smooth_rate_profile(
        duration_s=10.0,
        factor_range=(1.0, 1.5),
        change_duration_s_range=(1.0, 2.0),
        rng=random.Random(1),
    )

    assert profile.segments == ()
    assert profile.factor_at(5.0) == 1.0
    assert profile.integrated_factor_between(2.0, 7.0) == 5.0


def test_rejects_nonzero_mean_deviation() -> None:
    segment = SmoothRateSegment(start_s=0.0, duration_s=2.0, deviation=0.5)

    with pytest.raises(ValueError, match="zero integral"):
        SmoothRateProfile(duration_s=2.0, segments=(segment,))


@pytest.mark.parametrize(
    ("factor_range", "change_range"),
    [
        ((1.1, 1.5), (1.0, 2.0)),
        ((0.5, 0.9), (1.0, 2.0)),
        ((0.5, 1.5), (0.0, 2.0)),
        ((0.5, 1.5), (2.0, 1.0)),
    ],
)
def test_rejects_invalid_profile_ranges(
    factor_range: tuple[float, float],
    change_range: tuple[float, float],
) -> None:
    with pytest.raises(ValueError):
        create_smooth_rate_profile(
            duration_s=10.0,
            factor_range=factor_range,
            change_duration_s_range=change_range,
            rng=random.Random(1),
        )
