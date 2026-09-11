//! Tauri IPC surface. Commands validate input, log technical detail, and reject with a
//! `CommandError` whose message the UI can show as-is.

use std::sync::{Arc, Mutex};

use chrono::{DateTime, Utc};
use tauri::State;
use tracing::{Instrument, error, info, info_span, warn};
use tracing_appender::non_blocking::WorkerGuard;
use uuid::Uuid;

use crate::config::Environment;
use crate::error::AppError;
use crate::protocol::{
    ComponentState, CoreStatus, DiagnosticsReport, DiagnosticsResult, UiErrorReport, WorkerStatus,
};
use crate::worker::{DIAGNOSTICS, DIAGNOSTICS_TIMEOUT, WorkerSupervisor};

pub struct AppState {
    pub supervisor: Arc<WorkerSupervisor>,
    pub core: CoreInfo,
    /// Dropped on exit to flush buffered log lines.
    pub log_guard: Mutex<Option<WorkerGuard>>,
}

pub struct CoreInfo {
    pub app_version: String,
    pub environment: Environment,
    pub data_dir: String,
    pub log_dir: String,
    pub config_sources: Vec<String>,
    pub config_warnings: Vec<String>,
    pub started_at: DateTime<Utc>,
}

impl CoreInfo {
    pub fn status(&self) -> CoreStatus {
        let (state, summary) = if self.config_warnings.is_empty() {
            (
                ComponentState::Ok,
                format!(
                    "Lead Intelligence {} ({})",
                    self.app_version,
                    self.environment.name()
                ),
            )
        } else {
            (
                ComponentState::Degraded,
                format!(
                    "Running on built-in defaults: {} configuration warning(s)",
                    self.config_warnings.len()
                ),
            )
        };
        CoreStatus {
            state,
            summary,
            app_version: self.app_version.clone(),
            environment: self.environment,
            os: std::env::consts::OS.to_owned(),
            arch: std::env::consts::ARCH.to_owned(),
            data_dir: self.data_dir.clone(),
            log_dir: self.log_dir.clone(),
            config_sources: self.config_sources.clone(),
            config_warnings: self.config_warnings.clone(),
            started_at: self.started_at,
            uptime_s: (Utc::now() - self.started_at).num_milliseconds() as f64 / 1000.0,
        }
    }
}

fn ipc_span(command: &'static str) -> tracing::Span {
    info_span!("ipc", command, ipc_id = %Uuid::now_v7())
}

/// Live status of every subsystem. Never fails as a whole: when the worker cannot answer, the
/// report says so in `components_error` instead of inventing component states.
#[tauri::command]
pub async fn get_diagnostics(state: State<'_, AppState>) -> Result<DiagnosticsReport, AppError> {
    async {
        let (components, components_error) = match state
            .supervisor
            .request_typed::<DiagnosticsResult>(DIAGNOSTICS, DIAGNOSTICS_TIMEOUT)
            .await
        {
            Ok(result) => (Some(result), None),
            Err(error) => {
                warn!(%error, "worker diagnostics unavailable");
                (None, Some(error.to_command_error()))
            }
        };
        Ok(DiagnosticsReport {
            generated_at: Utc::now(),
            core: state.core.status(),
            worker: state.supervisor.status(),
            components,
            components_error,
        })
    }
    .instrument(ipc_span("get_diagnostics"))
    .await
}

#[tauri::command]
pub async fn restart_worker(state: State<'_, AppState>) -> Result<WorkerStatus, AppError> {
    async {
        info!("worker restart requested from the UI");
        state
            .supervisor
            .restart()
            .await
            .inspect_err(|error| error!(%error, "worker restart failed"))
    }
    .instrument(ipc_span("restart_worker"))
    .await
}

/// Writes a UI error into the desktop log. Oversized fields are clipped, not trusted.
#[tauri::command]
pub fn report_ui_error(report: UiErrorReport) -> Result<(), AppError> {
    fn clip(text: &str, max_chars: usize) -> String {
        text.chars().take(max_chars).collect()
    }
    let source = clip(report.source.trim(), 64);
    if source.is_empty() {
        return Err(AppError::InvalidInput("source must not be empty".into()));
    }
    error!(
        target: "ui",
        source,
        stack = report.stack.as_deref().map(|stack| clip(stack, 8000)),
        "{}",
        clip(&report.message, 2000)
    );
    Ok(())
}
