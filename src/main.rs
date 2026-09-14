use clap::Parser;
use scrap_monitoring_lidar_generator::cli::{self, Cli};

fn main() -> std::process::ExitCode {
    let cli = Cli::parse();
    let environment = std::env::vars_os()
        .filter_map(|(key, value)| Some((key.into_string().ok()?, value.into_string().ok()?)))
        .collect();
    match cli::check(&cli.command, &environment) {
        Ok(inputs) => {
            println!(
                "validation=passed sensors={} seed={}",
                inputs.environment.sensors.len(),
                inputs.generator.seed
            );
            std::process::ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("configuration error: {error}");
            std::process::ExitCode::from(2)
        }
    }
}
