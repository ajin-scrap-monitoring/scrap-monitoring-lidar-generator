//! Long-running simulation composition independent of external delivery.

mod application;
mod generation;

pub use application::{ApplicationError, ApplicationSummary, run_generator_application};
pub use generation::{
    GenerationBatch, GenerationRuntime, GenerationRuntimeError, GenerationRuntimeStats,
};
