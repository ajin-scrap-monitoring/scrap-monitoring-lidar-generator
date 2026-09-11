"""Tests for deterministic full-jitter reconnect backoff."""

import pytest

from scrap_monitoring_lidar_generator.transport import ReconnectBackoff


def test_failure_doubles_cap_until_maximum() -> None:
    backoff = ReconnectBackoff(initial_delay_s=0.5, maximum_delay_s=5.0, seed=1)
    expected_caps = [0.5, 1.0, 2.0, 4.0, 5.0, 5.0]

    for failure_count, expected_cap in enumerate(expected_caps, start=1):
        assert backoff.current_delay_cap_s == expected_cap
        delay_s = backoff.next_delay_after_failure()
        assert 0.0 <= delay_s <= expected_cap
        assert backoff.consecutive_failures == failure_count


def test_normal_ack_resets_cap_but_not_random_stream() -> None:
    backoff = ReconnectBackoff(initial_delay_s=0.5, maximum_delay_s=5.0, seed=1)
    first_delay_s = backoff.next_delay_after_failure()
    backoff.next_delay_after_failure()

    backoff.reset_after_ack()

    assert backoff.current_delay_cap_s == 0.5
    assert backoff.consecutive_failures == 0
    assert backoff.next_delay_after_failure() != first_delay_s


def test_same_seed_produces_same_jitter_sequence() -> None:
    first = ReconnectBackoff(initial_delay_s=0.5, maximum_delay_s=5.0, seed=123)
    second = ReconnectBackoff(initial_delay_s=0.5, maximum_delay_s=5.0, seed=123)

    assert [first.next_delay_after_failure() for _ in range(10)] == [
        second.next_delay_after_failure() for _ in range(10)
    ]


def test_transport_seed_domain_is_stable() -> None:
    backoff = ReconnectBackoff(initial_delay_s=0.5, maximum_delay_s=5.0, seed=123)

    assert [backoff.next_delay_after_failure() for _ in range(3)] == pytest.approx(
        [0.3100664873699944, 0.11316388265572797, 1.7942534034740267]
    )


@pytest.mark.parametrize(
    ("initial_delay_s", "maximum_delay_s", "seed"),
    [
        (0.0, 5.0, 1),
        (float("inf"), 5.0, 1),
        (True, 5.0, 1),
        (0.5, 0.0, 1),
        (0.5, 5.0, -1),
        (0.5, 5.0, True),
        (5.1, 5.0, 1),
    ],
)
def test_rejects_invalid_backoff_settings(
    initial_delay_s: float,
    maximum_delay_s: float,
    seed: int,
) -> None:
    with pytest.raises(ValueError):
        ReconnectBackoff(
            initial_delay_s=initial_delay_s,
            maximum_delay_s=maximum_delay_s,
            seed=seed,
        )
