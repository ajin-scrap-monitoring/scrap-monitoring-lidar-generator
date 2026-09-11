"""Simulation-time coordination for undistorted reference scans."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from scrap_monitoring_lidar_generator.configuration import (
    GeneratorInputs,
    build_environment_scene,
    build_sensor_frame,
)
from scrap_monitoring_lidar_generator.geometry import EnvironmentScene
from scrap_monitoring_lidar_generator.measurement import (
    ReferencePoint,
    ReferenceScan,
    ReferenceScanner,
    ScheduledScan,
    SensorRotationScheduler,
    TimedReferenceScan,
)
from scrap_monitoring_lidar_generator.runtime.measurement import build_rotation_schedulers
from scrap_monitoring_lidar_generator.runtime.scenario import build_scenario_simulator
from scrap_monitoring_lidar_generator.scenario import ScenarioSimulator


@dataclass(slots=True)
class _PendingReferenceScan:
    scheduler: SensorRotationScheduler
    scanner: ReferenceScanner
    schedule: ScheduledScan
    next_point_index: int = 0
    points: list[ReferencePoint] = field(default_factory=list)


class ScenarioTimeObserver(Protocol):
    """Read scenario intervals before the shared simulator advances."""

    def advance_to(self, elapsed_s: float, *, scenario: ScenarioSimulator) -> None:
        """Observe one interval that does not cross a scenario phase boundary."""
        ...


class ReferenceGenerationRuntime:
    """Observe one shared scenario in time order for all sensor rotations."""

    __slots__ = ("_observers", "_pending", "_scenario", "_scene")

    def __init__(
        self,
        *,
        scenario: ScenarioSimulator,
        scene: EnvironmentScene,
        scanners: tuple[ReferenceScanner, ...],
        schedulers: tuple[SensorRotationScheduler, ...],
        observers: Sequence[ScenarioTimeObserver] = (),
    ) -> None:
        if scenario.elapsed_s != 0.0:
            raise ValueError("reference runtime scenario must start at simulation time 0")
        if scene.dynamic_surface is not scenario.surface:
            raise ValueError("reference runtime scene must use the scenario height field")
        if not scanners or not schedulers:
            raise ValueError("reference runtime must contain at least one sensor")

        scanners_by_id = _unique_by_sensor_id(scanners, "scanner")
        schedulers_by_id = _unique_by_sensor_id(schedulers, "scheduler")
        if scanners_by_id.keys() != schedulers_by_id.keys():
            raise ValueError("reference runtime scanner and scheduler sensor sets must match")
        if any(scheduler.next_scan_id != 1 for scheduler in schedulers):
            raise ValueError("reference runtime schedulers must start at scan_id 1")

        self._scenario = scenario
        self._scene = scene
        self._observers = tuple(observers)
        self._pending = [
            _PendingReferenceScan(
                scheduler=schedulers_by_id[sensor_id],
                scanner=scanners_by_id[sensor_id],
                schedule=schedulers_by_id[sensor_id].next_scan(),
            )
            for sensor_id in sorted(scanners_by_id)
        ]

    @property
    def scenario(self) -> ScenarioSimulator:
        """Return the shared scenario advanced by this runtime."""
        return self._scenario

    @property
    def sensor_ids(self) -> tuple[str, ...]:
        """Return the coordinated sensor identifiers in stable order."""
        return tuple(pending.scanner.sensor_id for pending in self._pending)

    @property
    def next_completion_elapsed_s(self) -> float:
        """Return the earliest pending sensor rotation completion time."""
        return min(pending.schedule.completed_at_s for pending in self._pending)

    @property
    def earliest_pending_elapsed_s(self) -> float:
        """Return the earliest first-point time still awaiting scan completion."""
        return min(pending.schedule.captured_elapsed_s for pending in self._pending)

    def next_completed_scans(self) -> tuple[TimedReferenceScan, ...]:
        """Generate all sensor scans completing at the next event time."""
        completion_s = self.next_completion_elapsed_s
        while self._scenario.elapsed_s < completion_s:
            next_scene_event_s = self._scenario.next_surface_event_elapsed_s
            interval_end_s = min(next_scene_event_s, completion_s)
            for observer in self._observers:
                observer.advance_to(interval_end_s, scenario=self._scenario)
            self._measure_points_before(interval_end_s)
            self._scenario.advance_to(interval_end_s)

        completed = [
            pending for pending in self._pending if pending.schedule.completed_at_s == completion_s
        ]
        results: list[TimedReferenceScan] = []
        for pending in completed:
            if pending.next_point_index != pending.schedule.point_count:
                raise RuntimeError("completed rotation still contains unmeasured points")
            results.append(
                TimedReferenceScan(
                    schedule=pending.schedule,
                    scan=ReferenceScan(
                        sensor_id=pending.scanner.sensor_id,
                        points=tuple(pending.points),
                    ),
                )
            )
            pending.schedule = pending.scheduler.next_scan()
            pending.next_point_index = 0
            pending.points.clear()
        return tuple(results)

    def _measure_points_before(self, end_s: float) -> None:
        for pending in self._pending:
            point_times_s = pending.schedule.point_elapsed_times_s
            end_index = int(np.searchsorted(point_times_s, end_s, side="left"))
            if end_index <= pending.next_point_index:
                continue
            angles_deg = pending.schedule.angles_deg[pending.next_point_index : end_index]
            partial_scan = pending.scanner.generate(self._scene, angles_deg)
            pending.points.extend(partial_scan.points)
            pending.next_point_index = end_index


def build_reference_generation_runtime(
    inputs: GeneratorInputs,
    *,
    observers: Sequence[ScenarioTimeObserver] = (),
) -> ReferenceGenerationRuntime:
    """Assemble time-aware reference generation from validated inputs."""
    scenario = build_scenario_simulator(inputs)
    scene = build_environment_scene(inputs.environment, dynamic_surface=scenario.surface)
    measurement = inputs.generator.measurement
    scanners = tuple(
        ReferenceScanner(
            sensor_id=sensor.sensor_id,
            frame=build_sensor_frame(sensor),
            min_distance_m=measurement.min_distance_m,
            max_distance_m=measurement.max_distance_m,
        )
        for sensor in inputs.environment.sensors
    )
    return ReferenceGenerationRuntime(
        scenario=scenario,
        scene=scene,
        scanners=scanners,
        schedulers=build_rotation_schedulers(inputs),
        observers=observers,
    )


def _unique_by_sensor_id[SensorValue: (ReferenceScanner, SensorRotationScheduler)](
    values: tuple[SensorValue, ...],
    value_name: str,
) -> dict[str, SensorValue]:
    result: dict[str, SensorValue] = {}
    for value in values:
        if value.sensor_id in result:
            raise ValueError(f"reference runtime {value_name} sensor identifiers must be unique")
        result[value.sensor_id] = value
    return result
