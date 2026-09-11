"""Runtime orchestration and lifecycle management."""

from scrap_monitoring_lidar_generator.runtime.application import (
    DeadlineWaiter,
    GeneratorRunSummary,
    ScanMessageSink,
    run_generator_application,
    run_scan_generation,
)
from scrap_monitoring_lidar_generator.runtime.diagnostics import (
    JsonLinesDiagnosticsWriter,
    MeasurementDiagnosticsSink,
    build_diagnostics_writer,
    generator_input_fingerprint,
)
from scrap_monitoring_lidar_generator.runtime.generation import (
    MeasurementGenerationRuntime,
    build_measurement_generation_runtime,
)
from scrap_monitoring_lidar_generator.runtime.measurement import (
    build_measurement_generators,
    build_rotation_schedulers,
    build_spatial_distortion_timeline,
)
from scrap_monitoring_lidar_generator.runtime.performance import (
    DurationSummary,
    PerformanceRecorder,
    PerformanceStage,
)
from scrap_monitoring_lidar_generator.runtime.reference import (
    ReferenceGenerationRuntime,
    ScenarioTimeObserver,
    build_reference_generation_runtime,
)
from scrap_monitoring_lidar_generator.runtime.scenario import build_scenario_simulator
from scrap_monitoring_lidar_generator.runtime.transport import build_scan_sender

__all__ = [
    "DeadlineWaiter",
    "DurationSummary",
    "GeneratorRunSummary",
    "JsonLinesDiagnosticsWriter",
    "MeasurementDiagnosticsSink",
    "MeasurementGenerationRuntime",
    "PerformanceRecorder",
    "PerformanceStage",
    "ReferenceGenerationRuntime",
    "ScanMessageSink",
    "ScenarioTimeObserver",
    "build_diagnostics_writer",
    "build_measurement_generation_runtime",
    "build_measurement_generators",
    "build_reference_generation_runtime",
    "build_rotation_schedulers",
    "build_scan_sender",
    "build_scenario_simulator",
    "build_spatial_distortion_timeline",
    "generator_input_fingerprint",
    "run_generator_application",
    "run_scan_generation",
]
