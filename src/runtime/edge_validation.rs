//! Feature-gated frame completion telemetry for Raspberry Pi validation.

use std::{
    fs::{self, File, OpenOptions},
    io::{BufWriter, Write},
    path::{Path, PathBuf},
    time::Duration,
};

#[cfg(unix)]
use std::os::unix::fs::OpenOptionsExt;

use serde::Serialize;
use uuid::Uuid;

use crate::{
    measurement::{HitKind, MeasurementResult},
    observation::ObservationPublisherStats,
    scan_runtime::ScanRuntimeStats,
    scenario::ScenarioPhase,
};

use super::generation::ScenarioPhaseTransition;

pub const DEFAULT_WARMUP_DURATION_S: u64 = 300;
pub const DEFAULT_MEASUREMENT_DURATION_S: u64 = 3_600;
pub const DEFAULT_SAMPLE_CAPACITY: usize = 40_000;
pub const MAX_SAMPLE_CAPACITY: usize = 100_000;
pub const MAX_SCENARIO_TRANSITIONS: usize = 64;
pub(crate) const MINIMUM_START_LEAD_NS: u64 = 1_000_000_000;
const SEMANTIC_SAMPLE_INTERVAL_NS: u64 = 1_000_000_000;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum EdgeValidationObservationMode {
    Actual,
    NoOp,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EdgeValidationSettings {
    pub(crate) observation_mode: EdgeValidationObservationMode,
    pub(crate) warmup_duration: Duration,
    pub(crate) measurement_duration: Duration,
    pub(crate) sample_capacity: usize,
    pub(crate) start_at_monotonic_ns: Option<u64>,
    pub(crate) output_path: PathBuf,
}

impl EdgeValidationSettings {
    pub fn new(
        observation_mode: EdgeValidationObservationMode,
        warmup_duration: Duration,
        measurement_duration: Duration,
        sample_capacity: usize,
        output_path: impl Into<PathBuf>,
    ) -> Result<Self> {
        let output_path = output_path.into();
        if warmup_duration.is_zero() {
            return Err(EdgeValidationError::Invalid(
                "warmup duration must be positive",
            ));
        }
        if measurement_duration.is_zero() {
            return Err(EdgeValidationError::Invalid(
                "measurement duration must be positive",
            ));
        }
        if warmup_duration.subsec_nanos() != 0 || measurement_duration.subsec_nanos() != 0 {
            return Err(EdgeValidationError::Invalid(
                "edge validation durations must use whole seconds",
            ));
        }
        let total_duration = warmup_duration.checked_add(measurement_duration).ok_or(
            EdgeValidationError::Invalid(
                "warmup and measurement duration exceed the platform range",
            ),
        )?;
        duration_ns(total_duration)?;
        if !(1..=MAX_SAMPLE_CAPACITY).contains(&sample_capacity) {
            return Err(EdgeValidationError::Invalid(
                "sample capacity must be from 1 through 100000",
            ));
        }
        validate_output_path(&output_path)?;
        ensure_output_absent(&output_path)?;
        Ok(Self {
            observation_mode,
            warmup_duration,
            measurement_duration,
            sample_capacity,
            start_at_monotonic_ns: None,
            output_path,
        })
    }

    pub fn with_start_at_monotonic_ns(
        mut self,
        start_at_monotonic_ns: Option<u64>,
    ) -> Result<Self> {
        if start_at_monotonic_ns == Some(0) {
            return Err(EdgeValidationError::Invalid(
                "edge validation start monotonic time must be positive",
            ));
        }
        self.start_at_monotonic_ns = start_at_monotonic_ns;
        Ok(self)
    }

    pub(crate) fn generation_epoch_monotonic_ns(&self, current: u64) -> Result<u64> {
        let Some(requested) = self.start_at_monotonic_ns else {
            return Ok(current);
        };
        if requested <= current {
            return Err(EdgeValidationError::StartInPast { requested, current });
        }
        let minimum = current.saturating_add(MINIMUM_START_LEAD_NS);
        if requested < minimum {
            return Err(EdgeValidationError::StartTooSoon { requested, minimum });
        }
        Ok(requested)
    }
}

#[derive(Debug, thiserror::Error)]
pub enum EdgeValidationError {
    #[error("{0}")]
    Invalid(&'static str),
    #[error("edge validation telemetry allocation failed")]
    Allocation,
    #[error("edge validation sample capacity {capacity} was exceeded")]
    SampleOverflow { capacity: usize },
    #[error("edge validation sample capacity {capacity} is below the required {required} samples")]
    SampleCapacityInsufficient { required: usize, capacity: usize },
    #[error("edge validation expected {expected} measured batches but recorded {actual}")]
    MissingSamples { expected: usize, actual: usize },
    #[error("edge validation batch is missing sensor {sensor_id}")]
    MissingSensor { sensor_id: String },
    #[error("edge validation batch contains duplicate sensor {sensor_id}")]
    DuplicateSensor { sensor_id: String },
    #[error("edge validation batch contains unknown sensor {sensor_id}")]
    UnknownSensor { sensor_id: String },
    #[error(
        "edge validation sensor {sensor_id} sequence is not continuous: expected {expected}, got {actual}"
    )]
    SequenceGap {
        sensor_id: String,
        expected: u64,
        actual: u64,
    },
    #[error("edge validation publication clock precedes its scheduled deadline")]
    ClockBeforeDeadline,
    #[error("edge validation did not reach the end of its measurement window")]
    Incomplete,
    #[error("edge validation was interrupted before its measurement window completed")]
    Interrupted,
    #[error("edge validation scenario transition capacity {capacity} was exceeded")]
    ScenarioTransitionOverflow { capacity: usize },
    #[error("edge validation scenario evidence is invalid: {0}")]
    Scenario(&'static str),
    #[error("edge validation scan semantics are invalid: {0}")]
    ScanSemantics(&'static str),
    #[error(
        "edge validation requested monotonic start {requested} precedes setup completion {current}"
    )]
    StartInPast { requested: u64, current: u64 },
    #[error(
        "edge validation requested monotonic start {requested} is too soon; it must be at least {minimum}"
    )]
    StartTooSoon { requested: u64, minimum: u64 },
    #[error("edge validation output already exists: {0}")]
    OutputExists(PathBuf),
    #[error("edge validation output {operation} failed for {path}: {source}")]
    Io {
        operation: &'static str,
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("edge validation JSON encoding failed: {0}")]
    Json(#[from] serde_json::Error),
}

