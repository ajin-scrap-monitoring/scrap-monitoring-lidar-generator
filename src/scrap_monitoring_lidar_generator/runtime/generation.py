"""Runtime orchestration for reference and final measured scans."""

import time
from collections.abc import Callable

from scrap_monitoring_lidar_generator.configuration import GeneratorInputs
from scrap_monitoring_lidar_generator.measurement import (
    MeasurementGenerator,
    MeasurementResult,
    SpatialDistortionTimeline,
)
from scrap_monitoring_lidar_generator.runtime.diagnostics import MeasurementDiagnosticsSink
from scrap_monitoring_lidar_generator.runtime.measurement import (
    build_measurement_generators,
    build_spatial_distortion_timeline,
)
from scrap_monitoring_lidar_generator.runtime.performance import (
    PerformanceRecorder,
    PerformanceStage,
)
from scrap_monitoring_lidar_generator.runtime.reference import (
    ReferenceGenerationRuntime,
    build_reference_generation_runtime,
)
from scrap_monitoring_lidar_generator.scenario import ScenarioSimulator


class MeasurementGenerationRuntime:
    """Generate separate reference and final scans in simulation-time order."""

    __slots__ = (
        "_clock_ns",
        "_diagnostics_sink",
        "_generators",
        "_performance",
        "_reference_runtime",
        "_spatial_distortions",
    )

    def __init__(
        self,
        *,
        reference_runtime: ReferenceGenerationRuntime,
        generators: tuple[MeasurementGenerator, ...],
        spatial_distortions: SpatialDistortionTimeline | None = None,
        diagnostics_sink: MeasurementDiagnosticsSink | None = None,
        performance: PerformanceRecorder | None = None,
        clock_ns: Callable[[], int] = time.perf_counter_ns,
    ) -> None:
        generators_by_id: dict[str, MeasurementGenerator] = {}
        for generator in generators:
            if generator.sensor_id in generators_by_id:
                raise ValueError("measurement runtime generator sensor identifiers must be unique")
            generators_by_id[generator.sensor_id] = generator
        if generators_by_id.keys() != set(reference_runtime.sensor_ids):
            raise ValueError("measurement runtime reference and generator sensor sets must match")

        self._reference_runtime = reference_runtime
        self._generators = generators_by_id
        self._spatial_distortions = spatial_distortions
        self._diagnostics_sink = diagnostics_sink
        self._performance = performance
        self._clock_ns = clock_ns

    @property
    def scenario(self) -> ScenarioSimulator:
        """Return the shared scenario advanced by the reference runtime."""
        return self._reference_runtime.scenario

    @property
    def sensor_ids(self) -> tuple[str, ...]:
        """Return the coordinated sensor identifiers in stable order."""
        return self._reference_runtime.sensor_ids

    @property
    def next_completion_elapsed_s(self) -> float:
        """Return the earliest pending sensor rotation completion time."""
        return self._reference_runtime.next_completion_elapsed_s

    def next_completed_scans(self) -> tuple[MeasurementResult, ...]:
        """Generate final measurements for the next completed reference scans."""
        results = []
        for reference in self._reference_runtime.next_completed_scans():
            if self._performance is None:
                result = self._generators[reference.sensor_id].generate(reference)
            else:
                started_ns = self._clock_ns()
                try:
                    result = self._generators[reference.sensor_id].generate(reference)
                finally:
                    self._performance.record(
                        PerformanceStage.SCAN_GENERATION,
                        self._clock_ns() - started_ns,
                    )
            results.append(result)
        if self._diagnostics_sink is not None:
            for result in results:
                self._diagnostics_sink.record(result, self.scenario)
        if self._spatial_distortions is not None:
            self._spatial_distortions.discard_before(
                self._reference_runtime.earliest_pending_elapsed_s
            )
        return tuple(results)


def build_measurement_generation_runtime(
    inputs: GeneratorInputs,
    *,
    diagnostics_sink: MeasurementDiagnosticsSink | None = None,
    performance: PerformanceRecorder | None = None,
) -> MeasurementGenerationRuntime:
    """Assemble complete reference, distance-noise, and quality generation."""
    spatial_distortions = build_spatial_distortion_timeline(inputs)
    return MeasurementGenerationRuntime(
        reference_runtime=build_reference_generation_runtime(
            inputs,
            observers=(() if spatial_distortions is None else (spatial_distortions,)),
            performance=performance,
        ),
        generators=build_measurement_generators(
            inputs,
            spatial_distortions=spatial_distortions,
        ),
        spatial_distortions=spatial_distortions,
        diagnostics_sink=diagnostics_sink,
        performance=performance,
    )
