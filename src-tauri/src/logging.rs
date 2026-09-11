//! Structured JSON logs in daily-rotated files, plus a compact console in development.
//!
//! Correlation: IPC calls run inside an `ipc` span and worker calls inside a `worker_request`
//! span whose `request_id` is the envelope id the Python worker also logs. Payloads are never
//! logged, so business data and secrets stay out of the log files.

use std::path::Path;

use tracing_appender::non_blocking::WorkerGuard;
use tracing_appender::rolling::{RollingFileAppender, Rotation};
use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::util::SubscriberInitExt;
use tracing_subscriber::{EnvFilter, fmt};

use crate::config::LoggingConfig;

/// Installs the global subscriber. Keep the returned guard alive; dropping it flushes the file.
pub fn init(
    log_dir: &Path,
    config: &LoggingConfig,
    console: bool,
) -> Result<WorkerGuard, Box<dyn std::error::Error>> {
    std::fs::create_dir_all(log_dir)?;
    let appender = RollingFileAppender::builder()
        .rotation(Rotation::DAILY)
        .filename_prefix("desktop")
        .filename_suffix("log")
        .max_log_files(config.max_files)
        .build(log_dir)?;
    let (writer, guard) = tracing_appender::non_blocking(appender);
    // windowing/webview internals are noisy below warn
    let filter = EnvFilter::try_new(format!("{},tao=warn,wry=warn", config.level))?;
    let file = fmt::layer()
        .json()
        .flatten_event(true)
        .with_current_span(false)
        .with_span_list(true)
        .with_writer(writer);
    let console = console.then(|| fmt::layer().compact().with_writer(std::io::stderr));
    tracing_subscriber::registry()
        .with(filter)
        .with(file)
        .with(console)
        .try_init()?;
    Ok(guard)
}
