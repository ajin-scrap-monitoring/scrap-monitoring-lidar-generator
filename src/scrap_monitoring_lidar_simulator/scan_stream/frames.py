"""Convert generated measurements to the edge platform ScanFrame contract."""

import math
import re
import time
from collections.abc import Callable, Iterable

import numpy as np

from scrap_monitoring_lidar_simulator.measurement import MeasurementResult
from scrap_monitoring_lidar_simulator.measurement.sdk_compatibility import (
    HQ_ANGLE_STEP_DEG,
    HQ_DISTANCE_STEPS_PER_METER,
)
from scrap_monitoring_lidar_simulator.wire import lidar_pb2

_ANGLE_Q14_SCALE = 16_384
_ANGLE_MDEG_PER_QUADRANT = 90_000
_ANGLE_MDEG_PER_ROTATION = 360_000
_DISTANCE_Q2_PER_MM = 4
_NANOSECONDS_PER_SECOND = 1_000_000_000
_DRIVER_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


class ScanFrameFactory:
    """Build driver-compatible frames and per-instance sensor sequences."""

    def __init__(
        self,
        *,
        sensor_ids: Iterable[str],
        edge_id: str,
        config_revision: str,
        instance_ids: dict[str, str],
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        unix_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
    ) -> None:
        sensors = tuple(sensor_ids)
        if not sensors or len(set(sensors)) != len(sensors):
            raise ValueError("scan frame sensor identifiers must be non-empty and unique")
        if set(instance_ids) != set(sensors) or any(not value for value in instance_ids.values()):
            raise ValueError("scan frame instance identifiers must match all sensors")
        if _DRIVER_IDENTITY.fullmatch(edge_id) is None:
            raise ValueError("scan frame edge_id must be driver-compatible")
        if _DRIVER_IDENTITY.fullmatch(config_revision) is None:
            raise ValueError("scan frame config_revision must be driver-compatible")
        self._sensor_ids = frozenset(sensors)
        self._edge_id = edge_id
        self._config_revision = config_revision
        self._instance_ids = dict(instance_ids)
        self._monotonic_ns = monotonic_ns
        self._unix_ms = unix_ms
        self._previous_monotonic_ns: dict[str, int] = {}
        self._sequences = dict.fromkeys(sensors, 0)

    def build(self, result: MeasurementResult) -> lidar_pb2.ScanFrame | None:
        """Return one completed frame, skipping the first scan used to measure its rate."""
        sensor_id = result.sensor_id
        if sensor_id not in self._sensor_ids:
            raise ValueError(f"unknown scan frame sensor_id: {sensor_id}")

        samples = _normalized_samples(result)
        acquired_monotonic_ns = self._monotonic_ns()
        acquired_at_unix_ms = self._unix_ms()
        previous = self._previous_monotonic_ns.get(sensor_id)
        self._previous_monotonic_ns[sensor_id] = acquired_monotonic_ns
        if previous is None:
            return None
        elapsed_ns = acquired_monotonic_ns - previous
        if elapsed_ns <= 0:
            raise ValueError("scan completion timestamps must increase per sensor")
        scan_hz = _NANOSECONDS_PER_SECOND / elapsed_ns
        if not math.isfinite(scan_hz) or scan_hz <= 0.0:
            raise ValueError("scan rate must be finite and positive")

        sequence = self._sequences[sensor_id] + 1
        self._sequences[sensor_id] = sequence
        return lidar_pb2.ScanFrame(
            schema_version="1.0",
            edge_id=self._edge_id,
            sensor_id=sensor_id,
            sequence=sequence,
            acquired_at_unix_ms=acquired_at_unix_ms,
            acquired_monotonic_ns=acquired_monotonic_ns,
            sdk_status="OK",
            scan_hz=scan_hz,
            samples=samples,
            instance_id=self._instance_ids[sensor_id],
            config_revision=self._config_revision,
        )


def _normalized_samples(result: MeasurementResult) -> list[lidar_pb2.ScanSample]:
    scan = result.measured.scan
    angle_steps = np.rint(scan.angles_deg / HQ_ANGLE_STEP_DEG).astype(np.uint64)
    angle_mdeg = (
        (angle_steps * _ANGLE_MDEG_PER_QUADRANT + _ANGLE_Q14_SCALE // 2) // _ANGLE_Q14_SCALE
    ) % _ANGLE_MDEG_PER_ROTATION
    distance_steps = np.rint(scan.distances_m * HQ_DISTANCE_STEPS_PER_METER).astype(np.uint64)
    distance_mm = distance_steps // _DISTANCE_Q2_PER_MM
    quality = scan.qualities.astype(np.uint32) >> 2
    order = np.argsort(angle_mdeg, kind="stable")
    return [
        lidar_pb2.ScanSample(
            angle_mdeg=int(angle_mdeg[index]),
            distance_mm=int(distance_mm[index]),
            quality=int(quality[index]),
        )
        for index in order
    ]
