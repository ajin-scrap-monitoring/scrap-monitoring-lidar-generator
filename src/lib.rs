//! Rust configuration and protocol boundaries alongside the Python runtime.

pub mod cli;
pub mod configuration;
pub mod edge_integration;
pub mod error;
pub mod geometry;
pub mod measurement;
pub mod randomness;
pub mod rate_profile;
pub mod runtime;
pub mod scenario;

/// Engine resource limit for scenario inlet positions.
pub const MAX_INLET_POSITIONS: usize = 64;

pub mod wire {
    tonic::include_proto!("ajin.edge.lidar.v1");
}
