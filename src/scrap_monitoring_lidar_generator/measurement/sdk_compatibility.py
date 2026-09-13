"""RPLIDAR HQ node representation helpers without an SDK dependency."""

import numpy as np
from numpy.typing import NDArray

type FloatArray = NDArray[np.float64]

HQ_ANGLE_STEPS_PER_ROTATION = 1 << 16
HQ_ANGLE_STEP_DEG = 360.0 / HQ_ANGLE_STEPS_PER_ROTATION
HQ_DISTANCE_STEPS_PER_METER = 4_000
HQ_DISTANCE_STEP_M = 1.0 / HQ_DISTANCE_STEPS_PER_METER


def quantize_hq_angles_deg(angles_deg: FloatArray) -> FloatArray:
    """Return nearest values representable by SDK HQ ``angle_z_q14``."""
    result = np.array(angles_deg, dtype=np.float64, copy=True)
    if result.ndim != 1:
        raise ValueError("HQ angles must be a 1D array")
    if not bool(np.all(np.isfinite(result))):
        raise ValueError("HQ angles must be finite")
    if bool(np.any(result < 0.0)) or bool(np.any(result >= 360.0)):
        raise ValueError("HQ angles must be in [0, 360)")

    result /= HQ_ANGLE_STEP_DEG
    result += 0.5
    np.floor(result, out=result)
    np.remainder(result, HQ_ANGLE_STEPS_PER_ROTATION, out=result)
    result *= HQ_ANGLE_STEP_DEG
    return result


def quantize_hq_distances_m(distances_m: FloatArray) -> FloatArray:
    """Return nearest values representable by SDK HQ ``dist_mm_q2``."""
    result = np.array(distances_m, dtype=np.float64, copy=True)
    if result.ndim != 1:
        raise ValueError("HQ distances must be a 1D array")
    if not bool(np.all(np.isfinite(result))) or bool(np.any(result < 0.0)):
        raise ValueError("HQ distances must be finite and non-negative")

    result *= HQ_DISTANCE_STEPS_PER_METER
    result += 0.5
    np.floor(result, out=result)
    result /= HQ_DISTANCE_STEPS_PER_METER
    return result
