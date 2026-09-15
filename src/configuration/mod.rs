//! Versioned public input loading and cross-input validation.

mod environment;
mod generator;
mod models;
mod polygon;
mod quality;
pub mod strict_json;

pub use environment::{load_environment, parse_environment};
pub use generator::{
    load_generator_config, load_generator_inputs, parse_generator_config, validate_inputs,
};
pub use models::*;
pub use quality::{load_quality_profile, parse_quality_profile};

pub(crate) fn compensated_sum(values: impl IntoIterator<Item = f64>) -> f64 {
    let mut values = values.into_iter();
    let mut high = values.next().unwrap_or(0.0);
    let mut low = 0.0;
    for value in values {
        let combined = high + value;
        if high.abs() >= value.abs() {
            low += (high - combined) + value;
        } else {
            low += (value - combined) + high;
        }
        high = combined;
    }
    if low != 0.0 && low.is_finite() {
        high + low
    } else {
        high
    }
}
