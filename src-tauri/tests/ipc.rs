//! Exercises the IPC layer the React UI uses (command dispatch + JSON serialization) through
//! Tauri's mock runtime, backed by the real Python worker.

mod common;

use std::sync::Mutex;

use chrono::Utc;
use lead_intelligence_lib::commands::{AppState, CoreInfo};
use lead_intelligence_lib::config::Environment;
use lead_intelligence_lib::protocol::{
    CommandError, CommandErrorCode, ComponentState, DiagnosticsReport, WorkerState,
};
use lead_intelligence_lib::register_commands;
use lead_intelligence_lib::worker::WorkerSupervisor;
use serde_json::json;
use tauri::ipc::{CallbackFn, InvokeBody};
use tauri::test::{INVOKE_KEY, get_ipc_response, mock_builder, mock_context, noop_assets};
use tauri::webview::InvokeRequest;

fn invoke(
    webview: &tauri::WebviewWindow<tauri::test::MockRuntime>,
    cmd: &str,
    body: serde_json::Value,
) -> Result<tauri::ipc::InvokeResponseBody, serde_json::Value> {
    get_ipc_response(
        webview,
        InvokeRequest {
            cmd: cmd.into(),
            callback: CallbackFn(0),
            error: CallbackFn(1),
            url: "http://tauri.localhost".parse().expect("url"),
            body: InvokeBody::Json(body),
            headers: Default::default(),
            invoke_key: INVOKE_KEY.to_string(),
        },
    )
}

#[test]
fn ui_commands_reach_the_real_worker_through_ipc() {
    let data = tempfile::tempdir().expect("temp dir");
    let supervisor = WorkerSupervisor::new(common::real_worker(data.path()), common::quiet());
    tauri::async_runtime::block_on(supervisor.start()).expect("worker starts");

    let state = AppState {
        supervisor: supervisor.clone(),
        core: CoreInfo {
            app_version: "0.0.0-test".into(),
            environment: Environment::Development,
            data_dir: data.path().display().to_string(),
            log_dir: data.path().display().to_string(),
            config_sources: vec!["embedded:default.toml".into()],
            config_warnings: Vec::new(),
            started_at: Utc::now(),
        },
        log_guard: Mutex::new(None),
    };
    let app = register_commands(mock_builder())
        .manage(state)
        .build(mock_context(noop_assets()))
        .expect("mock app");
    let webview = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .expect("mock webview");

    let report: DiagnosticsReport = invoke(&webview, "get_diagnostics", json!({}))
        .expect("get_diagnostics succeeds")
        .deserialize()
        .expect("typed report");
    assert_eq!(report.worker.state, WorkerState::Running);
    let components = report.components.expect("worker answered");
    assert_eq!(components.sqlite.state, ComponentState::Ok);
    assert_eq!(components.duckdb.state, ComponentState::Ok);
    assert_eq!(
        components.health.pid,
        report.worker.health.expect("health").pid
    );

    invoke(
        &webview,
        "report_ui_error",
        json!({ "report": { "source": "ipc-test", "message": "synthetic", "stack": null } }),
    )
    .expect("ui errors are accepted");
    let rejected = invoke(
        &webview,
        "report_ui_error",
        json!({ "report": { "source": " ", "message": "x", "stack": null } }),
    )
    .expect_err("blank source rejected");
    let error: CommandError = serde_json::from_value(rejected).expect("CommandError shape");
    assert_eq!(error.code, CommandErrorCode::InvalidInput);

    tauri::async_runtime::block_on(supervisor.stop());
    let down: DiagnosticsReport = invoke(&webview, "get_diagnostics", json!({}))
        .expect("still answers without a worker")
        .deserialize()
        .expect("typed report");
    assert!(down.components.is_none());
    assert_eq!(
        down.components_error.expect("reason").code,
        CommandErrorCode::WorkerUnavailable
    );
}
