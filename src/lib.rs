//! Rust configuration and protocol boundaries alongside the Python runtime.

pub mod cli;
pub mod configuration;
pub mod error;

pub mod wire {
    tonic::include_proto!("ajin.edge.lidar.v1");
}
