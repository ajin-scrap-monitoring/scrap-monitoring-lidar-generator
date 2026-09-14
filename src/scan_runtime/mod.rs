//! Bounded scan publication, UDS serving, and driver-compatible status output.

mod buffer;
mod clock;
mod server;
mod status;

pub use buffer::{LatestTwo, RingDelivery, RingError};
pub use clock::{ClockReading, RuntimeClock, SystemRuntimeClock};
pub use server::{
    GrpcScanRuntime, RuntimeIdSource, ScanRuntimeConfig, ScanRuntimeStats, SensorRuntimeStats,
    SystemRuntimeIdSource,
};
pub use status::{DriverStatus, StatusIdentity, write_atomic_status};

pub const MAX_GRPC_MESSAGE_BYTES: usize = 4 * 1024 * 1024;
pub const MAX_SUBSCRIBERS_PER_SENSOR: usize = 8;
pub const STATUS_INTERVAL: std::time::Duration = std::time::Duration::from_secs(2);
pub const GRACEFUL_STOP_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(2);

#[derive(Debug, thiserror::Error)]
pub enum ScanRuntimeError {
    #[error("{0}")]
    Invalid(String),
    #[error("{operation} failed for {path}: {source}")]
    Io {
        operation: &'static str,
        path: std::path::PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("{0}")]
    Exhausted(&'static str),
    #[error("gRPC lane failed: {0}")]
    Grpc(String),
    #[error("runtime clock failed: {0}")]
    Clock(&'static str),
    #[error("status encoding failed: {0}")]
    StatusEncoding(#[from] serde_json::Error),
    #[error("status timestamp failed: {0}")]
    StatusTimestamp(#[from] time::error::Format),
    #[error("status timestamp is outside the supported UTC range")]
    StatusTimestampRange,
}

pub type Result<T> = std::result::Result<T, ScanRuntimeError>;

pub(crate) fn io_error(
    operation: &'static str,
    path: &std::path::Path,
    source: std::io::Error,
) -> ScanRuntimeError {
    ScanRuntimeError::Io {
        operation,
        path: path.to_owned(),
        source,
    }
}
