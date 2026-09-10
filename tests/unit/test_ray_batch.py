"""Tests for validated ray batches."""

import numpy as np
import pytest

from scrap_monitoring_lidar_generator.geometry import Ray, RayBatch, Vec3


def test_builds_read_only_batch_in_ray_order() -> None:
    rays = (
        Ray(Vec3(1.0, 2.0, 3.0), Vec3(0.0, 0.0, -1.0)),
        Ray(Vec3(4.0, 5.0, 6.0), Vec3(1.0, 0.0, 0.0)),
    )

    batch = RayBatch.from_rays(rays)

    assert batch.count == 2
    np.testing.assert_array_equal(batch.origins_m, ((1.0, 2.0, 3.0), (4.0, 5.0, 6.0)))
    np.testing.assert_array_equal(batch.directions, ((0.0, 0.0, -1.0), (1.0, 0.0, 0.0)))
    assert batch.origins_m.flags.writeable is False
    assert batch.directions.flags.writeable is False


def test_copies_input_arrays() -> None:
    origins_m = np.array(((1.0, 2.0, 3.0),), dtype=np.float64)
    directions = np.array(((0.0, 0.0, -1.0),), dtype=np.float64)

    batch = RayBatch(origins_m, directions)
    origins_m[:] = 9.0
    directions[:] = (1.0, 0.0, 0.0)

    np.testing.assert_array_equal(batch.origins_m, ((1.0, 2.0, 3.0),))
    np.testing.assert_array_equal(batch.directions, ((0.0, 0.0, -1.0),))


def test_accepts_empty_batch() -> None:
    batch = RayBatch(
        np.empty((0, 3), dtype=np.float64),
        np.empty((0, 3), dtype=np.float64),
    )

    assert batch.count == 0


@pytest.mark.parametrize(
    ("origins_m", "directions"),
    [
        (np.zeros(3), np.zeros((1, 3))),
        (np.zeros((1, 2)), np.zeros((1, 2))),
        (np.zeros((2, 3)), np.zeros((1, 3))),
        (np.array(((float("nan"), 0.0, 0.0),)), np.array(((1.0, 0.0, 0.0),))),
        (np.zeros((1, 3)), np.array(((2.0, 0.0, 0.0),))),
    ],
)
def test_rejects_invalid_batch_arrays(
    origins_m: np.ndarray[tuple[int, ...], np.dtype[np.float64]],
    directions: np.ndarray[tuple[int, ...], np.dtype[np.float64]],
) -> None:
    with pytest.raises(ValueError):
        RayBatch(origins_m, directions)
