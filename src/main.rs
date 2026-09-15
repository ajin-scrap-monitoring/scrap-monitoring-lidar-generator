use clap::Parser;
use scrap_monitoring_lidar_generator::{
    cli::{self, Cli, Command},
    runtime::run_generator_application,
};

#[tokio::main(flavor = "multi_thread")]
async fn main() -> std::process::ExitCode {
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
        Command::Run { overrides } => {
            let settings = match cli::resolve_runtime_settings(&environment, overrides) {
                Ok(settings) => settings,
                Err(error) => {
                    eprintln!("configuration error: {error}");
                    return std::process::ExitCode::from(2);
                }
            };
            let inputs = match cli::load_overridden_inputs(&settings.model) {
                Ok(inputs) => inputs,
                Err(error) => {
                    eprintln!("configuration error: {error}");
                    return std::process::ExitCode::from(2);
                }
            };
            match run_generator_application(inputs, settings).await {
                Ok(summary) => {
                    let published = summary
                        .scan_stats
                        .sensors
                        .iter()
                        .map(|sensor| sensor.published_frames)
                        .sum::<u64>();
                    let frame_loss = summary
                        .scan_stats
                        .sensors
                        .iter()
                        .map(|sensor| sensor.frame_loss)
                        .sum::<u64>();
                    let subscribers = summary
                        .scan_stats
                        .sensors
                        .iter()
                        .map(|sensor| sensor.subscribers)
                        .sum::<usize>();
                    println!(
                        "run_id={} generated={} published={}",
                        summary.run_id, summary.generated_scans, published
                    );
                    println!(
                        "scan_stream published={published} frame_loss={frame_loss} subscribers={subscribers}"
                    );
                    println!(
                        "observation=active sent={} dropped={} connection_failures={}",
                        summary.observation_stats.sent_records,
                        summary.observation_stats.dropped_records,
                        summary.observation_stats.connection_failures
                    );
                    std::process::ExitCode::SUCCESS
                }
                Err(error) => {
                    eprintln!("runtime error: {error}");
                    std::process::ExitCode::from(1)
                }
            }
        }
    }
}
