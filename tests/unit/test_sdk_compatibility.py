"""Tests for RPLIDAR SDK HQ representation compatibility."""

from collections.abc import Callable

import numpy as np
import pytest
from numpy.typing import NDArray

from scrap_monitoring_lidar_simulator.measurement.sdk_compatibility import (
    HQ_ANGLE_STEP_DEG,
    HQ_DISTANCE_STEP_M,
    quantize_hq_angles_deg,
    quantize_hq_distances_m,
)


def test_angles_use_nearest_q14_value_and_wrap_the_last_tick() -> None:
    source = np.array((0.0, HQ_ANGLE_STEP_DEG * 1.1, 359.999), dtype=np.float64)

    result = quantize_hq_angles_deg(source)

    np.testing.assert_array_equal(result, (0.0, HQ_ANGLE_STEP_DEG, 0.0))
    np.testing.assert_array_equal(source, (0.0, HQ_ANGLE_STEP_DEG * 1.1, 359.999))


def test_distances_use_nearest_quarter_millimeter_value() -> None:
    source = np.array((0.0, 0.05001, 1.2346, 30.0), dtype=np.float64)

    result = quantize_hq_distances_m(source)

    np.testing.assert_array_equal(result, (0.0, 0.05, 1.2345, 30.0))
    np.testing.assert_array_equal(source, (0.0, 0.05001, 1.2346, 30.0))
    np.testing.assert_allclose(
        result / HQ_DISTANCE_STEP_M,
        np.rint(result / HQ_DISTANCE_STEP_M),
        atol=1e-12,
    )


@pytest.mark.parametrize(
    ("quantizer", "values", "message"),
    [
        (quantize_hq_angles_deg, np.array(((0.0,),)), "1D"),
        (quantize_hq_angles_deg, np.array((float("nan"),)), "finite"),
        (quantize_hq_angles_deg, np.array((360.0,)), r"\[0, 360\)"),
        (quantize_hq_distances_m, np.array(((0.0,),)), "1D"),
        (quantize_hq_distances_m, np.array((float("inf"),)), "finite"),
        (quantize_hq_distances_m, np.array((-0.1,)), "non-negative"),
    ],
)
def test_rejects_values_outside_hq_input_domain(
    quantizer: Callable[[NDArray[np.float64]], NDArray[np.float64]],
    values: NDArray[np.float64],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        quantizer(values)
