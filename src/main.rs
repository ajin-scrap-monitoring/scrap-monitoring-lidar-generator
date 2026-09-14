use clap::Parser;
use scrap_monitoring_lidar_generator::cli::{self, Cli, Command};

fn main() -> std::process::ExitCode {
    let cli = Cli::parse();
    let environment = std::env::vars_os()
        .filter_map(|(key, value)| Some((key.into_string().ok()?, value.into_string().ok()?)))
        .collect();
    match &cli.command {
        command @ Command::Check { .. } => match cli::check(command, &environment) {
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
        },
        Command::ExportSyntheticProcessingConfig(arguments) => {
            match cli::export_synthetic_processing_config(arguments, &environment) {
                Ok(()) => std::process::ExitCode::SUCCESS,
                Err(error) => {
                    eprintln!("configuration error: {error}");
                    std::process::ExitCode::from(2)
                }
            }
        }
    }
}
