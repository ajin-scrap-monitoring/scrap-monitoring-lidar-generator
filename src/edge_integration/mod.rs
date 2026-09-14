//! Synthetic-only `lidar-processing` configuration export.

use std::{collections::BTreeMap, fs, path::Path};

use serde_json::{Value, json};

use crate::{
    configuration::{GeneratorInputs, SensorConfig},
    geometry::{Polygon2, Vec2},
    scenario::MAX_GRID_NODES,
};

const BIN_WIDTH_MM: i64 = 50;
const DRIVER_IDENTITY_MAX_BYTES: usize = 64;
const ENDPOINT_MAX_BYTES: usize = 100;
const IDENTITY_MAX_BYTES: usize = 128;
const PLANE_TOLERANCE: f64 = 1e-6;
const SECTION_HEIGHT_SAMPLES: usize = 41;

type Matrix3 = [[f64; 3]; 3];
type Vector3 = [f64; 3];

#[derive(Debug, thiserror::Error)]
pub enum ProcessingConfigError {
    #[error("{0}")]
    Invalid(String),
    #[error(transparent)]
    Geometry(#[from] crate::geometry::GeometryError),
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
}

pub type Result<T> = std::result::Result<T, ProcessingConfigError>;

/// Build the complete demo configuration consumed by pinned `lidar-processing`.
pub fn build_synthetic_processing_config(
    inputs: &GeneratorInputs,
    socket_directory: &Path,
    site_id: &str,
    edge_id: &str,
    config_revision: &str,
) -> Result<Value> {
    let socket_directory = absolute_posix_directory(socket_directory)?;
    require_identity(site_id, "site_id", IDENTITY_MAX_BYTES)?;
    require_identity(edge_id, "edge_id", DRIVER_IDENTITY_MAX_BYTES)?;
    require_identity(
        config_revision,
        "config_revision",
        DRIVER_IDENTITY_MAX_BYTES,
    )?;

    let environment = &inputs.environment;
    if environment.sensors.len() != 2 {
        return invalid("processing configuration requires exactly two sensors");
    }
    let qualities: BTreeMap<_, _> = inputs
        .quality_profile
        .sensors
        .iter()
        .map(|quality| (quality.sensor_id.as_str(), quality))
        .collect();
    let boundary = Polygon2::new(
        environment
            .boundary_xy_m
            .iter()
            .copied()
            .map(Vec2::try_from)
            .collect::<std::result::Result<Vec<_>, _>>()?,
    )?;

    let mut sensors = Vec::new();
    sensors
        .try_reserve_exact(environment.sensors.len())
        .map_err(|_| ProcessingConfigError::Invalid("sensor output allocation failed".into()))?;
    for sensor in &environment.sensors {
        require_identity(&sensor.sensor_id, "sensor_id", DRIVER_IDENTITY_MAX_BYTES).map_err(
            |_| {
                ProcessingConfigError::Invalid(
                    "sensor identifiers must be driver-compatible".into(),
                )
            },
        )?;
        let quality = qualities.get(sensor.sensor_id.as_str()).ok_or_else(|| {
            ProcessingConfigError::Invalid(format!(
                "quality profile is missing sensor {}",
                sensor.sensor_id
            ))
        })?;
        sensors.push(sensor_processing_config(
            sensor,
            inputs,
            &boundary,
            &socket_directory,
            minimum_valid_quality(&quality.valid_distance_frequencies)?,
        )?);
    }

    Ok(json!({
        "schema_version": "1.0",
        "site_id": site_id,
        "edge_id": edge_id,
        "config_revision": config_revision,
        "allow_demo_calibration": true,
        "calibration": {
            "version": environment.environment_id,
            "demo": true,
            "fusion_map": [[0.0, 0.0], [1.0, 1.0]],
            "single_sensor_maps": {},
        },
        "processing": {
            "target_scan_hz": inputs.generator.measurement.rotation_rate_hz,
        },
        "sensors": sensors,
    }))
}

/// Write a deterministic, human-readable UTF-8 JSON configuration.
pub fn write_synthetic_processing_config(path: &Path, config: &Value) -> Result<()> {
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        fs::create_dir_all(parent)?;
    }
    let mut payload = serde_json::to_string_pretty(config)?;
    payload.push('\n');
    fs::write(path, payload)?;
    Ok(())
}

