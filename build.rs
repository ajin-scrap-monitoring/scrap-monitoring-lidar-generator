fn main() -> Result<(), Box<dyn std::error::Error>> {
    let proto = "contracts/lidar/v1/lidar.proto";
    println!("cargo:rerun-if-changed={proto}");
    let mut config = prost_build::Config::new();
    config.protoc_executable(protoc_bin_vendored::protoc_bin_path()?);
    tonic_prost_build::configure().compile_with_config(
        config,
        &[proto],
        &["contracts/lidar/v1"],
    )?;
    Ok(())
}
