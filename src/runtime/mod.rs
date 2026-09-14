//! Long-running simulation composition independent of external delivery.

mod generation;

pub use generation::{
    GenerationBatch, GenerationRuntime, GenerationRuntimeError, GenerationRuntimeStats,
};
