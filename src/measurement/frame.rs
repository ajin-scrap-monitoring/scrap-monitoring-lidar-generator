//! Conversion from completed measured scans to the external ScanFrame contract.

use std::collections::{BTreeMap, BTreeSet};

use crate::wire::{ScanFrame, ScanSample};

use super::{MeasurementError, MeasurementResult, Result};

const ANGLE_Q14_SCALE: u64 = 16_384;
const ANGLE_MDEG_PER_QUADRANT: u64 = 90_000;
const ANGLE_MDEG_PER_ROTATION: u64 = 360_000;
const DISTANCE_Q2_PER_MM: u64 = 4;
const NANOSECONDS_PER_SECOND: f64 = 1_000_000_000.0;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ClockSample {
    pub monotonic_ns: u64,
    pub unix_ms: i64,
}

pub struct ScanFrameFactory<Monotonic, Unix>
where
    Monotonic: FnMut() -> u64,
    Unix: FnMut() -> i64,
{
    sensor_ids: BTreeSet<String>,
    edge_id: String,
    config_revision: String,
    instance_ids: BTreeMap<String, String>,
    monotonic_ns: Monotonic,
    unix_ms: Unix,
    previous_monotonic_ns: BTreeMap<String, u64>,
    sequences: BTreeMap<String, u64>,
}

impl<Monotonic, Unix> ScanFrameFactory<Monotonic, Unix>
where
    Monotonic: FnMut() -> u64,
    Unix: FnMut() -> i64,
{
    pub fn new(
        sensor_ids: impl IntoIterator<Item = String>,
        edge_id: impl Into<String>,
        config_revision: impl Into<String>,
        instance_ids: BTreeMap<String, String>,
        monotonic_ns: Monotonic,
        unix_ms: Unix,
    ) -> Result<Self> {
        let sensors: Vec<_> = sensor_ids.into_iter().collect();
        let sensor_ids: BTreeSet<_> = sensors.iter().cloned().collect();
        let edge_id = edge_id.into();
        let config_revision = config_revision.into();
        if sensor_ids.is_empty()
            || sensor_ids.len() != sensors.len()
            || instance_ids.keys().collect::<BTreeSet<_>>()
                != sensor_ids.iter().collect::<BTreeSet<_>>()
            || instance_ids.values().any(|value| value.is_empty())
            || !driver_identity(&edge_id)
            || !driver_identity(&config_revision)
        {
            return Err(MeasurementError::Invalid(
                "scan frame identities or sensor set are invalid",
            ));
        }
        let sequences = sensor_ids
            .iter()
            .map(|sensor_id| (sensor_id.clone(), 0))
            .collect();
        Ok(Self {
            sensor_ids,
            edge_id,
            config_revision,
            instance_ids,
            monotonic_ns,
            unix_ms,
            previous_monotonic_ns: BTreeMap::new(),
            sequences,
        })
    }

    pub fn with_instance_id_source(
        sensor_ids: impl IntoIterator<Item = String>,
        edge_id: impl Into<String>,
        config_revision: impl Into<String>,
        mut instance_id: impl FnMut(&str) -> String,
        monotonic_ns: Monotonic,
        unix_ms: Unix,
    ) -> Result<Self> {
        let sensors: Vec<_> = sensor_ids.into_iter().collect();
        let instance_ids = sensors
            .iter()
            .map(|sensor_id| (sensor_id.clone(), instance_id(sensor_id)))
            .collect();
        Self::new(
            sensors,
            edge_id,
            config_revision,
            instance_ids,
            monotonic_ns,
            unix_ms,
        )
    }

    pub fn build(&mut self, result: &MeasurementResult) -> Result<Option<ScanFrame>> {
        let sensor_id = result.sensor_id();
        if !self.sensor_ids.contains(sensor_id) {
            return Err(MeasurementError::Invalid(
                "measurement sensor is unknown to the frame factory",
            ));
        }
        let samples = normalized_samples(result)?;
        let monotonic_ns = (self.monotonic_ns)();
        let unix_ms = (self.unix_ms)();
        let Some(previous) = self.previous_monotonic_ns.get(sensor_id).copied() else {
            self.previous_monotonic_ns
                .insert(sensor_id.to_owned(), monotonic_ns);
            return Ok(None);
        };
        let elapsed_ns = monotonic_ns
            .checked_sub(previous)
            .filter(|elapsed| *elapsed > 0)
            .ok_or(MeasurementError::Clock(
                "scan completion clock must increase per sensor",
            ))?;
        let scan_hz = NANOSECONDS_PER_SECOND / elapsed_ns as f64;
        if !scan_hz.is_finite() || scan_hz <= 0.0 {
            return Err(MeasurementError::Clock(
                "calculated scan rate must be finite and positive",
            ));
        }
        let sequence = self
            .sequences
            .get(sensor_id)
            .copied()
            .and_then(|value| value.checked_add(1))
            .ok_or(MeasurementError::Exhausted(
                "scan frame sequence range is exhausted",
            ))?;
        self.previous_monotonic_ns
            .insert(sensor_id.to_owned(), monotonic_ns);
        self.sequences.insert(sensor_id.to_owned(), sequence);
        Ok(Some(ScanFrame {
            schema_version: "1.0".to_owned(),
            edge_id: self.edge_id.clone(),
            sensor_id: sensor_id.to_owned(),
            sequence,
            acquired_at_unix_ms: unix_ms,
            acquired_monotonic_ns: monotonic_ns,
            sdk_status: "OK".to_owned(),
            scan_hz,
            samples,
            instance_id: self
                .instance_ids
                .get(sensor_id)
                .expect("constructor validated instance identifiers")
                .clone(),
            config_revision: self.config_revision.clone(),
        }))
    }
}

fn normalized_samples(result: &MeasurementResult) -> Result<Vec<ScanSample>> {
    let scan = result.measured();
    let mut samples = Vec::new();
    samples
        .try_reserve_exact(scan.angles_deg().len())
        .map_err(|_| MeasurementError::Exhausted("scan sample allocation failed"))?;
    for ((&angle, &distance), &quality) in scan
        .angles_deg()
        .iter()
        .zip(scan.distances_m())
        .zip(scan.qualities())
    {
        let angle_tick = u64::from(super::sdk::quantize_hq_angle_ticks(angle)?);
        let angle_mdeg = (angle_tick
            .checked_mul(ANGLE_MDEG_PER_QUADRANT)
            .and_then(|value| value.checked_add(ANGLE_Q14_SCALE / 2))
            .ok_or(MeasurementError::Exhausted(
                "HQ angle conversion overflowed",
            ))?
            / ANGLE_Q14_SCALE)
            % ANGLE_MDEG_PER_ROTATION;
        let distance_tick = super::sdk::quantize_hq_distance_ticks(distance)?;
        let distance_mm = distance_tick / DISTANCE_Q2_PER_MM;
        samples.push(ScanSample {
            angle_mdeg: u32::try_from(angle_mdeg)
                .map_err(|_| MeasurementError::Exhausted("angle millidegree value exceeds u32"))?,
            distance_mm: u32::try_from(distance_mm)
                .map_err(|_| MeasurementError::Exhausted("distance value exceeds u32"))?,
            quality: u32::from(quality >> 2),
        });
    }
    samples.sort_by_key(|sample| sample.angle_mdeg);
    Ok(samples)
}

fn driver_identity(value: &str) -> bool {
    let bytes = value.as_bytes();
    (1..=64).contains(&bytes.len())
        && bytes[0].is_ascii_alphanumeric()
        && bytes[1..]
            .iter()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'.' | b'-'))
}
