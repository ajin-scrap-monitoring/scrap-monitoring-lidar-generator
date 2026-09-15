//! Driver-compatible status schema and atomic file replacement.

use std::{
    fs::{File, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};

use serde::Serialize;
use time::{OffsetDateTime, macros::format_description};
use uuid::Uuid;

use super::{ClockReading, Result, ScanRuntimeError, io_error};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StatusIdentity {
    pub service: String,
    pub edge_id: String,
    pub sensor_id: String,
    pub config_revision: String,
    pub instance_id: String,
    pub service_version: String,
    pub site_id: String,
    pub deployment_revision: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct DriverStatus {
    pub schema_version: &'static str,
    pub service: String,
    pub edge_id: String,
    pub sensor_id: String,
    pub config_revision: String,
    pub instance_id: String,
    pub started_at: String,
    pub updated_at: String,
    pub last_progress_at: String,
    pub state: &'static str,
    pub reason_codes: Vec<String>,
    pub sequence: u64,
    pub frame_loss: u64,
    pub sdk_errors: u64,
    pub last_scan_unix_ms: i64,
    pub reported_at: String,
    pub service_version: String,
    pub site_id: String,
    pub deployment_revision: String,
}

impl DriverStatus {
    pub fn new(
        identity: &StatusIdentity,
        started_at_unix_ms: i64,
        now: ClockReading,
        last_progress_monotonic_ns: Option<u64>,
        sequence: u64,
        frame_loss: u64,
        last_scan_unix_ms: i64,
    ) -> Result<Self> {
        let progress_monotonic_ns = last_progress_monotonic_ns.unwrap_or(now.monotonic_ns);
        let elapsed_ms =
            now.monotonic_ns
                .checked_sub(progress_monotonic_ns)
                .ok_or(ScanRuntimeError::Clock(
                    "status monotonic clock precedes last progress",
                ))?
                / 1_000_000;
        let elapsed_ms = i64::try_from(elapsed_ms)
            .map_err(|_| ScanRuntimeError::Clock("status elapsed time exceeds i64"))?;
        let last_progress_unix_ms =
            now.unix_ms
                .checked_sub(elapsed_ms)
                .ok_or(ScanRuntimeError::Clock(
                    "status progress wall clock is outside i64",
                ))?;
        let now_text = format_unix_ms(now.unix_ms)?;
        Ok(Self {
            schema_version: "1.0",
            service: identity.service.clone(),
            edge_id: identity.edge_id.clone(),
            sensor_id: identity.sensor_id.clone(),
            config_revision: identity.config_revision.clone(),
            instance_id: identity.instance_id.clone(),
            started_at: format_unix_ms(started_at_unix_ms)?,
            updated_at: now_text.clone(),
            last_progress_at: format_unix_ms(last_progress_unix_ms)?,
            state: if sequence == 0 { "STARTING" } else { "HEALTHY" },
            reason_codes: Vec::new(),
            sequence,
            frame_loss,
            sdk_errors: 0,
            last_scan_unix_ms,
            reported_at: now_text,
            service_version: identity.service_version.clone(),
            site_id: identity.site_id.clone(),
            deployment_revision: identity.deployment_revision.clone(),
        })
    }
}

pub fn write_atomic_status(path: &Path, status: &DriverStatus, temporary_id: Uuid) -> Result<()> {
    prepare_atomic_status(path, status, temporary_id)?.commit()
}

pub(crate) fn temporary_status_path(path: &Path, temporary_id: Uuid) -> Result<PathBuf> {
    let parent = path.parent().ok_or_else(|| {
        ScanRuntimeError::Invalid(format!("status path has no parent: {}", path.display()))
    })?;
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| {
            ScanRuntimeError::Invalid(format!("status path is not UTF-8: {}", path.display()))
        })?;
    Ok(parent.join(format!(".{name}.{temporary_id}.tmp")))
}

pub(crate) struct PreparedStatus {
    path: PathBuf,
    temporary: Option<PathBuf>,
}

impl PreparedStatus {
    pub(crate) fn commit(mut self) -> Result<()> {
        let temporary = self
            .temporary
            .as_ref()
            .expect("prepared status owns its temporary file");
        std::fs::rename(temporary, &self.path)
            .map_err(|source| io_error("status rename", &self.path, source))?;
        self.temporary = None;
        Ok(())
    }
}

impl Drop for PreparedStatus {
    fn drop(&mut self) {
        if let Some(temporary) = &self.temporary {
            let _ = std::fs::remove_file(temporary);
        }
    }
}

pub(crate) fn prepare_atomic_status(
    path: &Path,
    status: &DriverStatus,
    temporary_id: Uuid,
) -> Result<PreparedStatus> {
    let temporary = temporary_status_path(path, temporary_id)?;
    let mut bytes = serde_json::to_vec(status)?;
    if !bytes.is_ascii() {
        return Err(ScanRuntimeError::Invalid(
            "status fields must encode as ASCII".to_owned(),
        ));
    }
    bytes.push(b'\n');
    let mut file = open_new(&temporary)?;
    let write_result = (|| {
        file.write_all(&bytes)
            .map_err(|source| io_error("status write", &temporary, source))?;
        file.flush()
            .map_err(|source| io_error("status flush", &temporary, source))?;
        file.sync_all()
            .map_err(|source| io_error("status fsync", &temporary, source))?;
        Ok(())
    })();
    drop(file);
    if let Err(error) = write_result {
        let _ = std::fs::remove_file(temporary);
        return Err(error);
    }
    Ok(PreparedStatus {
        path: path.to_owned(),
        temporary: Some(temporary),
    })
}

fn open_new(path: &Path) -> Result<File> {
    OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|source| io_error("status temporary create", path, source))
}

fn format_unix_ms(unix_ms: i64) -> Result<String> {
    const MILLIS_PER_SECOND: i64 = 1_000;
    const NANOS_PER_MILLISECOND: i128 = 1_000_000;
    let nanos = i128::from(unix_ms)
        .checked_mul(NANOS_PER_MILLISECOND)
        .ok_or(ScanRuntimeError::StatusTimestampRange)?;
    let time = OffsetDateTime::from_unix_timestamp_nanos(nanos)
        .map_err(|_| ScanRuntimeError::StatusTimestampRange)?;
    debug_assert_eq!(unix_ms.div_euclid(MILLIS_PER_SECOND), time.unix_timestamp());
    time.format(format_description!(
        "[year]-[month]-[day]T[hour]:[minute]:[second].[subsecond digits:3]Z"
    ))
    .map_err(Into::into)
}

pub(crate) fn status_path(root: &Path, service: &str) -> PathBuf {
    root.join(service).join(format!("{service}.json"))
}
