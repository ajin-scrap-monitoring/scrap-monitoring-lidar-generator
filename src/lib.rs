//! Rust configuration and protocol boundaries alongside the Python runtime.

pub mod cli;
pub mod configuration;
pub mod diagnostics;
pub mod edge_integration;
pub mod error;
pub mod geometry;
pub mod measurement;
pub mod observation;
pub mod randomness;
pub mod rate_profile;
pub mod runtime;
pub mod scenario;

mod output_format;

/// Engine resource limit for scenario inlet positions.
pub const MAX_INLET_POSITIONS: usize = 64;

/// Maximum number of development diagnostic records selected for each sensor.
pub const MAX_DIAGNOSTIC_SCANS_PER_SENSOR: u64 = 16;

pub mod wire {
    tonic::include_proto!("ajin.edge.lidar.v1");
}