pub type Result<T> = std::result::Result<T, EdgeValidationError>;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct PublishedFrameTiming {
    pub sensor_index: usize,
    pub sequence: u64,
    pub published_monotonic_ns: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub struct FrameCompletionSample {
    pub sequence: u64,
    pub deadline_monotonic_ns: u64,
    pub published_monotonic_ns: u64,
    pub latency_ns: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub struct BatchCompletionSample {
    pub deadline_monotonic_ns: u64,
    pub published_monotonic_ns: u64,
    pub latency_ns: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum ScenarioPhaseTelemetry {
    Filling,
    Collecting,
}

impl From<ScenarioPhase> for ScenarioPhaseTelemetry {
    fn from(value: ScenarioPhase) -> Self {
        match value {
            ScenarioPhase::Filling => Self::Filling,
            ScenarioPhase::Collecting => Self::Collecting,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub struct ScenarioStateTelemetry {
    pub cycle_index: u64,
    pub phase: ScenarioPhaseTelemetry,
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize)]
pub struct ScenarioTransitionTelemetry {
    pub transitioned_at_elapsed_s: f64,
    pub from: ScenarioStateTelemetry,
    pub to: ScenarioStateTelemetry,
    pub before_batch: BatchCompletionSample,
    pub after_batch: BatchCompletionSample,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct ScenarioTelemetry {
    pub window_started_elapsed_s: f64,
    pub window_ended_elapsed_s: f64,
    pub observed_through_elapsed_s: f64,
    pub initial_state: ScenarioStateTelemetry,
    pub final_state: ScenarioStateTelemetry,
    pub transition_capacity: usize,
    pub transition_count: usize,
    pub overflowed: bool,
    pub transitions: Vec<ScenarioTransitionTelemetry>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct SensorCompletionSeries {
    pub sensor_id: String,
    pub sample_count: usize,
    pub missing_sample_count: u64,
    pub first_sequence: u64,
    pub last_sequence: u64,
    pub samples: Vec<FrameCompletionSample>,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize)]
pub struct ObservationTelemetry {
    pub accepted_records: u64,
    pub sent_records: u64,
    pub dropped_records: u64,
    pub connection_failures: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ScanStreamTelemetry {
    pub sensor_id: String,
    pub published_frames: u64,
    pub frame_loss: u64,
    pub subscribers: usize,
    pub subscriber_seen: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ScanSemanticTelemetry {
    pub sensor_id: String,
    pub sampled_scan_count: u64,
    pub reference_sample_count: u64,
    pub no_hit_count: u64,
    pub floor_hit_count: u64,
    pub wall_hit_count: u64,
    pub surface_hit_count: u64,
    pub measured_valid_count: u64,
    pub measured_invalid_count: u64,
    pub measured_without_reference_count: u64,
    pub reference_hit_without_measurement_count: u64,
    pub reference_change_count: u64,
}

#[derive(Clone, Debug, Default)]
struct ScanSemanticAccumulator {
    sampled_scan_count: u64,
    reference_sample_count: u64,
    no_hit_count: u64,
    floor_hit_count: u64,
    wall_hit_count: u64,
    surface_hit_count: u64,
    measured_valid_count: u64,
    measured_invalid_count: u64,
    measured_without_reference_count: u64,
    reference_hit_without_measurement_count: u64,
    reference_change_count: u64,
    previous_reference_signature: Option<u64>,
}

impl ScanSemanticAccumulator {
    fn record(&mut self, result: &MeasurementResult) -> Result<()> {
        let reference = result.reference().scan().points();
        let measured = result.measured().hq_samples();
        if reference.len() != measured.len() {
            return Err(EdgeValidationError::ScanSemantics(
                "reference and measured scan lengths differ",
            ));
        }
        let mut signature = 0xcbf2_9ce4_8422_2325_u64;
        for (point, sample) in reference.iter().zip(measured) {
            let kind = match point.hit_kind {
                None => {
                    increment(&mut self.no_hit_count)?;
                    0_u8
                }
                Some(HitKind::Floor) => {
                    increment(&mut self.floor_hit_count)?;
                    1
                }
                Some(HitKind::Wall) => {
                    increment(&mut self.wall_hit_count)?;
                    2
                }
                Some(HitKind::Surface) => {
                    increment(&mut self.surface_hit_count)?;
                    3
                }
            };
            signature = fnv1a(signature, &[kind]);
            signature = fnv1a(signature, &point.distance_m.to_bits().to_le_bytes());
            let measured_valid = sample.dist_mm_q2 != 0;
            if measured_valid {
                increment(&mut self.measured_valid_count)?;
            } else {
                increment(&mut self.measured_invalid_count)?;
            }
            if point.hit_kind.is_none() && measured_valid {
                increment(&mut self.measured_without_reference_count)?;
            }
            if point.hit_kind.is_some() && !measured_valid {
                increment(&mut self.reference_hit_without_measurement_count)?;
            }
        }
        self.reference_sample_count = self
            .reference_sample_count
            .checked_add(u64::try_from(reference.len()).map_err(|_| {
                EdgeValidationError::ScanSemantics("reference sample count exceeds u64")
            })?)
            .ok_or(EdgeValidationError::ScanSemantics(
                "reference sample count overflowed",
            ))?;
        increment(&mut self.sampled_scan_count)?;
        if self
            .previous_reference_signature
            .is_some_and(|previous| previous != signature)
        {
            increment(&mut self.reference_change_count)?;
        }
        self.previous_reference_signature = Some(signature);
        Ok(())
    }

    fn finish(self, sensor_id: String) -> ScanSemanticTelemetry {
        ScanSemanticTelemetry {
            sensor_id,
            sampled_scan_count: self.sampled_scan_count,
            reference_sample_count: self.reference_sample_count,
            no_hit_count: self.no_hit_count,
            floor_hit_count: self.floor_hit_count,
            wall_hit_count: self.wall_hit_count,
            surface_hit_count: self.surface_hit_count,
            measured_valid_count: self.measured_valid_count,
            measured_invalid_count: self.measured_invalid_count,
            measured_without_reference_count: self.measured_without_reference_count,
            reference_hit_without_measurement_count: self.reference_hit_without_measurement_count,
            reference_change_count: self.reference_change_count,
        }
    }
}

impl From<ObservationPublisherStats> for ObservationTelemetry {
    fn from(stats: ObservationPublisherStats) -> Self {
        Self {
            accepted_records: stats.accepted_records,
            sent_records: stats.sent_records,
            dropped_records: stats.dropped_records,
            connection_failures: stats.connection_failures,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct EdgeValidationReport {
    pub schema_version: &'static str,
    pub run_id: String,
    pub observation_mode: EdgeValidationObservationMode,
    pub warmup_duration_s: u64,
    pub measurement_duration_s: u64,
    pub measurement_started_monotonic_ns: u64,
    pub measurement_ended_monotonic_ns: u64,
    pub expected_sensor_ids: [String; 2],
    pub sample_capacity: usize,
    pub expected_sample_count: usize,
    pub generated_scans: u64,
    pub completed_batch_count: usize,
    pub dropped_sample_count: u64,
    pub overflowed: bool,
    pub observation: ObservationTelemetry,
    pub scan_stream: [ScanStreamTelemetry; 2],
    pub scan_semantics: [ScanSemanticTelemetry; 2],
    pub scenario: ScenarioTelemetry,
    pub sensors: [SensorCompletionSeries; 2],
    pub batch_max_samples: Vec<BatchCompletionSample>,
}

pub(crate) struct EdgeValidationRecorder {
    observation_mode: EdgeValidationObservationMode,
    warmup_duration_s: u64,
    measurement_duration_s: u64,
    measurement_started_monotonic_ns: u64,
    measurement_ended_monotonic_ns: u64,
    validation_started_monotonic_ns: u64,
    scenario_window_ended_elapsed_s: f64,
    scenario_observed_through_elapsed_s: f64,
    scenario_initial_state: ScenarioStateTelemetry,
    scenario_current_state: ScenarioStateTelemetry,
    scenario_transitions: Vec<ScenarioTransitionTelemetry>,
    last_scenario_batch: Option<BatchCompletionSample>,
    expected_sensor_ids: [String; 2],
    sample_capacity: usize,
    expected_sample_count: usize,
    sensor_samples: [Vec<FrameCompletionSample>; 2],
    batch_max_samples: Vec<BatchCompletionSample>,
    last_sequences: [Option<u64>; 2],
    subscriber_seen: [bool; 2],
    next_semantic_sample_monotonic_ns: u64,
    scan_semantics: [ScanSemanticAccumulator; 2],
    reached_end: bool,
}

impl EdgeValidationRecorder {
    pub(crate) fn new(
        settings: &EdgeValidationSettings,
        epoch_monotonic_ns: u64,
        expected_sensor_ids: [String; 2],
        rotation_rate_hz: f64,
    ) -> Result<Self> {
        if expected_sensor_ids[0].is_empty()
            || expected_sensor_ids[1].is_empty()
            || expected_sensor_ids[0] == expected_sensor_ids[1]
        {
            return Err(EdgeValidationError::Invalid(
                "edge validation requires exactly two unique sensors",
            ));
        }
        let warmup_ns = duration_ns(settings.warmup_duration)?;
        let measurement_ns = duration_ns(settings.measurement_duration)?;
        let total_duration = settings
            .warmup_duration
            .checked_add(settings.measurement_duration)
            .ok_or(EdgeValidationError::Invalid(
                "edge validation duration exceeds the platform range",
            ))?;
        let measurement_started_monotonic_ns =
            epoch_monotonic_ns
                .checked_add(warmup_ns)
                .ok_or(EdgeValidationError::Invalid(
                    "measurement start exceeds the host monotonic range",
                ))?;
        let measurement_ended_monotonic_ns = measurement_started_monotonic_ns
            .checked_add(measurement_ns)
            .ok_or(EdgeValidationError::Invalid(
                "measurement end exceeds the host monotonic range",
            ))?;
        let next_semantic_sample_monotonic_ns = measurement_started_monotonic_ns
            .checked_add(SEMANTIC_SAMPLE_INTERVAL_NS)
            .ok_or(EdgeValidationError::Invalid(
                "semantic sample deadline exceeds the host monotonic range",
            ))?;
        let expected_sample_count = expected_sample_count(
            settings.warmup_duration,
            settings.measurement_duration,
            rotation_rate_hz,
        )?;
        if expected_sample_count > settings.sample_capacity {
            return Err(EdgeValidationError::SampleCapacityInsufficient {
                required: expected_sample_count,
                capacity: settings.sample_capacity,
            });
        }
        let mut first = Vec::new();
        let mut second = Vec::new();
        let mut batches = Vec::new();
        let mut scenario_transitions = Vec::new();
        first
            .try_reserve_exact(settings.sample_capacity)
            .map_err(|_| EdgeValidationError::Allocation)?;
        second
            .try_reserve_exact(settings.sample_capacity)
            .map_err(|_| EdgeValidationError::Allocation)?;
        batches
            .try_reserve_exact(settings.sample_capacity)
            .map_err(|_| EdgeValidationError::Allocation)?;
        scenario_transitions
            .try_reserve_exact(MAX_SCENARIO_TRANSITIONS)
            .map_err(|_| EdgeValidationError::Allocation)?;
        let scenario_initial_state = ScenarioStateTelemetry {
            cycle_index: 0,
            phase: ScenarioPhaseTelemetry::Filling,
        };
        Ok(Self {
            observation_mode: settings.observation_mode,
            warmup_duration_s: settings.warmup_duration.as_secs(),
            measurement_duration_s: settings.measurement_duration.as_secs(),
            measurement_started_monotonic_ns,
            measurement_ended_monotonic_ns,
            validation_started_monotonic_ns: epoch_monotonic_ns,
            scenario_window_ended_elapsed_s: total_duration.as_secs_f64(),
            scenario_observed_through_elapsed_s: 0.0,
            scenario_initial_state,
            scenario_current_state: scenario_initial_state,
            scenario_transitions,
            last_scenario_batch: None,
            expected_sensor_ids,
            sample_capacity: settings.sample_capacity,
            expected_sample_count,
            sensor_samples: [first, second],
            batch_max_samples: batches,
            last_sequences: [None, None],
            subscriber_seen: [false, false],
            next_semantic_sample_monotonic_ns,
            scan_semantics: std::array::from_fn(|_| ScanSemanticAccumulator::default()),
            reached_end: false,
        })
    }

    pub(crate) fn observe_subscribers(&mut self, scan_stats: &ScanRuntimeStats) -> Result<()> {
        for stats in &scan_stats.sensors {
            let index = self.sensor_index(&stats.sensor_id)?;
            self.subscriber_seen[index] |= stats.subscribers > 0;
        }
        Ok(())
    }

    pub(crate) fn sensor_index(&self, sensor_id: &str) -> Result<usize> {
        self.expected_sensor_ids
            .iter()
            .position(|expected| expected == sensor_id)
            .ok_or_else(|| EdgeValidationError::UnknownSensor {
                sensor_id: sensor_id.to_owned(),
            })
    }

    pub(crate) fn measures_deadline(&self, deadline_monotonic_ns: u64) -> bool {
        deadline_monotonic_ns > self.measurement_started_monotonic_ns
            && deadline_monotonic_ns <= self.measurement_ended_monotonic_ns
    }

    pub(crate) fn record_scan_semantics(
        &mut self,
        deadline_monotonic_ns: u64,
        scans: &[MeasurementResult],
    ) -> Result<()> {
        if !self.measures_deadline(deadline_monotonic_ns)
            || deadline_monotonic_ns < self.next_semantic_sample_monotonic_ns
        {
            return Ok(());
        }
        self.next_semantic_sample_monotonic_ns = self
            .next_semantic_sample_monotonic_ns
            .checked_add(SEMANTIC_SAMPLE_INTERVAL_NS)
            .ok_or(EdgeValidationError::ScanSemantics(
                "semantic sample deadline overflowed",
            ))?;
        let mut recorded = [false; 2];
        for scan in scans {
            let index = self.sensor_index(scan.sensor_id())?;
            if recorded[index] {
                return Err(EdgeValidationError::DuplicateSensor {
                    sensor_id: scan.sensor_id().to_owned(),
                });
            }
            self.scan_semantics[index].record(scan)?;
            recorded[index] = true;
        }
        for (index, present) in recorded.into_iter().enumerate() {
            if !present {
                return Err(EdgeValidationError::MissingSensor {
                    sensor_id: self.expected_sensor_ids[index].clone(),
                });
            }
        }
        Ok(())
    }

    pub(crate) fn record_batch(
        &mut self,
        deadline_monotonic_ns: u64,
        timings: &[PublishedFrameTiming],
        scenario_transitions: &[ScenarioPhaseTransition],
    ) -> Result<bool> {
        if deadline_monotonic_ns > self.measurement_ended_monotonic_ns {
            self.reached_end = true;
            return Ok(true);
        }

        let batch = self.validated_batch(deadline_monotonic_ns, timings)?;
        self.record_scenario(
            deadline_monotonic_ns,
            batch.as_ref().map(|(_, sample)| *sample),
            scenario_transitions,
        )?;
        if !self.measures_deadline(deadline_monotonic_ns) {
            return Ok(false);
        }
        let Some((samples, batch_sample)) = batch else {
            return Err(EdgeValidationError::MissingSensor {
                sensor_id: self.expected_sensor_ids[0].clone(),
            });
        };
        if self.batch_max_samples.len() >= self.sample_capacity {
            return Err(EdgeValidationError::SampleOverflow {
                capacity: self.sample_capacity,
            });
        }
        self.validate_sequences(&samples)?;
        for (index, sample) in samples.into_iter().enumerate() {
            self.last_sequences[index] = Some(sample.sequence);
            self.sensor_samples[index].push(sample);
        }
        self.batch_max_samples.push(batch_sample);
        Ok(false)
    }

    fn validated_batch(
        &self,
        deadline_monotonic_ns: u64,
        timings: &[PublishedFrameTiming],
    ) -> Result<Option<([FrameCompletionSample; 2], BatchCompletionSample)>> {
        if timings.is_empty() {
            return Ok(None);
        }
        let mut batch: [Option<FrameCompletionSample>; 2] = [None, None];
        for timing in timings {
            if timing.sensor_index >= self.expected_sensor_ids.len() {
                return Err(EdgeValidationError::UnknownSensor {
                    sensor_id: format!("index-{}", timing.sensor_index),
                });
            }
            if batch[timing.sensor_index].is_some() {
                return Err(EdgeValidationError::DuplicateSensor {
                    sensor_id: self.expected_sensor_ids[timing.sensor_index].clone(),
                });
            }
            let latency_ns = timing
                .published_monotonic_ns
                .checked_sub(deadline_monotonic_ns)
                .ok_or(EdgeValidationError::ClockBeforeDeadline)?;
            batch[timing.sensor_index] = Some(FrameCompletionSample {
                sequence: timing.sequence,
                deadline_monotonic_ns,
                published_monotonic_ns: timing.published_monotonic_ns,
                latency_ns,
            });
        }
        for (index, sample) in batch.iter().enumerate() {
            if sample.is_none() {
                return Err(EdgeValidationError::MissingSensor {
                    sensor_id: self.expected_sensor_ids[index].clone(),
                });
            }
        }
        let samples = batch.map(|sample| sample.expect("both sensor samples were validated"));
        let latest = samples
            .iter()
            .max_by_key(|sample| sample.published_monotonic_ns)
            .copied()
            .expect("two validated samples have a maximum");
        Ok(Some((
            samples,
            BatchCompletionSample {
                deadline_monotonic_ns,
                published_monotonic_ns: latest.published_monotonic_ns,
                latency_ns: latest.latency_ns,
            },
        )))
    }

    fn validate_sequences(&self, samples: &[FrameCompletionSample; 2]) -> Result<()> {
        for (index, sample) in samples.iter().enumerate() {
            if let Some(previous) = self.last_sequences[index] {
                let expected =
                    previous
                        .checked_add(1)
                        .ok_or_else(|| EdgeValidationError::SequenceGap {
                            sensor_id: self.expected_sensor_ids[index].clone(),
                            expected: u64::MAX,
                            actual: sample.sequence,
                        })?;
                if sample.sequence != expected {
                    return Err(EdgeValidationError::SequenceGap {
                        sensor_id: self.expected_sensor_ids[index].clone(),
                        expected,
                        actual: sample.sequence,
                    });
                }
            }
        }
        Ok(())
    }

    fn record_scenario(
        &mut self,
        deadline_monotonic_ns: u64,
        batch: Option<BatchCompletionSample>,
        transitions: &[ScenarioPhaseTransition],
    ) -> Result<()> {
        let observed_ns = deadline_monotonic_ns
            .checked_sub(self.validation_started_monotonic_ns)
            .ok_or(EdgeValidationError::Scenario(
                "batch deadline precedes the validation window",
            ))?;
        let observed_s = observed_ns as f64 / 1_000_000_000.0;
        if observed_s + 1e-12 < self.scenario_observed_through_elapsed_s
            || observed_s > self.scenario_window_ended_elapsed_s + 1e-9
        {
            return Err(EdgeValidationError::Scenario(
                "batch elapsed time is outside the validation window",
            ));
        }
        for transition in transitions {
            self.record_scenario_transition(transition, batch)?;
        }
        self.scenario_observed_through_elapsed_s = observed_s;
        if let Some(batch) = batch {
            self.last_scenario_batch = Some(batch);
        }
        Ok(())
    }

    fn record_scenario_transition(
        &mut self,
        transition: &ScenarioPhaseTransition,
        after_batch: Option<BatchCompletionSample>,
    ) -> Result<()> {
        if self.scenario_transitions.len() >= MAX_SCENARIO_TRANSITIONS {
            return Err(EdgeValidationError::ScenarioTransitionOverflow {
                capacity: MAX_SCENARIO_TRANSITIONS,
            });
        }
        if !transition.elapsed_s.is_finite()
            || transition.elapsed_s <= 0.0
            || transition.elapsed_s > self.scenario_window_ended_elapsed_s
            || self
                .scenario_transitions
                .last()
                .is_some_and(|previous| transition.elapsed_s <= previous.transitioned_at_elapsed_s)
        {
            return Err(EdgeValidationError::Scenario(
                "transition times must be finite, ordered, and inside the validation window",
            ));
        }
        let before_batch = self
            .last_scenario_batch
            .ok_or(EdgeValidationError::Scenario(
                "transition has no preceding frame completion latency",
            ))?;
        let after_batch = after_batch.ok_or(EdgeValidationError::Scenario(
            "transition has no following frame completion latency",
        ))?;
        let offset = Duration::try_from_secs_f64(transition.elapsed_s).map_err(|_| {
            EdgeValidationError::Scenario("transition elapsed time cannot be represented")
        })?;
        let transitioned_at_monotonic_ns = self
            .validation_started_monotonic_ns
            .checked_add(duration_ns(offset)?)
            .ok_or(EdgeValidationError::Scenario(
                "transition monotonic time exceeds the host range",
            ))?;
        if before_batch.deadline_monotonic_ns >= transitioned_at_monotonic_ns
            || after_batch.deadline_monotonic_ns < transitioned_at_monotonic_ns
        {
            return Err(EdgeValidationError::Scenario(
                "transition is not bracketed by frame completion latency",
            ));
        }

        let to = ScenarioStateTelemetry {
            cycle_index: transition.cycle_index,
            phase: transition.phase.into(),
        };
        validate_scenario_state_change(self.scenario_current_state, to)?;
        self.scenario_transitions.push(ScenarioTransitionTelemetry {
            transitioned_at_elapsed_s: transition.elapsed_s,
            from: self.scenario_current_state,
            to,
            before_batch,
            after_batch,
        });
        self.scenario_current_state = to;
        Ok(())
    }

    pub(crate) fn finish(
        self,
        run_id: String,
        generated_scans: u64,
        scan_stats: &ScanRuntimeStats,
        observation_stats: ObservationPublisherStats,
    ) -> Result<EdgeValidationReport> {
        if !self.reached_end
            || self.batch_max_samples.is_empty()
            || (self.scenario_observed_through_elapsed_s - self.scenario_window_ended_elapsed_s)
                .abs()
                > 1e-9
        {
            return Err(EdgeValidationError::Incomplete);
        }
        let completed_batch_count = self.batch_max_samples.len();
        if self
            .sensor_samples
            .iter()
            .any(|samples| samples.len() != completed_batch_count)
        {
            return Err(EdgeValidationError::Incomplete);
        }
        if completed_batch_count != self.expected_sample_count {
            return Err(EdgeValidationError::MissingSamples {
                expected: self.expected_sample_count,
                actual: completed_batch_count,
            });
        }
        let [first_id, second_id] = self.expected_sensor_ids.clone();
        let [first_samples, second_samples] = self.sensor_samples;
        let [first_semantics, second_semantics] = self.scan_semantics;
        let scan_stream = std::array::from_fn(|index| {
            let stats = &scan_stats.sensors[index];
            ScanStreamTelemetry {
                sensor_id: stats.sensor_id.clone(),
                published_frames: stats.published_frames,
                frame_loss: stats.frame_loss,
                subscribers: stats.subscribers,
                subscriber_seen: self.subscriber_seen[index],
            }
        });
        if scan_stream
            .iter()
            .zip([first_id.as_str(), second_id.as_str()])
            .any(|(stats, expected)| stats.sensor_id != expected)
        {
            return Err(EdgeValidationError::Invalid(
                "scan runtime sensor order differs from validation sensor order",
            ));
        }
        let scenario_transition_count = self.scenario_transitions.len();
        Ok(EdgeValidationReport {
            schema_version: "edge-validation-telemetry.v1",
            run_id,
            observation_mode: self.observation_mode,
            warmup_duration_s: self.warmup_duration_s,
            measurement_duration_s: self.measurement_duration_s,
            measurement_started_monotonic_ns: self.measurement_started_monotonic_ns,
            measurement_ended_monotonic_ns: self.measurement_ended_monotonic_ns,
            expected_sensor_ids: [first_id.clone(), second_id.clone()],
            sample_capacity: self.sample_capacity,
            expected_sample_count: self.expected_sample_count,
            generated_scans,
            completed_batch_count,
            dropped_sample_count: 0,
            overflowed: false,
            observation: observation_stats.into(),
            scan_stream,
            scan_semantics: [
                first_semantics.finish(first_id.clone()),
                second_semantics.finish(second_id.clone()),
            ],
            scenario: ScenarioTelemetry {
                window_started_elapsed_s: 0.0,
                window_ended_elapsed_s: self.scenario_window_ended_elapsed_s,
                observed_through_elapsed_s: self.scenario_observed_through_elapsed_s,
                initial_state: self.scenario_initial_state,
                final_state: self.scenario_current_state,
                transition_capacity: MAX_SCENARIO_TRANSITIONS,
                transition_count: scenario_transition_count,
                overflowed: false,
                transitions: self.scenario_transitions,
            },
            sensors: [
                SensorCompletionSeries {
                    sensor_id: first_id,
                    sample_count: first_samples.len(),
                    missing_sample_count: 0,
                    first_sequence: first_samples
                        .first()
                        .expect("completed report has sensor samples")
                        .sequence,
                    last_sequence: first_samples
                        .last()
                        .expect("completed report has sensor samples")
                        .sequence,
                    samples: first_samples,
                },
                SensorCompletionSeries {
                    sensor_id: second_id,
                    sample_count: second_samples.len(),
                    missing_sample_count: 0,
                    first_sequence: second_samples
                        .first()
                        .expect("completed report has sensor samples")
                        .sequence,
                    last_sequence: second_samples
                        .last()
                        .expect("completed report has sensor samples")
                        .sequence,
                    samples: second_samples,
                },
            ],
            batch_max_samples: self.batch_max_samples,
        })
    }
}

fn validate_scenario_state_change(
    from: ScenarioStateTelemetry,
    to: ScenarioStateTelemetry,
) -> Result<()> {
    let valid = match (from.phase, to.phase) {
        (ScenarioPhaseTelemetry::Filling, ScenarioPhaseTelemetry::Collecting) => {
            to.cycle_index == from.cycle_index
        }
        (ScenarioPhaseTelemetry::Collecting, ScenarioPhaseTelemetry::Filling) => from
            .cycle_index
            .checked_add(1)
            .is_some_and(|expected| to.cycle_index == expected),
        _ => false,
    };
    if valid {
        Ok(())
    } else {
        Err(EdgeValidationError::Scenario(
            "phase and cycle transition is not contiguous",
        ))
    }
}

fn increment(value: &mut u64) -> Result<()> {
    *value = value
        .checked_add(1)
        .ok_or(EdgeValidationError::ScanSemantics(
            "scan semantic counter overflowed",
        ))?;
    Ok(())
}

fn fnv1a(mut state: u64, bytes: &[u8]) -> u64 {
    for byte in bytes {
        state ^= u64::from(*byte);
        state = state.wrapping_mul(0x0000_0100_0000_01b3);
    }
    state
}

pub fn write_edge_validation_report_atomic(
    path: &Path,
    report: &EdgeValidationReport,
) -> Result<()> {
    validate_output_path(path)?;
    ensure_output_absent(path)?;
    let parent = path.parent().expect("validated path has a parent");
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .expect("validated path has a UTF-8 file name");
    let temporary = parent.join(format!(".{name}.{}.tmp", Uuid::new_v4().simple()));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    options.mode(0o600);
    let mut file = options
        .open(&temporary)
        .map_err(|source| io_error("temporary create", &temporary, source))?;
    let result = (|| {
        {
            let mut writer = BufWriter::new(&mut file);
            serde_json::to_writer(&mut writer, report)?;
            writer
                .write_all(b"\n")
                .map_err(|source| io_error("temporary write", &temporary, source))?;
            writer
                .flush()
                .map_err(|source| io_error("temporary flush", &temporary, source))?;
        }
        file.sync_all()
            .map_err(|source| io_error("temporary fsync", &temporary, source))?;
        fs::hard_link(&temporary, path).map_err(|source| map_commit_error(path, source))?;
        fs::remove_file(&temporary)
            .map_err(|source| io_error("temporary remove", &temporary, source))?;
        File::open(parent)
            .and_then(|directory| directory.sync_all())
            .map_err(|source| io_error("parent fsync", parent, source))?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

fn ensure_output_absent(path: &Path) -> Result<()> {
    match fs::symlink_metadata(path) {
        Ok(_) => Err(EdgeValidationError::OutputExists(path.to_owned())),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(source) => Err(io_error("inspect", path, source)),
    }
}

fn validate_output_path(path: &Path) -> Result<()> {
    if !path.is_absolute() || path.to_str().is_none() {
        return Err(EdgeValidationError::Invalid(
            "edge validation output must be an absolute UTF-8 path",
        ));
    }
    let parent = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty());
    let name = path.file_name().and_then(|name| name.to_str());
    if parent.is_none() || name.is_none_or(str::is_empty) {
        return Err(EdgeValidationError::Invalid(
            "edge validation output must have a UTF-8 file name",
        ));
    }
    let parent = parent.expect("validated parent exists");
    match fs::symlink_metadata(parent) {
        Ok(metadata) if metadata.file_type().is_dir() => Ok(()),
        Ok(_) => Err(EdgeValidationError::Invalid(
            "edge validation output parent must be a directory",
        )),
        Err(source) => Err(io_error("parent inspect", parent, source)),
    }
}

fn duration_ns(duration: Duration) -> Result<u64> {
    u64::try_from(duration.as_nanos()).map_err(|_| {
        EdgeValidationError::Invalid("edge validation duration exceeds the nanosecond range")
    })
}

fn expected_sample_count(
    warmup_duration: Duration,
    measurement_duration: Duration,
    rotation_rate_hz: f64,
) -> Result<usize> {
    if !rotation_rate_hz.is_finite() || rotation_rate_hz <= 0.0 {
        return Err(EdgeValidationError::Invalid(
            "edge validation rotation rate must be finite and positive",
        ));
    }
    let end_duration =
        warmup_duration
            .checked_add(measurement_duration)
            .ok_or(EdgeValidationError::Invalid(
                "edge validation duration exceeds the platform range",
            ))?;
    let start_index = tolerant_floor(warmup_duration.as_secs_f64() * rotation_rate_hz)?;
    let end_index = tolerant_floor(end_duration.as_secs_f64() * rotation_rate_hz)?;
    let count = end_index
        .checked_sub(start_index)
        .ok_or(EdgeValidationError::Invalid(
            "edge validation measurement window is invalid",
        ))?;
    usize::try_from(count).map_err(|_| {
        EdgeValidationError::Invalid(
            "edge validation expected sample count exceeds the platform range",
        )
    })
}

fn tolerant_floor(value: f64) -> Result<u64> {
    if !value.is_finite() || value < 0.0 || value > u64::MAX as f64 {
        return Err(EdgeValidationError::Invalid(
            "edge validation scan index exceeds the supported range",
        ));
    }
    let nearest = value.round();
    let normalized = if (value - nearest).abs() <= value.max(1.0) * 1e-12 {
        nearest
    } else {
        value
    };
    Ok(normalized.floor() as u64)
}

fn map_commit_error(path: &Path, source: std::io::Error) -> EdgeValidationError {
    if source.kind() == std::io::ErrorKind::AlreadyExists {
        EdgeValidationError::OutputExists(path.to_owned())
    } else {
        io_error("commit", path, source)
    }
}

fn io_error(operation: &'static str, path: &Path, source: std::io::Error) -> EdgeValidationError {
    EdgeValidationError::Io {
        operation,
        path: path.to_owned(),
        source,
    }
}

#[cfg(test)]
mod tests {
    use std::fs;

    use crate::scan_runtime::SensorRuntimeStats;
    use tempfile::tempdir;

    use super::*;

    fn settings(directory: &Path, capacity: usize) -> EdgeValidationSettings {
        EdgeValidationSettings::new(
            EdgeValidationObservationMode::NoOp,
            Duration::from_secs(1),
            Duration::from_secs(2),
            capacity,
            directory.join("telemetry.json"),
        )
        .unwrap()
    }

    fn timings(first_sequence: u64, published_at: u64) -> [PublishedFrameTiming; 2] {
        [
            PublishedFrameTiming {
                sensor_index: 0,
                sequence: first_sequence,
                published_monotonic_ns: published_at,
            },
            PublishedFrameTiming {
                sensor_index: 1,
                sequence: first_sequence,
                published_monotonic_ns: published_at + 1,
            },
        ]
    }

    fn asymmetric_timings(
        sequence: u64,
        deadline: u64,
        latencies: [u64; 2],
    ) -> [PublishedFrameTiming; 2] {
        std::array::from_fn(|sensor_index| PublishedFrameTiming {
            sensor_index,
            sequence,
            published_monotonic_ns: deadline + latencies[sensor_index],
        })
    }

    fn scan_stats() -> ScanRuntimeStats {
        ScanRuntimeStats {
            sensors: ["lidar_1", "lidar_2"].map(|sensor_id| SensorRuntimeStats {
                sensor_id: sensor_id.to_owned(),
                published_frames: 30,
                frame_loss: 0,
                subscribers: 1,
            }),
        }
    }

    #[test]
    fn recorder_uses_open_start_closed_end_and_computes_batch_maximum() {
        let directory = tempdir().unwrap();
        let mut recorder = EdgeValidationRecorder::new(
            &settings(directory.path(), 2),
            10,
            ["lidar_1".into(), "lidar_2".into()],
            1.0,
        )
        .unwrap();
        recorder.observe_subscribers(&scan_stats()).unwrap();
        assert!(!recorder.record_batch(1_000_000_010, &[], &[]).unwrap());
        assert!(recorder.measures_deadline(1_000_000_011));
        assert!(
            !recorder
                .record_batch(2_000_000_010, &timings(10, 2_000_000_020), &[])
                .unwrap()
        );
        assert!(
            !recorder
                .record_batch(3_000_000_010, &timings(11, 3_000_000_030), &[])
                .unwrap()
        );
        assert!(recorder.record_batch(3_000_000_011, &[], &[]).unwrap());

        let mut final_scan_stats = scan_stats();
        final_scan_stats.sensors[0].frame_loss = 3;
        final_scan_stats
            .sensors
            .iter_mut()
            .for_each(|sensor| sensor.subscribers = 0);
        let report = recorder
            .finish(
                "run-a".into(),
                60,
                &final_scan_stats,
                ObservationPublisherStats::default(),
            )
            .unwrap();
        assert_eq!(report.completed_batch_count, 2);
        assert_eq!(report.sensors[0].sample_count, 2);
        assert_eq!(report.batch_max_samples[0].latency_ns, 11);
        assert_eq!(report.batch_max_samples[1].latency_ns, 21);
        assert!(
            report
                .scan_stream
                .iter()
                .all(|sensor| sensor.subscriber_seen)
        );
        assert_eq!(report.scan_stream[0].frame_loss, 3);
        assert_eq!(report.scan_stream[0].subscribers, 0);
        assert!(!report.overflowed);
        assert_eq!(report.dropped_sample_count, 0);
    }

    #[test]
    fn recorder_rejects_missing_duplicate_gap_and_overflow() {
        let directory = tempdir().unwrap();
        let base = settings(directory.path(), 1);
        let expected = ["lidar_1".to_owned(), "lidar_2".to_owned()];

        let mut missing = EdgeValidationRecorder::new(&base, 0, expected.clone(), 0.5).unwrap();
        assert!(matches!(
            missing.record_batch(1_000_000_001, &timings(1, 1_000_000_002)[..1], &[]),
            Err(EdgeValidationError::MissingSensor { .. })
        ));

        let mut duplicate = EdgeValidationRecorder::new(&base, 0, expected.clone(), 0.5).unwrap();
        let duplicated = [timings(1, 1_000_000_002)[0]; 2];
        assert!(matches!(
            duplicate.record_batch(1_000_000_001, &duplicated, &[]),
            Err(EdgeValidationError::DuplicateSensor { .. })
        ));

        let mut gap = EdgeValidationRecorder::new(&base, 0, expected.clone(), 0.5).unwrap();
        gap.record_batch(1_000_000_001, &timings(1, 1_000_000_002), &[])
            .unwrap();
        assert!(matches!(
            gap.record_batch(1_500_000_000, &timings(3, 1_500_000_001), &[]),
            Err(EdgeValidationError::SampleOverflow { capacity: 1 })
        ));

        let base = settings(directory.path(), 2);
        let mut gap = EdgeValidationRecorder::new(&base, 0, expected, 1.0).unwrap();
        gap.record_batch(1_000_000_001, &timings(1, 1_000_000_002), &[])
            .unwrap();
        assert!(matches!(
            gap.record_batch(1_500_000_000, &timings(3, 1_500_000_001), &[]),
            Err(EdgeValidationError::SequenceGap { .. })
        ));
    }

    #[test]
    fn scenario_transitions_retain_only_bounded_state_and_adjacent_latency() {
        let directory = tempdir().unwrap();
        let mut recorder = EdgeValidationRecorder::new(
            &settings(directory.path(), 4),
            0,
            ["lidar_1".into(), "lidar_2".into()],
            1.0,
        )
        .unwrap();
        recorder
            .record_batch(1_400_000_000, &timings(1, 1_400_000_010), &[])
            .unwrap();
        recorder
            .record_batch(
                1_600_000_000,
                &timings(2, 1_600_000_020),
                &[ScenarioPhaseTransition {
                    elapsed_s: 1.5,
                    cycle_index: 0,
                    phase: ScenarioPhase::Collecting,
                }],
            )
            .unwrap();
        recorder
            .record_batch(
                1_800_000_000,
                &timings(3, 1_800_000_030),
                &[ScenarioPhaseTransition {
                    elapsed_s: 1.7,
                    cycle_index: 1,
                    phase: ScenarioPhase::Filling,
                }],
            )
            .unwrap();

        assert_eq!(recorder.scenario_transitions.len(), 2);
        assert_eq!(recorder.scenario_current_state.cycle_index, 1);
        assert_eq!(recorder.scenario_transitions[0].before_batch.latency_ns, 11);
        assert_eq!(recorder.scenario_transitions[0].after_batch.latency_ns, 21);
        let encoded = serde_json::to_string(&recorder.scenario_transitions).unwrap();
        assert!(!encoded.contains("surface"));
        assert!(!encoded.contains("height"));
    }

    #[test]
    fn scenario_transition_capacity_rejects_the_sixty_fifth_transition() {
        let directory = tempdir().unwrap();
        let mut recorder = EdgeValidationRecorder::new(
            &settings(directory.path(), 100),
            0,
            ["lidar_1".into(), "lidar_2".into()],
            1.0,
        )
        .unwrap();
        recorder
            .record_batch(100_000_000, &timings(1, 100_000_001), &[])
            .unwrap();

        for index in 0..MAX_SCENARIO_TRANSITIONS {
            let deadline_ns = 120_000_000 + index as u64 * 20_000_000;
            let transition = if index % 2 == 0 {
                ScenarioPhaseTransition {
                    elapsed_s: (deadline_ns - 10_000_000) as f64 / 1_000_000_000.0,
                    cycle_index: index as u64 / 2,
                    phase: ScenarioPhase::Collecting,
                }
            } else {
                ScenarioPhaseTransition {
                    elapsed_s: (deadline_ns - 10_000_000) as f64 / 1_000_000_000.0,
                    cycle_index: index as u64 / 2 + 1,
                    phase: ScenarioPhase::Filling,
                }
            };
            recorder
                .record_batch(
                    deadline_ns,
                    &timings(index as u64 + 2, deadline_ns + 1),
                    &[transition],
                )
                .unwrap();
        }

        let deadline_ns = 120_000_000 + MAX_SCENARIO_TRANSITIONS as u64 * 20_000_000;
        let error = recorder
            .record_batch(
                deadline_ns,
                &timings(MAX_SCENARIO_TRANSITIONS as u64 + 2, deadline_ns + 1),
                &[ScenarioPhaseTransition {
                    elapsed_s: (deadline_ns - 10_000_000) as f64 / 1_000_000_000.0,
                    cycle_index: MAX_SCENARIO_TRANSITIONS as u64 / 2,
                    phase: ScenarioPhase::Collecting,
                }],
            )
            .unwrap_err();

        assert!(matches!(
            error,
            EdgeValidationError::ScenarioTransitionOverflow {
                capacity: MAX_SCENARIO_TRANSITIONS
            }
        ));
        assert_eq!(
            recorder.scenario_transitions.len(),
            MAX_SCENARIO_TRANSITIONS
        );
    }

    #[test]
    fn scenario_transitions_reject_discontinuous_state_and_invalid_latency_brackets() {
        let directory = tempdir().unwrap();
        let base = settings(directory.path(), 4);
        let expected = ["lidar_1".to_owned(), "lidar_2".to_owned()];

        let mut discontinuous =
            EdgeValidationRecorder::new(&base, 0, expected.clone(), 1.0).unwrap();
        discontinuous
            .record_batch(1_400_000_000, &timings(1, 1_400_000_010), &[])
            .unwrap();
        assert!(matches!(
            discontinuous.record_batch(
                1_600_000_000,
                &timings(2, 1_600_000_020),
                &[ScenarioPhaseTransition {
                    elapsed_s: 1.5,
                    cycle_index: 1,
                    phase: ScenarioPhase::Filling,
                }],
            ),
            Err(EdgeValidationError::Scenario(
                "phase and cycle transition is not contiguous"
            ))
        ));

        let mut unbracketed = EdgeValidationRecorder::new(&base, 0, expected, 1.0).unwrap();
        unbracketed
            .record_batch(1_400_000_000, &timings(1, 1_400_000_010), &[])
            .unwrap();
        assert!(matches!(
            unbracketed.record_batch(
                1_600_000_000,
                &timings(2, 1_600_000_020),
                &[ScenarioPhaseTransition {
                    elapsed_s: 1.4,
                    cycle_index: 0,
                    phase: ScenarioPhase::Collecting,
                }],
            ),
            Err(EdgeValidationError::Scenario(
                "transition is not bracketed by frame completion latency"
            ))
        ));
    }

    #[test]
    fn scenario_telemetry_serialization_matches_the_ingestion_fixture() {
        let directory = tempdir().unwrap();
        let settings = EdgeValidationSettings::new(
            EdgeValidationObservationMode::NoOp,
            Duration::from_secs(300),
            Duration::from_secs(3_600),
            4,
            directory.path().join("telemetry.json"),
        )
        .unwrap();
        let mut recorder = EdgeValidationRecorder::new(
            &settings,
            100_000_000_000,
            ["lidar_1".into(), "lidar_2".into()],
            0.001,
        )
        .unwrap();
        recorder
            .record_batch(
                400_100_000_000,
                &asymmetric_timings(10, 400_100_000_000, [1_000_000, 4_000_000]),
                &[],
            )
            .unwrap();
        recorder
            .record_batch(
                400_200_000_000,
                &asymmetric_timings(11, 400_200_000_000, [2_000_000, 5_000_000]),
                &[
                    ScenarioPhaseTransition {
                        elapsed_s: 300.15,
                        cycle_index: 0,
                        phase: ScenarioPhase::Collecting,
                    },
                    ScenarioPhaseTransition {
                        elapsed_s: 300.18,
                        cycle_index: 1,
                        phase: ScenarioPhase::Filling,
                    },
                ],
            )
            .unwrap();
        recorder
            .record_batch(
                4_000_000_000_000,
                &asymmetric_timings(12, 4_000_000_000_000, [3_000_000, 6_000_000]),
                &[],
            )
            .unwrap();
        assert!(recorder.record_batch(4_000_000_000_001, &[], &[]).unwrap());
        let report = recorder
            .finish(
                "fixture-run".into(),
                6,
                &scan_stats(),
                ObservationPublisherStats::default(),
            )
            .unwrap();
        let expected: serde_json::Value = serde_json::from_str(include_str!(
            "../../tests/fixtures/edge-validation/scenario-telemetry.v1.json"
        ))
        .unwrap();
        let actual = serde_json::to_value(report.scenario).unwrap();

        assert_eq!(actual, expected);
        assert!(!actual.to_string().contains("surface"));
        assert!(!actual.to_string().contains("height"));
    }

    #[test]
    fn atomic_writer_refuses_replacement_and_cleans_temporary_files() {
        let directory = tempdir().unwrap();
        let output = directory.path().join("telemetry.json");
        let mut recorder = EdgeValidationRecorder::new(
            &settings(directory.path(), 1),
            0,
            ["lidar_1".into(), "lidar_2".into()],
            0.5,
        )
        .unwrap();
        recorder
            .record_batch(3_000_000_000, &timings(1, 3_000_000_001), &[])
            .unwrap();
        recorder.record_batch(3_000_000_001, &[], &[]).unwrap();
        let report = recorder
            .finish(
                "run-a".into(),
                60,
                &scan_stats(),
                ObservationPublisherStats::default(),
            )
            .unwrap();

        write_edge_validation_report_atomic(&output, &report).unwrap();
        let decoded: serde_json::Value =
            serde_json::from_slice(&fs::read(&output).unwrap()).unwrap();
        assert_eq!(decoded["schema_version"], "edge-validation-telemetry.v1");
        assert!(matches!(
            write_edge_validation_report_atomic(&output, &report),
            Err(EdgeValidationError::OutputExists(_))
        ));
        assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 1);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(output).unwrap().permissions().mode() & 0o777,
                0o600
            );
        }
    }

    #[test]
    fn settings_reject_unbounded_or_unsafe_outputs() {
        let directory = tempdir().unwrap();
        assert!(
            EdgeValidationSettings::new(
                EdgeValidationObservationMode::Actual,
                Duration::ZERO,
                Duration::from_secs(1),
                1,
                directory.path().join("out.json")
            )
            .is_err()
        );
        assert!(
            EdgeValidationSettings::new(
                EdgeValidationObservationMode::Actual,
                Duration::from_secs(1),
                Duration::ZERO,
                1,
                directory.path().join("out.json")
            )
            .is_err()
        );
        assert!(
            EdgeValidationSettings::new(
                EdgeValidationObservationMode::Actual,
                Duration::from_secs(1),
                Duration::from_secs(1),
                MAX_SAMPLE_CAPACITY + 1,
                directory.path().join("out.json")
            )
            .is_err()
        );
        assert!(
            EdgeValidationSettings::new(
                EdgeValidationObservationMode::Actual,
                Duration::from_secs(1),
                Duration::from_secs(1),
                1,
                "relative.json"
            )
            .is_err()
        );
        let existing = directory.path().join("existing.json");
        fs::write(&existing, b"preserve").unwrap();
        assert!(matches!(
            EdgeValidationSettings::new(
                EdgeValidationObservationMode::Actual,
                Duration::from_secs(1),
                Duration::from_secs(1),
                1,
                existing
            ),
            Err(EdgeValidationError::OutputExists(_))
        ));
    }

    #[test]
    fn explicit_start_requires_one_second_of_setup_lead() {
        let directory = tempdir().unwrap();
        let base = settings(directory.path(), 1);
        assert_eq!(base.generation_epoch_monotonic_ns(100).unwrap(), 100);
        for requested in [99, 100] {
            let configured = base
                .clone()
                .with_start_at_monotonic_ns(Some(requested))
                .unwrap();
            assert!(matches!(
                configured.generation_epoch_monotonic_ns(100),
                Err(EdgeValidationError::StartInPast { .. })
            ));
        }
        let configured = base
            .clone()
            .with_start_at_monotonic_ns(Some(1_000_000_099))
            .unwrap();
        assert!(matches!(
            configured.generation_epoch_monotonic_ns(100),
            Err(EdgeValidationError::StartTooSoon { .. })
        ));
        let configured = base
            .with_start_at_monotonic_ns(Some(1_000_000_100))
            .unwrap();
        assert_eq!(
            configured.generation_epoch_monotonic_ns(100).unwrap(),
            1_000_000_100
        );
    }

    #[test]
    fn production_window_requires_exactly_thirty_six_thousand_samples() {
        assert_eq!(
            expected_sample_count(
                Duration::from_secs(DEFAULT_WARMUP_DURATION_S),
                Duration::from_secs(DEFAULT_MEASUREMENT_DURATION_S),
                10.0,
            )
            .unwrap(),
            36_000
        );
    }
}
