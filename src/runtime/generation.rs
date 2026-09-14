//! Time-ordered scenario coordination with one fixed worker per sensor.

use std::{
    sync::{
        Arc,
        mpsc::{Receiver, SyncSender, sync_channel},
    },
    thread::{self, JoinHandle},
};

use crate::{
    configuration::{GeneratorInputs, SensorConfig},
    geometry::{GeometryError, Polygon2, Vec2, Vec3},
    measurement::{
        CollectionOcclusionSettings, DistortionInterval, DropoutSettings, EnvironmentScene,
        FallingMaterialSettings, MeasurementError, MeasurementGenerator, MeasurementResult,
        MeasurementSettings, QualityDistribution, ReferenceScanner, ScheduledScan, SensorFrame,
        SensorRotationScheduler, SnapshotEventCoordinator, SpatialDistortionResolver,
        SpatialDistortionTimeline, VoidSettings, create_seeded_rotation_scheduler,
    },
    scenario::{
        ScenarioError, ScenarioModelSnapshot, ScenarioSimulator, build_scenario_simulator,
        scale_duration_range, scenario_time_scale,
    },
};

const SENSOR_WORKER_QUEUE_CAPACITY: usize = 1;

#[derive(Debug, thiserror::Error)]
pub enum GenerationRuntimeError {
    #[error(transparent)]
    Geometry(#[from] GeometryError),
    #[error(transparent)]
    Measurement(#[from] MeasurementError),
    #[error(transparent)]
    Rotation(#[from] crate::measurement::rotation::RotationError),
    #[error(transparent)]
    Scenario(#[from] ScenarioError),
    #[error("sensor worker could not start: {0}")]
    WorkerStart(#[source] std::io::Error),
    #[error("sensor worker stopped before accepting its scan")]
    WorkerRequest,
    #[error("sensor worker stopped before returning its scan")]
    WorkerResponse,
    #[error("sensor worker panicked")]
    WorkerPanic,
    #[error("generation runtime requires exactly two unique sensors")]
    SensorSet,
    #[error("generation runtime sensor configuration is inconsistent")]
    SensorConfiguration,
    #[error("scaled spatial event rate is outside the finite range")]
    EventRate,
}

pub type Result<T> = std::result::Result<T, GenerationRuntimeError>;

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct GenerationRuntimeStats {
    pub completed_batches: u64,
    pub generated_scans: u64,
}

#[derive(Debug)]
pub struct GenerationBatch {
    pub completed_at_s: f64,
    pub scans: Vec<MeasurementResult>,
}

struct SensorCoordinator {
    sensor_id: String,
    scheduler: SensorRotationScheduler,
    pending: Option<ScheduledScan>,
    request: SyncSender<WorkerCommand>,
    response: Receiver<std::result::Result<MeasurementResult, MeasurementError>>,
    worker: Option<JoinHandle<()>>,
}

enum WorkerCommand {
    Generate {
        schedule: ScheduledScan,
        snapshots: Arc<SnapshotEventCoordinator>,
        spatial: Option<Arc<SpatialDistortionResolver>>,
    },
    Stop,
}

pub struct GenerationRuntime {
    scenario: ScenarioSimulator,
    snapshots: SnapshotEventCoordinator,
    spatial: Option<SpatialDistortionTimeline>,
    sensors: Vec<SensorCoordinator>,
    stats: GenerationRuntimeStats,
}

impl GenerationRuntime {
    pub fn from_inputs(inputs: &GeneratorInputs) -> Result<Self> {
        if inputs.environment.sensors.len() != 2
            || inputs.environment.sensors[0].sensor_id == inputs.environment.sensors[1].sensor_id
        {
            return Err(GenerationRuntimeError::SensorSet);
        }

        let scenario = build_scenario_simulator(inputs)?;
        let initial_surface = scenario.surface().surface_snapshot()?;
        let snapshots = SnapshotEventCoordinator::new(initial_surface);
        let scene = Arc::new(build_environment_scene(inputs)?);
        let spatial = build_spatial_timeline(inputs, scene.as_ref().clone())?;
        let mut sensors = Vec::with_capacity(2);
        for sensor in &inputs.environment.sensors {
            sensors.push(start_sensor_worker(
                inputs,
                sensor,
                Arc::clone(&scene),
                &snapshots,
            )?);
        }

        Ok(Self {
            scenario,
            snapshots,
            spatial,
            sensors,
            stats: GenerationRuntimeStats::default(),
        })
    }

    pub fn sensor_ids(&self) -> impl ExactSizeIterator<Item = &str> {
        self.sensors.iter().map(|sensor| sensor.sensor_id.as_str())
    }

    pub fn next_completion_elapsed_s(&self) -> f64 {
        self.sensors
            .iter()
            .filter_map(|sensor| sensor.pending.as_ref())
            .map(ScheduledScan::completed_at_s)
            .fold(f64::INFINITY, f64::min)
    }

    pub fn earliest_pending_elapsed_s(&self) -> f64 {
        self.sensors
            .iter()
            .filter_map(|sensor| sensor.pending.as_ref())
            .map(ScheduledScan::captured_elapsed_s)
            .fold(f64::INFINITY, f64::min)
    }

    pub fn elapsed_s(&self) -> f64 {
        self.scenario.elapsed_s()
    }

    pub fn model_snapshot(&self) -> Result<ScenarioModelSnapshot> {
        Ok(self.scenario.observation_snapshot()?)
    }

    pub fn stats(&self) -> GenerationRuntimeStats {
        self.stats
    }

    pub fn next_completed_scans(&mut self) -> Result<GenerationBatch> {
        let completion_s = self.next_completion_elapsed_s();
        if !completion_s.is_finite() {
            return Err(GenerationRuntimeError::SensorConfiguration);
        }
        self.advance_scenario_and_distortions(completion_s)?;

        let snapshots = Arc::new(self.snapshots.clone());
        let spatial = self
            .spatial
            .as_ref()
            .map(|timeline| Arc::new(timeline.resolver().clone()));
        let mut requested = Vec::with_capacity(self.sensors.len());
        for (index, sensor) in self.sensors.iter_mut().enumerate() {
            let pending = sensor
                .pending
                .as_ref()
                .ok_or(GenerationRuntimeError::SensorConfiguration)?;
            if pending.completed_at_s().to_bits() != completion_s.to_bits() {
                continue;
            }
            let next = sensor.scheduler.next_scan()?;
            let schedule = sensor
                .pending
                .replace(next)
                .ok_or(GenerationRuntimeError::SensorConfiguration)?;
            sensor
                .request
                .send(WorkerCommand::Generate {
                    schedule,
                    snapshots: Arc::clone(&snapshots),
                    spatial: spatial.as_ref().map(Arc::clone),
                })
                .map_err(|_| GenerationRuntimeError::WorkerRequest)?;
            requested.push(index);
        }

        let mut scans = Vec::with_capacity(requested.len());
        for index in requested {
            let result = self.sensors[index]
                .response
                .recv()
                .map_err(|_| GenerationRuntimeError::WorkerResponse)??;
            scans.push(result);
        }
        if scans.is_empty() {
            return Err(GenerationRuntimeError::SensorConfiguration);
        }

        let earliest_pending_s = self.earliest_pending_elapsed_s();
        self.snapshots
            .discard_before_earliest_pending(earliest_pending_s)?;
        if let Some(spatial) = &mut self.spatial {
            spatial.discard_before(earliest_pending_s)?;
        }
        self.stats.completed_batches = self.stats.completed_batches.saturating_add(1);
        self.stats.generated_scans = self
            .stats
            .generated_scans
            .saturating_add(scans.len() as u64);
        Ok(GenerationBatch {
            completed_at_s: completion_s,
            scans,
        })
    }

    fn advance_scenario_and_distortions(&mut self, through_s: f64) -> Result<()> {
        while self.scenario.elapsed_s() < through_s {
            let started_at_s = self.scenario.elapsed_s();
            let ends_at_s = self.scenario.next_surface_event_elapsed_s()?.min(through_s);
            if let Some(spatial) = &mut self.spatial {
                let surface = self.scenario.surface().surface_snapshot()?;
                spatial.advance_to(DistortionInterval {
                    started_at_s,
                    ends_at_s,
                    phase: self.scenario.active_phase()?.into(),
                    surface: &surface,
                })?;
            }
            let advance = self.scenario.advance_to(ends_at_s)?;
            for event in advance.events {
                self.snapshots
                    .push_event(event.elapsed_s, event.model.surface)?;
            }
        }
        Ok(())
    }
}

impl Drop for GenerationRuntime {
    fn drop(&mut self) {
        for sensor in &self.sensors {
            let _ = sensor.request.send(WorkerCommand::Stop);
        }
        for sensor in &mut self.sensors {
            if let Some(worker) = sensor.worker.take() {
                let _ = worker.join();
            }
        }
    }
}

fn start_sensor_worker(
    inputs: &GeneratorInputs,
    sensor: &SensorConfig,
    scene: Arc<EnvironmentScene>,
    snapshots: &SnapshotEventCoordinator,
) -> Result<SensorCoordinator> {
    let measurement = &inputs.generator.measurement;
    let mut scheduler = create_seeded_rotation_scheduler(
        sensor.sensor_id.clone(),
        measurement.sample_rate_hz,
        measurement.rotation_rate_hz,
        inputs.generator.seed,
    )?;
    let pending = scheduler.next_scan()?;
    let mut scanner = ReferenceScanner::new(
        sensor.sensor_id.clone(),
        sensor_frame(sensor)?,
        measurement.min_distance_m,
        measurement.max_distance_m,
    )?;
    // Warm every angle used by the first rotation before the real-time epoch starts.
    scanner.generate_with_snapshots(scene.as_ref(), snapshots, pending.clone())?;
    let mut generator = measurement_generator(inputs, &sensor.sensor_id)?;
    let (request, requests) = sync_channel(SENSOR_WORKER_QUEUE_CAPACITY);
    let (responses, response) = sync_channel(SENSOR_WORKER_QUEUE_CAPACITY);
    let sensor_id = sensor.sensor_id.clone();
    let worker_name = format!("lidar-sensor-{}", sensor.sensor_id);
    let worker = thread::Builder::new()
        .name(worker_name)
        .spawn(move || {
            while let Ok(command) = requests.recv() {
                let WorkerCommand::Generate {
                    schedule,
                    snapshots,
                    spatial,
                } = command
                else {
                    return;
                };
                let result = scanner
                    .generate_with_snapshots(scene.as_ref(), snapshots.as_ref(), schedule)
                    .and_then(|reference| generator.generate(reference, spatial.as_deref()));
                if responses.send(result).is_err() {
                    return;
                }
            }
        })
        .map_err(GenerationRuntimeError::WorkerStart)?;
    Ok(SensorCoordinator {
        sensor_id,
        scheduler,
        pending: Some(pending),
        request,
        response,
        worker: Some(worker),
    })
}

fn build_environment_scene(inputs: &GeneratorInputs) -> Result<EnvironmentScene> {
    Ok(EnvironmentScene::new(
        boundary(inputs)?,
        inputs.environment.floor_z_m,
        inputs.environment.top_z_m,
        Vec::new(),
    )?)
}

fn sensor_frame(sensor: &SensorConfig) -> Result<SensorFrame> {
    Ok(SensorFrame::new(
        Vec3::try_from(sensor.p0_m)?,
        Vec3::try_from(sensor.u0)?,
        Vec3::try_from(sensor.u90)?,
    )?)
}

fn measurement_generator(
    inputs: &GeneratorInputs,
    sensor_id: &str,
) -> Result<MeasurementGenerator> {
    let measurement = &inputs.generator.measurement;
    let quality = inputs
        .quality_profile
        .sensors
        .iter()
        .find(|quality| quality.sensor_id == sensor_id)
        .ok_or(GenerationRuntimeError::SensorConfiguration)?;
    let time_scale = scenario_time_scale(inputs.generator.scenario.mean_fill_duration_s)?;
    let dropout = &measurement.distortions.dropout;
    Ok(MeasurementGenerator::new(
        MeasurementSettings {
            sensor_id: sensor_id.to_owned(),
            min_distance_m: measurement.min_distance_m,
            max_distance_m: measurement.max_distance_m,
            noise_enabled: measurement.distance_noise.enabled,
            noise_standard_deviation_m: measurement.distance_noise.standard_deviation_m,
            noise_limit_m: measurement.distance_noise.limit_m,
            reflection_error_enabled: measurement.distortions.reflection_error.enabled,
            reflection_error_probability: measurement.distortions.reflection_error.probability,
            reflection_error_reduction_range_m: measurement
                .distortions
                .reflection_error
                .distance_reduction_m_range,
            valid_quality: QualityDistribution::new(quality.valid_distance_frequencies)?,
            invalid_quality: QualityDistribution::new(quality.invalid_distance_frequencies)?,
            dropout: dropout
                .enabled
                .then(|| -> Result<DropoutSettings> {
                    Ok(DropoutSettings {
                        event_interval_s_range: scale_duration_range(
                            dropout.event_interval_s_range,
                            time_scale,
                        )?,
                        duration_s_range: scale_duration_range(
                            dropout.duration_s_range,
                            time_scale,
                        )?,
                    })
                })
                .transpose()?,
        },
        inputs.generator.seed,
    )?)
}

fn build_spatial_timeline(
    inputs: &GeneratorInputs,
    static_scene: EnvironmentScene,
) -> Result<Option<SpatialDistortionTimeline>> {
    let generator = &inputs.generator;
    let distortions = &generator.measurement.distortions;
    if !distortions.falling_material.enabled
        && !distortions.voids.enabled
        && !distortions.collection_occlusion.enabled
    {
        return Ok(None);
    }
    let time_scale = scenario_time_scale(generator.scenario.mean_fill_duration_s)?;
    let falling = &distortions.falling_material;
    let voids = &distortions.voids;
    let collection = &distortions.collection_occlusion;
    let timeline = SpatialDistortionTimeline::new(
        boundary(inputs)?,
        static_scene,
        falling
            .enabled
            .then(|| -> Result<FallingMaterialSettings> {
                let rate = falling.event_rate_per_s / time_scale;
                if !rate.is_finite() || rate < 0.0 {
                    return Err(GenerationRuntimeError::EventRate);
                }
                Ok(FallingMaterialSettings {
                    event_rate_per_s: rate,
                    radius_m_range: falling.radius_m_range,
                    duration_s_range: scale_duration_range(falling.duration_s_range, time_scale)?,
                    distance_reduction_m_range: falling.distance_reduction_m_range,
                    inlet_positions: generator
                        .scenario
                        .inlet_positions_xy_m
                        .iter()
                        .copied()
                        .map(Vec2::try_from)
                        .collect::<std::result::Result<Vec<_>, _>>()?,
                    placement_radius_m: generator.scenario.surface.pile_spread_radius_m,
                })
            })
            .transpose()?,
        voids
            .enabled
            .then(|| -> Result<VoidSettings> {
                Ok(VoidSettings {
                    surface_area_ratio: voids.surface_area_ratio,
                    radius_m_range: voids.radius_m_range,
                    duration_s_range: scale_duration_range(voids.duration_s_range, time_scale)?,
                    cover_height_increase_m: voids.cover_height_increase_m,
                    distance_increase_m_range: voids.distance_increase_m_range,
                })
            })
            .transpose()?,
        collection
            .enabled
            .then(|| -> Result<CollectionOcclusionSettings> {
                Ok(CollectionOcclusionSettings {
                    event_interval_s_range: scale_duration_range(
                        collection.event_interval_s_range,
                        time_scale,
                    )?,
                    radius_m_range: collection.radius_m_range,
                    duration_s_range: scale_duration_range(
                        collection.duration_s_range,
                        time_scale,
                    )?,
                    distance_reduction_m_range: collection.distance_reduction_m_range,
                })
            })
            .transpose()?,
        generator.seed,
    )?;
    Ok(Some(timeline))
}

fn boundary(inputs: &GeneratorInputs) -> Result<Polygon2> {
    Ok(Polygon2::new(
        inputs
            .environment
            .boundary_xy_m
            .iter()
            .copied()
            .map(Vec2::try_from)
            .collect::<std::result::Result<Vec<_>, _>>()?,
    )?)
}
