//! Lead Intelligence desktop core: window shell, typed IPC and the Python worker supervisor.

pub mod commands;
pub mod config;
pub mod data;
pub mod error;
pub mod logging;
pub mod protocol;
pub mod worker;

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use chrono::{DateTime, Utc};
use tauri::{Emitter, Manager, RunEvent, Runtime};
use tracing::{error, info, warn};

use crate::commands::{AppState, CoreInfo};
use crate::config::{AppConfig, Environment};
use crate::protocol::WorkerStatus;
use crate::worker::{StatusListener, SupervisorSettings, WorkerLaunch, WorkerSupervisor};

/// Tauri event carrying a `WorkerStatus` whenever the worker lifecycle changes.
pub const WORKER_STATUS_EVENT: &str = "worker://status";

/// Registers every IPC command. Shared by the real app and the IPC tests.
pub fn register_commands<R: Runtime>(builder: tauri::Builder<R>) -> tauri::Builder<R> {
    builder.invoke_handler(tauri::generate_handler![
        commands::get_diagnostics,
        commands::restart_worker,
        commands::report_ui_error,
        commands::list_businesses,
        commands::get_business,
        commands::list_datasets,
        commands::list_sources,
        commands::preview_import,
        commands::list_campaigns,
        commands::save_campaign,
        commands::validate_criteria,
        commands::start_discovery,
        commands::control_discovery,
        commands::list_discovery_runs,
        commands::list_candidates,
        commands::get_candidate,
        commands::start_crawl,
        commands::control_crawl,
        commands::list_crawl_runs,
        commands::list_crawl_urls,
        commands::get_crawl_url,
        commands::start_resolution,
        commands::control_resolution,
        commands::list_resolution_runs,
        commands::list_matches,
        commands::get_match,
        commands::act_on_match,
        commands::choose_canonical_value,
        commands::start_latino,
        commands::control_latino,
        commands::list_latino_runs,
        commands::get_latino,
        commands::review_latino,
        commands::start_website_audit,
        commands::control_website_audit,
        commands::list_website_runs,
        commands::list_website_audits,
        commands::get_website_audit,
        commands::start_contacts,
        commands::control_contacts,
        commands::list_contact_runs,
        commands::list_contacts,
        commands::get_contacts,
        commands::act_on_contact,
        commands::list_decision_makers,
        commands::review_decision_maker,
        commands::list_suppressions,
        commands::change_suppression,
        commands::import_suppressions,
        commands::export_suppressions,
        commands::seed_synthetic_data
    ])
}