fn sensor_processing_config(
    sensor: &SensorConfig,
    inputs: &GeneratorInputs,
    boundary: &Polygon2,
    socket_directory: &str,
    minimum_valid_quality: u8,
) -> Result<Value> {
    let (rotation, translation_mm, axis_x) = processing_transform(sensor)?;
    let (roi_x_mm, angle_interval_mdeg) = select_section(
        sensor,
        axis_x,
        boundary,
        inputs.environment.floor_z_m,
        inputs.environment.top_z_m,
    )?;
    let floor_mm = millimetres(inputs.environment.floor_z_m)?;
    let top_mm = millimetres(inputs.environment.top_z_m)?;
    let bin_count = usize::try_from((roi_x_mm[1] - roi_x_mm[0]) / BIN_WIDTH_MM)
        .map_err(|_| ProcessingConfigError::Invalid("section bin count is too large".into()))?;
    let mut bottom_mm = Vec::new();
    let mut max_height_mm = Vec::new();
    bottom_mm
        .try_reserve_exact(bin_count)
        .map_err(|_| ProcessingConfigError::Invalid("section profile allocation failed".into()))?;
    max_height_mm
        .try_reserve_exact(bin_count)
        .map_err(|_| ProcessingConfigError::Invalid("section profile allocation failed".into()))?;
    bottom_mm.resize(bin_count, floor_mm);
    max_height_mm.resize(bin_count, top_mm);

    let endpoint = format!("unix:{socket_directory}/{}.sock", sensor.sensor_id);
    if endpoint.len() > ENDPOINT_MAX_BYTES {
        return invalid(format!(
            "{} processing endpoint exceeds {ENDPOINT_MAX_BYTES} bytes",
            sensor.sensor_id
        ));
    }
    let measurement = &inputs.generator.measurement;
    Ok(json!({
        "sensor_id": sensor.sensor_id,
        "endpoint": endpoint,
        "sample_filter": {
            "distance_min_mm": millimetres(measurement.min_distance_m)?,
            "distance_max_mm": millimetres(measurement.max_distance_m)?,
            "quality_min": minimum_valid_quality,
            "angle_interval_mdeg": angle_interval_mdeg,
        },
        "base_weight": 0.5,
        "calibration": {
            "rotation": rotation.map(|row| row.map(stable_float)),
            "translation_mm": translation_mm.map(stable_float),
            "roi_x_mm": roi_x_mm,
            "roi_z_mm": [floor_mm, top_mm],
            "roi_polygon_xz_mm": [
                [roi_x_mm[0], floor_mm],
                [roi_x_mm[1], floor_mm],
                [roi_x_mm[1], top_mm],
                [roi_x_mm[0], top_mm],
            ],
            "masks_x_mm": [],
            "mask_polygons_xz_mm": [],
            "bottom_mm": bottom_mm,
            "max_height_mm": max_height_mm,
        },
    }))
}

fn processing_transform(sensor: &SensorConfig) -> Result<(Matrix3, Vector3, Vector3)> {
    let u0 = sensor.u0;
    let u90 = sensor.u90;
    if u90[2].abs() > PLANE_TOLERANCE {
        return invalid(format!("{} u90 must be horizontal", sensor.sensor_id));
    }
    if u0[2] >= -PLANE_TOLERANCE {
        return invalid(format!("{} u0 must point downward", sensor.sensor_id));
    }

    let axis_x = negate(u90);
    let axis_z = [0.0, 0.0, 1.0];
    let axis_y = cross(axis_z, axis_x);
    let world_to_section = [axis_x, axis_y, axis_z];
    let sdk_y = negate(u90);
    let sdk_z = cross(u0, sdk_y);
    let sdk_to_world = columns(u0, sdk_y, sdk_z);
    let rotation = matrix_multiply(world_to_section, sdk_to_world);
    let translation_mm = matrix_vector(world_to_section, sensor.p0_m).map(|value| value * 1_000.0);
    if translation_mm.iter().any(|value| !value.is_finite()) {
        return invalid(format!(
            "{} cannot produce a finite translation",
            sensor.sensor_id
        ));
    }
    let product = matrix_multiply(rotation, transpose(rotation));
    for (row_index, row) in product.iter().enumerate() {
        for (column_index, value) in row.iter().enumerate() {
            let expected = if row_index == column_index { 1.0 } else { 0.0 };
            if !numpy_close(*value, expected, 1e-6, 1e-5) {
                return invalid(format!(
                    "{} cannot produce a rigid transform",
                    sensor.sensor_id
                ));
            }
        }
    }
    if !numpy_close(determinant(rotation), 1.0, 1e-6, 1e-5) {
        return invalid(format!(
            "{} cannot produce a rigid transform",
            sensor.sensor_id
        ));
    }
    Ok((rotation, translation_mm, axis_x))
}

