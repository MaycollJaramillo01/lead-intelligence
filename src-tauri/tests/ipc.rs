//! Exercises the IPC layer the React UI uses (command dispatch + JSON serialization) through
//! Tauri's mock runtime, backed by the real Python worker.

mod common;

use std::path::Path;
use std::sync::{Arc, Mutex};

use chrono::Utc;
use lead_intelligence_lib::commands::{AppState, CoreInfo};
use lead_intelligence_lib::config::Environment;
use lead_intelligence_lib::data::{BusinessDetail, BusinessPage, DatasetPage, SyntheticSeedResult};
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

type MockApp = (
    tauri::App<tauri::test::MockRuntime>,
    tauri::WebviewWindow<tauri::test::MockRuntime>,
);

fn mock_app(supervisor: Arc<WorkerSupervisor>, dir: &Path, environment: Environment) -> MockApp {
    let state = AppState {
        supervisor,
        core: CoreInfo {
            app_version: "0.0.0-test".into(),
            environment,
            data_dir: dir.display().to_string(),
            log_dir: dir.display().to_string(),
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
    (app, webview)
}

fn command_error(rejection: serde_json::Value) -> CommandError {
    serde_json::from_value(rejection).expect("CommandError shape")
}

#[test]
fn ui_commands_reach_the_real_worker_through_ipc() {
    let data = tempfile::tempdir().expect("temp dir");
    let supervisor = WorkerSupervisor::new(common::real_worker(data.path()), common::quiet());
    tauri::async_runtime::block_on(supervisor.start()).expect("worker starts");
    let (_app, webview) = mock_app(supervisor.clone(), data.path(), Environment::Development);

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
    assert_eq!(command_error(rejected).code, CommandErrorCode::InvalidInput);

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

fn business_query(limit: u32) -> serde_json::Value {
    json!({ "query": {
        "project_id": null, "search": null, "status": null, "sort": "name",
        "limit": limit, "cursor": null
    }})
}

#[test]
fn data_commands_serve_real_store_queries_through_ipc() {
    let data = tempfile::tempdir().expect("temp dir");
    let supervisor = WorkerSupervisor::new(common::real_worker(data.path()), common::quiet());
    tauri::async_runtime::block_on(supervisor.start()).expect("worker starts");
    let (_dev_app, dev) = mock_app(supervisor.clone(), data.path(), Environment::Development);

    let empty: BusinessPage = invoke(&dev, "list_businesses", business_query(50))
        .expect("empty store answers")
        .deserialize()
        .expect("typed page");
    assert_eq!((empty.items.len(), empty.total), (0, 0));

    let too_big = invoke(&dev, "list_businesses", business_query(100_000))
        .expect_err("limit validated in the core");
    assert_eq!(command_error(too_big).code, CommandErrorCode::InvalidInput);
    let malformed = json!({ "query": { "business_id": "not-a-uuid" } });
    invoke(&dev, "get_business", malformed).expect_err("malformed id never reaches the worker");
    let unknown = json!({ "query": { "business_id": "01900000-0000-7000-8000-000000000001" } });
    let missing = invoke(&dev, "get_business", unknown).expect_err("unknown business");
    assert_eq!(command_error(missing).code, CommandErrorCode::NotFound);

    let seed: SyntheticSeedResult = invoke(&dev, "seed_synthetic_data", json!({}))
        .expect("development seed")
        .deserialize()
        .expect("typed seed result");
    assert!(seed.created);

    let page: BusinessPage = invoke(&dev, "list_businesses", business_query(10))
        .expect("page")
        .deserialize()
        .expect("typed page");
    assert_eq!(page.total, seed.business_count);
    assert!(page.items.iter().all(|item| item.is_synthetic));
    assert!(page.next_cursor.is_some());

    let detail: BusinessDetail = invoke(
        &dev,
        "get_business",
        json!({ "query": { "business_id": seed.business_id } }),
    )
    .expect("detail")
    .deserialize()
    .expect("typed detail");
    assert_eq!(detail.business.id, seed.business_id);
    assert!(!detail.names.is_empty() && !detail.evidence.is_empty());

    let datasets: DatasetPage = invoke(
        &dev,
        "list_datasets",
        json!({ "query": { "project_id": null, "limit": 50, "cursor": null } }),
    )
    .expect("datasets")
    .deserialize()
    .expect("typed datasets");
    assert_eq!(datasets.items[0].dataset.id, seed.dataset_id);

    let (_release_app, release) =
        mock_app(supervisor.clone(), data.path(), Environment::Production);
    let refused = invoke(&release, "seed_synthetic_data", json!({})).expect_err("release build");
    assert_eq!(command_error(refused).code, CommandErrorCode::NotAllowed);

    tauri::async_runtime::block_on(supervisor.stop());
}