pub fn run() {
    let started_at = Utc::now();
    let app = register_commands(tauri::Builder::default())
        .setup(move |app| {
            let state = bootstrap(app.handle(), started_at)?;
            let supervisor = state.supervisor.clone();
            app.manage(state);
            tauri::async_runtime::spawn(async move {
                if let Err(error) = supervisor.start().await {
                    error!(%error, "python worker failed to start");
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build the Lead Intelligence application");

    app.run(|app, event| {
        if let RunEvent::Exit = event
            && let Some(state) = app.try_state::<AppState>()
        {
            tauri::async_runtime::block_on(state.supervisor.stop());
            info!("desktop core exited");
            // flush buffered log lines before the process ends
            drop(
                state
                    .log_guard
                    .lock()
                    .ok()
                    .and_then(|mut guard| guard.take()),
            );
        }
    });
}

struct DefaultDirs {
    data: PathBuf,
    logs: PathBuf,
    user_config: PathBuf,
    repo_root: Option<PathBuf>,
}

/// Development builds keep everything inside the repository; release builds use the
/// per-user application directories (%LOCALAPPDATA% / %APPDATA% on Windows).
fn default_dirs<R: Runtime>(app: &tauri::AppHandle<R>) -> tauri::Result<DefaultDirs> {
    if cfg!(debug_assertions) {
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("src-tauri lives inside the repository")
            .to_path_buf();
        return Ok(DefaultDirs {
            data: root.join("data"),
            logs: root.join("logs"),
            user_config: root.join("config").join("local.toml"),
            repo_root: Some(root),
        });
    }
    let paths = app.path();
    Ok(DefaultDirs {
        data: paths.app_local_data_dir()?.join("data"),
        logs: paths.app_log_dir()?,
        user_config: paths.app_config_dir()?.join("config.toml"),
        repo_root: None,
    })
}

/// Empty = default; relative paths resolve against the default's parent (the app base dir).
fn configured_dir(configured: &str, default: &Path) -> PathBuf {
    if configured.trim().is_empty() {
        return default.to_path_buf();
    }
    let path = PathBuf::from(configured);
    if path.is_absolute() {
        path
    } else {
        default.parent().unwrap_or(default).join(path)
    }
}

fn bootstrap<R: Runtime>(
    app: &tauri::AppHandle<R>,
    started_at: DateTime<Utc>,
) -> Result<AppState, Box<dyn std::error::Error>> {
    let dirs = default_dirs(app)?;
    let loaded = config::load(&dirs.user_config, &|key| std::env::var(key).ok());
    let data_dir = configured_dir(&loaded.config.paths.data_dir, &dirs.data);
    let log_dir = configured_dir(&loaded.config.paths.log_dir, &dirs.logs);
    let log_guard = logging::init(
        &log_dir,
        &loaded.config.logging,
        loaded.environment == Environment::Development,
    )?;
    info!(
        version = env!("CARGO_PKG_VERSION"),
        environment = loaded.environment.name(),
        data_dir = %data_dir.display(),
        log_dir = %log_dir.display(),
        sources = ?loaded.sources,
        "desktop core starting"
    );
    for warning in &loaded.warnings {
        warn!(%warning, "configuration warning");
    }

    let launch = resolve_worker_launch(
        &loaded.config,
        loaded.environment,
        dirs.repo_root.as_deref(),
        &data_dir,
    );
    if let Err(reason) = &launch {
        error!(%reason, "python worker cannot be launched");
    }
    let emitter = app.clone();
    let listener: StatusListener = Arc::new(move |status: WorkerStatus| {
        if let Err(error) = emitter.emit(WORKER_STATUS_EVENT, status) {
            warn!(%error, "could not emit the worker status event");
        }
    });
    let settings = SupervisorSettings::new(
        launch,
        Duration::from_millis(loaded.config.worker.startup_timeout_ms),
    );

    Ok(AppState {
        supervisor: WorkerSupervisor::new(settings, listener),
        core: CoreInfo {
            app_version: env!("CARGO_PKG_VERSION").to_owned(),
            environment: loaded.environment,
            data_dir: data_dir.display().to_string(),
            log_dir: log_dir.display().to_string(),
            config_sources: loaded.sources,
            config_warnings: loaded.warnings,
            started_at,
        },
        log_guard: Mutex::new(Some(log_guard)),
    })
}

/// Python inside the uv-managed virtualenv of the research engine (development only).
pub fn dev_python(repo_root: &Path) -> PathBuf {
    let venv = repo_root
        .join("services")
        .join("research-engine")
        .join(".venv");
    if cfg!(windows) {
        venv.join("Scripts").join("python.exe")
    } else {
        venv.join("bin").join("python")
    }
}

/// Chooses how to launch the worker: explicit configuration, the development virtualenv, or the
/// sidecar executable placed next to the application binary by the installer.
pub fn resolve_worker_launch(
    config: &AppConfig,
    environment: Environment,
    repo_root: Option<&Path>,
    data_dir: &Path,
) -> Result<WorkerLaunch, String> {
    let (program, args) = if !config.worker.program.is_empty() {
        (
            PathBuf::from(&config.worker.program),
            config.worker.args.clone(),
        )
    } else if let Some(root) = repo_root {
        (
            dev_python(root),
            vec!["-m".to_owned(), "research_engine".to_owned()],
        )
    } else {
        let exe = std::env::current_exe()
            .map_err(|e| format!("cannot locate the application executable: {e}"))?;
        let sidecar =
            exe.with_file_name(format!("research-engine{}", std::env::consts::EXE_SUFFIX));
        (sidecar, config.worker.args.clone())
    };
    if !program.is_file() {
        return Err(format!(
            "worker executable not found at {} (development: run `pnpm run setup`)",
            program.display()
        ));
    }
    Ok(WorkerLaunch {
        program,
        args,
        env: worker_env(config, environment, data_dir),
    })
}

fn worker_env(
    config: &AppConfig,
    environment: Environment,
    data_dir: &Path,
) -> Vec<(String, String)> {
    let python_level = match config.logging.level.as_str() {
        "trace" | "debug" => "debug",
        "warn" => "warning",
        "error" => "error",
        _ => "info",
    };
    [
        ("LEAD_DATA_DIR", data_dir.display().to_string()),
        ("LEAD_LOG_LEVEL", python_level.to_owned()),
        ("LEAD_OLLAMA_URL", config.ollama.base_url.clone()),
        (
            "LEAD_OLLAMA_TIMEOUT_S",
            (config.ollama.timeout_ms as f64 / 1000.0).to_string(),
        ),
        // the synthetic test-data seed exists only in development builds
        (
            "LEAD_DEV_TOOLS",
            u8::from(environment == Environment::Development).to_string(),
        ),
        ("PYTHONUTF8", "1".to_owned()),
        ("PYTHONUNBUFFERED", "1".to_owned()),
    ]
    .into_iter()
    .map(|(key, value)| (key.to_owned(), value))
    .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn configured_dir_resolves_empty_relative_and_absolute() {
        let default = Path::new("/base/data");
        assert_eq!(configured_dir("", default), PathBuf::from("/base/data"));
        assert_eq!(
            configured_dir("other", default),
            PathBuf::from("/base/other")
        );
        let absolute = std::env::temp_dir().join("lead-absolute");
        assert_eq!(
            configured_dir(&absolute.display().to_string(), default),
            absolute
        );
    }

    #[test]
    fn worker_env_maps_levels_to_python_names() {
        let mut config = config::load(Path::new("missing.toml"), &|_| None).config;
        config.logging.level = "warn".into();
        let env = worker_env(&config, Environment::Production, Path::new("data"));
        assert!(env.contains(&("LEAD_LOG_LEVEL".into(), "warning".into())));
        assert!(env.contains(&("LEAD_OLLAMA_TIMEOUT_S".into(), "2".into())));
        assert!(env.contains(&("LEAD_DEV_TOOLS".into(), "0".into())));
        let dev = worker_env(&config, Environment::Development, Path::new("data"));
        assert!(dev.contains(&("LEAD_DEV_TOOLS".into(), "1".into())));
    }

    #[test]
    fn missing_worker_is_an_explanatory_error() {
        let mut config = config::load(Path::new("missing.toml"), &|_| None).config;
        config.worker.program = "Z:/definitely/missing/worker.exe".into();
        let error =
            resolve_worker_launch(&config, Environment::Production, None, Path::new("data"))
                .unwrap_err();
        assert!(error.contains("not found"), "{error}");
    }
}