fn select_section(
    sensor: &SensorConfig,
    axis_x: Vector3,
    boundary: &Polygon2,
    floor_z_m: f64,
    top_z_m: f64,
) -> Result<([i64; 2], [i64; 2])> {
    let projections_mm = boundary
        .vertices()
        .iter()
        .map(|vertex| millimetres(vertex.x() * axis_x[0] + vertex.y() * axis_x[1]))
        .collect::<Result<Vec<_>>>()?;
    let minimum = *projections_mm
        .iter()
        .min()
        .ok_or_else(|| ProcessingConfigError::Invalid("environment boundary is empty".into()))?;
    let maximum = *projections_mm
        .iter()
        .max()
        .ok_or_else(|| ProcessingConfigError::Invalid("environment boundary is empty".into()))?;
    let first_edge = minimum.div_euclid(BIN_WIDTH_MM) * BIN_WIDTH_MM;
    let last_edge = ceil_div(maximum, BIN_WIDTH_MM)
        .checked_mul(BIN_WIDTH_MM)
        .ok_or_else(|| ProcessingConfigError::Invalid("section edge exceeds i64".into()))?;
    let origin_mm = millimetres(dot(sensor.p0_m, axis_x))?;
    let span = last_edge
        .checked_sub(first_edge)
        .ok_or_else(|| ProcessingConfigError::Invalid("section span exceeds i64".into()))?;
    let edge_count = usize::try_from(span / BIN_WIDTH_MM)
        .map_err(|_| ProcessingConfigError::Invalid("section edge count is too large".into()))?;
    if edge_count > MAX_GRID_NODES {
        return invalid(format!("section profile exceeds {MAX_GRID_NODES} bins"));
    }
    let mut valid = BTreeMap::new();
    for index in 0..edge_count {
        let edge = first_edge + i64::try_from(index).unwrap() * BIN_WIDTH_MM;
        let center_m = (edge as f64 + BIN_WIDTH_MM as f64 / 2.0) / 1_000.0;
        valid.insert(
            edge,
            section_column_is_inside(sensor, center_m, axis_x, boundary, floor_z_m, top_z_m)?,
        );
    }

    let mut left_candidates: Vec<_> = valid
        .keys()
        .copied()
        .filter(|edge| *edge as f64 + BIN_WIDTH_MM as f64 / 2.0 < origin_mm as f64)
        .collect();
    left_candidates.reverse();
    let right_candidates: Vec<_> = valid
        .keys()
        .copied()
        .filter(|edge| *edge as f64 + BIN_WIDTH_MM as f64 / 2.0 >= origin_mm as f64)
        .collect();
    let left = contiguous_run(&valid, left_candidates);
    let right = contiguous_run(&valid, right_candidates);
    let (selected, angles) = if left.len() >= right.len() && !left.is_empty() {
        (left, [0, 90_000])
    } else if !right.is_empty() {
        (right, [270_000, 360_000])
    } else {
        return invalid(format!(
            "{} has no valid 50 mm section bins",
            sensor.sensor_id
        ));
    };
    let lower = *selected
        .iter()
        .min()
        .expect("non-empty section selection checked above");
    let upper = selected
        .iter()
        .max()
        .expect("non-empty section selection checked above")
        .checked_add(BIN_WIDTH_MM)
        .ok_or_else(|| ProcessingConfigError::Invalid("section edge exceeds i64".into()))?;
    Ok(([lower, upper], angles))
}

fn contiguous_run(valid: &BTreeMap<i64, bool>, candidates: Vec<i64>) -> Vec<i64> {
    let mut selected = Vec::new();
    for edge in candidates {
        if valid.get(&edge).copied().unwrap_or(false) {
            selected.push(edge);
        } else if !selected.is_empty() {
            break;
        }
    }
    selected
}

fn section_column_is_inside(
    sensor: &SensorConfig,
    section_x_m: f64,
    axis_x: Vector3,
    boundary: &Polygon2,
    floor_z_m: f64,
    top_z_m: f64,
) -> Result<bool> {
    let origin_section_x_m = dot(sensor.p0_m, axis_x);
    let b = origin_section_x_m - section_x_m;
    for index in 0..SECTION_HEIGHT_SAMPLES {
        let fraction = index as f64 / (SECTION_HEIGHT_SAMPLES - 1) as f64;
        let height_m = floor_z_m + (top_z_m - floor_z_m) * fraction;
        let a = (height_m - sensor.p0_m[2]) / sensor.u0[2];
        let point = [
            sensor.p0_m[0] + a * sensor.u0[0] + b * sensor.u90[0],
            sensor.p0_m[1] + a * sensor.u0[1] + b * sensor.u90[1],
        ];
        if !boundary.contains(Vec2::new(point[0], point[1])?)? {
            return Ok(false);
        }
    }
    Ok(true)
}

fn minimum_valid_quality(frequencies: &[u64; 256]) -> Result<u8> {
    frequencies
        .iter()
        .position(|frequency| *frequency > 0)
        .map(|quality| (quality as u8) >> 2)
        .ok_or_else(|| {
            ProcessingConfigError::Invalid(
                "quality profile has no valid measurement quality".into(),
            )
        })
}

