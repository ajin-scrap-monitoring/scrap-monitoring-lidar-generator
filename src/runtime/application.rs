//! Process lifecycle for paced generation and independent external outputs.

use std::{
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

#[cfg(unix)]
use tokio::signal::unix::{Signal, SignalKind};
use tokio::sync::{mpsc, watch};
use uuid::Uuid;

use crate::{
    cli::RuntimeSettings,
    configuration::GeneratorInputs,
    diagnostics::{
        BoundedDiagnosticsWriter, DiagnosticsError, DiagnosticsRecordInput,
        DiagnosticsWriterConfig, generator_input_fingerprint,
    },
    measurement::{MeasurementError, SystemScanFrameFactory},
    observation::{
        ObservationError, ObservationPublisherConfig, ObservationPublisherStats, ObservationScene,
        ObservationStreamHeader, TcpObservationPublisher,
    },
    scan_runtime::{GrpcScanRuntime, ScanRuntimeConfig, ScanRuntimeError, ScanRuntimeStats},
};

use super::{GenerationBatch, GenerationRuntime, GenerationRuntimeError};

const GENERATION_OUTPUT_CAPACITY: usize = 1;

#[derive(Debug, thiserror::Error)]
pub enum ApplicationError {
    #[error(transparent)]
    Diagnostics(#[from] DiagnosticsError),
    #[error(transparent)]
    Generation(#[from] GenerationRuntimeError),
    #[error(transparent)]
    Measurement(#[from] MeasurementError),
    #[error(transparent)]
    Observation(#[from] ObservationError),
    #[error(transparent)]
    Scan(#[from] ScanRuntimeError),
    #[error("generation coordinator could not start: {0}")]
    GenerationStart(#[source] std::io::Error),
    #[error("generation coordinator failed: {0}")]
    GenerationWorker(String),
    #[error("generation coordinator panicked")]
    GenerationPanic,
    #[error("generation coordinator join task failed: {0}")]
    GenerationJoin(#[source] tokio::task::JoinError),
    #[error("runtime signal registration failed: {0}")]
    Signal(#[source] std::io::Error),
    #[error("scan runtime failed: {0}")]
    ScanFatal(String),
    #[error("system wall clock precedes the Unix epoch")]
    ClockBeforeEpoch,
    #[error("system wall clock microseconds exceed the signed 64-bit range")]
    ClockOverflow,
    #[error("generation deadline is outside the host monotonic clock range")]
    DeadlineOverflow,
    #[error("generation coordinator stopped unexpectedly")]
    GenerationStopped,
}

pub type Result<T> = std::result::Result<T, ApplicationError>;

#[derive(Clone, Debug)]
pub struct ApplicationSummary {
    pub run_id: String,
    pub run_started_at_utc_us: i64,
    pub generated_scans: u64,
    pub scan_stats: ScanRuntimeStats,
    pub observation_endpoint: String,
    pub observation_stats: ObservationPublisherStats,
}

struct GeneratedOutput {
    batch: GenerationBatch,
    snapshot: crate::scenario::ScenarioModelSnapshot,
}

struct GenerationCoordinator {
    stop: Arc<AtomicBool>,
    receiver: mpsc::Receiver<GeneratedOutput>,
    thread: Option<JoinHandle<std::result::Result<(), String>>>,
}

impl GenerationCoordinator {
    fn start(mut runtime: GenerationRuntime, epoch: Instant) -> Result<Self> {
        let stop = Arc::new(AtomicBool::new(false));
        let worker_stop = Arc::clone(&stop);
        let (sender, receiver) = mpsc::channel(GENERATION_OUTPUT_CAPACITY);
        let worker = thread::Builder::new()
            .name("lidar-generation-coordinator".into())
            .spawn(move || {
                let result = run_generation_worker(&mut runtime, epoch, &worker_stop, &sender);
                let shutdown = runtime.shutdown().map_err(|error| error.to_string());
                result.and(shutdown)
            })
            .map_err(ApplicationError::GenerationStart)?;
        Ok(Self {
            stop,
            receiver,
            thread: Some(worker),
        })
    }

    async fn next(&mut self) -> Option<GeneratedOutput> {
        self.receiver.recv().await
    }

    async fn shutdown(&mut self) -> Result<()> {
        self.stop.store(true, Ordering::Release);
        self.receiver.close();
        let Some(worker) = self.thread.take() else {
            return Ok(());
        };
        worker.thread().unpark();
        match tokio::task::spawn_blocking(move || worker.join())
            .await
            .map_err(ApplicationError::GenerationJoin)?
        {
            Ok(Ok(())) => Ok(()),
            Ok(Err(error)) => Err(ApplicationError::GenerationWorker(error)),
            Err(_) => Err(ApplicationError::GenerationPanic),
        }
    }
}

impl Drop for GenerationCoordinator {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        self.receiver.close();
        if let Some(worker) = self.thread.take() {
            worker.thread().unpark();
        }
    }
}

fn run_generation_worker(
    runtime: &mut GenerationRuntime,
    epoch: Instant,
    stop: &AtomicBool,
    sender: &mpsc::Sender<GeneratedOutput>,
) -> std::result::Result<(), String> {
    while !stop.load(Ordering::Acquire) {
        let next_s = runtime.next_completion_elapsed_s();
        let offset = Duration::try_from_secs_f64(next_s)
            .map_err(|_| ApplicationError::DeadlineOverflow.to_string())?;
        let deadline = epoch
            .checked_add(offset)
            .ok_or_else(|| ApplicationError::DeadlineOverflow.to_string())?;
        while !stop.load(Ordering::Acquire) {
            let now = Instant::now();
            if now >= deadline {
                break;
            }
            thread::park_timeout(deadline.duration_since(now));
        }
        if stop.load(Ordering::Acquire) {
            return Ok(());
        }
        let batch = runtime
            .next_completed_scans()
            .map_err(|error| error.to_string())?;
        let snapshot = runtime
            .model_snapshot()
            .map_err(|error| error.to_string())?;
        if sender
            .blocking_send(GeneratedOutput { batch, snapshot })
            .is_err()
        {
            return if stop.load(Ordering::Acquire) {
                Ok(())
            } else {
                Err("generation output receiver stopped".to_owned())
            };
        }
    }
    Ok(())
}

pub async fn run_generator_application(
    inputs: GeneratorInputs,
    settings: RuntimeSettings,
) -> Result<ApplicationSummary> {
    let mut shutdown = ShutdownSignals::new()?;
    let generation = GenerationRuntime::from_inputs(&inputs)?;
    let sensor_ids: [String; 2] = inputs
        .environment
        .sensors
        .iter()
        .map(|sensor| sensor.sensor_id.clone())
        .collect::<Vec<_>>()
        .try_into()
        .map_err(|_| GenerationRuntimeError::SensorSet)?;
    let run_id = Uuid::new_v4().to_string();
    let fingerprint = generator_input_fingerprint(&inputs)?;
    let observation_config = ObservationPublisherConfig::from_transport(
        settings.observation_host.clone(),
        settings.observation_port,
        settings.observation_interval_s,
        &inputs.generator.observation_transport,
    )?;
    let observation_header = ObservationStreamHeader::new(
        inputs.environment.environment_id.clone(),
        run_id.clone(),
        fingerprint,
        inputs.generator.seed,
        ObservationScene::from_inputs(&inputs)?,
    )?;
    let mut observation = TcpObservationPublisher::new(observation_config, observation_header)?;
    let observation_endpoint = observation.endpoint();

    let scan_config = ScanRuntimeConfig {
        sensor_ids: sensor_ids.clone(),
        socket_directory: settings.grpc_socket_dir,
        status_directory: settings.status_dir,
        edge_id: settings.edge_id.clone(),
        config_revision: settings.config_revision.clone(),
        site_id: settings.site_id,
        deployment_revision: settings.deployment_revision,
        service_version: env!("CARGO_PKG_VERSION").to_owned(),
    };
    let mut scan = GrpcScanRuntime::start_system(scan_config).await?;
    let mut frame_factory = match SystemScanFrameFactory::with_system_clocks(
        sensor_ids,
        settings.edge_id,
        settings.config_revision,
        scan.instance_ids(),
    ) {
        Ok(factory) => factory,
        Err(error) => {
            scan.close().await;
            return Err(error.into());
        }
    };
    if let Err(error) = observation.start() {
        scan.close().await;
        return Err(error.into());
    }

    // Anchor simulated elapsed time only after external outputs are ready. This prevents a slow
    // socket or status initialization from turning the first generation cycle into a catch-up
    // burst while keeping diagnostic wall timestamps aligned with the simulation epoch.
    let run_started_at_utc_us = match unix_time_us() {
        Ok(timestamp) => timestamp,
        Err(error) => {
            observation.close().await;
            scan.close().await;
            return Err(error);
        }
    };
    let generation_epoch = Instant::now();
    let mut diagnostics = if inputs.generator.diagnostics.enabled {
        match DiagnosticsWriterConfig::from_inputs(&inputs, run_id.clone(), run_started_at_utc_us)
            .and_then(BoundedDiagnosticsWriter::start)
        {
            Ok(writer) => Some(writer),
            Err(error) => {
                observation.close().await;
                scan.close().await;
                return Err(error.into());
            }
        }
    } else {
        None
    };

    let mut generation = match GenerationCoordinator::start(generation, generation_epoch) {
        Ok(worker) => worker,
        Err(error) => {
            observation.close().await;
            if let Some(writer) = &mut diagnostics {
                writer.close().await;
            }
            scan.close().await;
            return Err(error);
        }
    };
    let mut fatal = scan.fatal_receiver();
    let mut generated_scans = 0_u64;
    let mut published_scans = 0_u64;
    let execution = run_until_shutdown(
        &mut generation,
        &mut shutdown,
        &mut frame_factory,
        &scan,
        &mut fatal,
        &mut observation,
        &mut diagnostics,
        &mut generated_scans,
        &mut published_scans,
    )
    .await;

    let generation_shutdown = generation.shutdown().await;
    observation.close().await;
    if let Some(writer) = &mut diagnostics {
        writer.close().await;
    }
    scan.close().await;
    let scan_stats = scan.stats();

    resolve_execution_result(execution, generation_shutdown)?;
    debug_assert_eq!(published_scans, total_published(&scan_stats));
    Ok(ApplicationSummary {
        run_id,
        run_started_at_utc_us,
        generated_scans,
        scan_stats,
        observation_endpoint,
        observation_stats: observation.stats(),
    })
}

#[allow(clippy::too_many_arguments)]
async fn run_until_shutdown(
    generation: &mut GenerationCoordinator,
    shutdown: &mut ShutdownSignals,
    frame_factory: &mut SystemScanFrameFactory,
    scan: &GrpcScanRuntime,
    fatal: &mut watch::Receiver<Option<String>>,
    observation: &mut TcpObservationPublisher,
    diagnostics: &mut Option<BoundedDiagnosticsWriter>,
    generated_scans: &mut u64,
    published_scans: &mut u64,
) -> Result<()> {
    loop {
        if let Some(error) = fatal.borrow().clone() {
            return Err(ApplicationError::ScanFatal(error));
        }
        tokio::select! {
            biased;
            signal = shutdown.wait() => {
                signal?;
                return Ok(());
            }
            changed = fatal.changed() => {
                if changed.is_err() {
                    return Err(ApplicationError::ScanFatal(
                        "scan fatal channel stopped".to_owned(),
                    ));
                }
            }
            output = generation.next() => {
                let Some(output) = output else {
                    return Err(ApplicationError::GenerationStopped);
                };
                let scan_count = u64::try_from(output.batch.scans.len())
                    .map_err(|_| GenerationRuntimeError::SensorConfiguration)?;
                for result in output.batch.scans {
                    if let Some(writer) = diagnostics
                        && writer.wants_record(result.sensor_id())?
                    {
                        let record = DiagnosticsRecordInput::from_measurement(
                            &result,
                            output.snapshot.clone(),
                        )?;
                        writer.try_record(record)?;
                    }
                    if let Some(frame) = frame_factory.build(result)? {
                        scan.publish(frame).await?;
                        *published_scans = published_scans.saturating_add(1);
                    }
                }
                *generated_scans = generated_scans.saturating_add(scan_count);
                if observation.is_due(output.batch.completed_at_s) {
                    let _ = observation.publish(output.snapshot);
                }
            }
        }
    }
}

struct ShutdownSignals {
    #[cfg(unix)]
    interrupt: Signal,
    #[cfg(unix)]
    terminate: Signal,
}

impl ShutdownSignals {
    fn new() -> Result<Self> {
        #[cfg(unix)]
        {
            Ok(Self {
                interrupt: tokio::signal::unix::signal(SignalKind::interrupt())
                    .map_err(ApplicationError::Signal)?,
                terminate: tokio::signal::unix::signal(SignalKind::terminate())
                    .map_err(ApplicationError::Signal)?,
            })
        }
        #[cfg(not(unix))]
        Ok(Self {})
    }

    async fn wait(&mut self) -> Result<()> {
        #[cfg(unix)]
        {
            tokio::select! {
                received = self.interrupt.recv() => signal_received(received, "SIGINT"),
                received = self.terminate.recv() => signal_received(received, "SIGTERM"),
            }
        }
        #[cfg(not(unix))]
        tokio::signal::ctrl_c()
            .await
            .map_err(ApplicationError::Signal)
    }
}

#[cfg(unix)]
fn signal_received(received: Option<()>, name: &'static str) -> Result<()> {
    if received.is_some() {
        Ok(())
    } else {
        Err(ApplicationError::Signal(std::io::Error::other(format!(
            "{name} signal stream stopped"
        ))))
    }
}

fn unix_time_us() -> Result<i64> {
    let elapsed = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| ApplicationError::ClockBeforeEpoch)?;
    i64::try_from(elapsed.as_micros()).map_err(|_| ApplicationError::ClockOverflow)
}

fn total_published(stats: &ScanRuntimeStats) -> u64 {
    stats
        .sensors
        .iter()
        .map(|sensor| sensor.published_frames)
        .sum()
}

fn resolve_execution_result(execution: Result<()>, shutdown: Result<()>) -> Result<()> {
    match (execution, shutdown) {
        (Err(ApplicationError::GenerationStopped), Err(error)) => Err(error),
        (Err(error), _) | (Ok(()), Err(error)) => Err(error),
        (Ok(()), Ok(())) => Ok(()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn worker_failure_has_priority_over_closed_output_channel() {
        let error = resolve_execution_result(
            Err(ApplicationError::GenerationStopped),
            Err(ApplicationError::GenerationWorker(
                "worker failed".to_owned(),
            )),
        )
        .unwrap_err();
        assert!(matches!(error, ApplicationError::GenerationWorker(_)));
    }

    #[test]
    fn primary_execution_failure_has_priority_over_shutdown_failure() {
        let error = resolve_execution_result(
            Err(ApplicationError::ScanFatal("scan failed".to_owned())),
            Err(ApplicationError::GenerationWorker(
                "worker failed".to_owned(),
            )),
        )
        .unwrap_err();
        assert!(matches!(error, ApplicationError::ScanFatal(_)));
    }
}