fn millimetres(value_m: f64) -> Result<i64> {
    let value_mm = value_m * 1_000.0;
    let rounded = value_mm.round_ties_even();
    if !value_mm.is_finite()
        || (value_mm - rounded).abs() > 1e-6
        || rounded < i64::MIN as f64
        || rounded > i64::MAX as f64
    {
        return invalid(format!(
            "processing geometry requires integer millimetres: {value_m}"
        ));
    }
    Ok(rounded as i64)
}

fn absolute_posix_directory(path: &Path) -> Result<String> {
    if !path.is_absolute() || path == Path::new("/") {
        return invalid("processing socket directory must be an absolute non-root path");
    }
    let value = path.to_str().ok_or_else(|| {
        ProcessingConfigError::Invalid("processing socket directory must be UTF-8".into())
    })?;
    Ok(value.trim_end_matches('/').to_owned())
}

fn require_identity(value: &str, name: &str, maximum_bytes: usize) -> Result<()> {
    let bytes = value.as_bytes();
    if bytes.is_empty()
        || bytes.len() > maximum_bytes
        || !bytes[0].is_ascii_alphanumeric()
        || !bytes
            .iter()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'.' | b'-'))
    {
        let qualifier = if maximum_bytes == DRIVER_IDENTITY_MAX_BYTES {
            "driver-compatible"
        } else {
            "safe deployment"
        };
        return invalid(format!("{name} must be a {qualifier} identifier"));
    }
    Ok(())
}

fn invalid<T>(message: impl Into<String>) -> Result<T> {
    Err(ProcessingConfigError::Invalid(message.into()))
}

fn negate(value: Vector3) -> Vector3 {
    [-value[0], -value[1], -value[2]]
}

fn dot(left: Vector3, right: Vector3) -> f64 {
    left[0] * right[0] + left[1] * right[1] + left[2] * right[2]
}

fn cross(left: Vector3, right: Vector3) -> Vector3 {
    [
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    ]
}

fn columns(first: Vector3, second: Vector3, third: Vector3) -> Matrix3 {
    [
        [first[0], second[0], third[0]],
        [first[1], second[1], third[1]],
        [first[2], second[2], third[2]],
    ]
}

fn transpose(value: Matrix3) -> Matrix3 {
    [
        [value[0][0], value[1][0], value[2][0]],
        [value[0][1], value[1][1], value[2][1]],
        [value[0][2], value[1][2], value[2][2]],
    ]
}

fn matrix_multiply(left: Matrix3, right: Matrix3) -> Matrix3 {
    std::array::from_fn(|row| {
        std::array::from_fn(|column| {
            left[row][0] * right[0][column]
                + left[row][1] * right[1][column]
                + left[row][2] * right[2][column]
        })
    })
}

fn matrix_vector(matrix: Matrix3, vector: Vector3) -> Vector3 {
    matrix.map(|row| dot(row, vector))
}

fn determinant(matrix: Matrix3) -> f64 {
    matrix[0][0] * (matrix[1][1] * matrix[2][2] - matrix[1][2] * matrix[2][1])
        - matrix[0][1] * (matrix[1][0] * matrix[2][2] - matrix[1][2] * matrix[2][0])
        + matrix[0][2] * (matrix[1][0] * matrix[2][1] - matrix[1][1] * matrix[2][0])
}

fn numpy_close(actual: f64, expected: f64, absolute: f64, relative: f64) -> bool {
    actual.is_finite()
        && expected.is_finite()
        && (actual - expected).abs() <= absolute + relative * expected.abs()
}

fn stable_float(value: f64) -> f64 {
    if value.abs() < 1e-15 {
        return 0.0;
    }
    if value.abs() < 1e16 {
        format!("{value:.15}").parse().unwrap_or(value)
    } else {
        value
    }
}

fn ceil_div(value: i64, divisor: i64) -> i64 {
    value.div_euclid(divisor) + i64::from(value.rem_euclid(divisor) != 0)
}

#[cfg(test)]
mod tests {
    use super::{ceil_div, stable_float};

    #[test]
    fn decimal_grid_ceiling_handles_both_signs() {
        assert_eq!(ceil_div(1, 50), 1);
        assert_eq!(ceil_div(50, 50), 1);
        assert_eq!(ceil_div(51, 50), 2);
        assert_eq!(ceil_div(-1, 50), 0);
        assert_eq!(ceil_div(-50, 50), -1);
        assert_eq!(ceil_div(-51, 50), -1);
    }

    #[test]
    fn stable_float_matches_python_decimal_place_rounding() {
        assert_eq!(stable_float(4.430800646815651), 4.430800646815651);
        assert_eq!(stable_float(2.8458872586489115), 2.845887258648911);
        assert_eq!(stable_float(-1.5578600007716954), -1.557860000771695);
        assert_eq!(stable_float(1e-16), 0.0);
    }
}
