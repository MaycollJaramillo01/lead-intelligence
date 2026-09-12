//! Exercises the IPC layer the React UI uses (command dispatch + JSON serialization) through
//! Tauri's mock runtime, backed by the real Python worker.

mod common;

use std::path::Path;
use std::sync::{Arc, Mutex};

use chrono::Utc;
use lead_intelligence_lib::commands::{AppState, CoreInfo};
use lead_intelligence_lib::config::Environment;
use lead_intelligence_lib::data::{
    BusinessDetail, BusinessPage, CampaignSaved, CandidateDetail, CandidatePage, CrawlRun,
    CrawlRunPage, CrawlUrlDetail, CrawlUrlPage, CriteriaCheck, DatasetPage, DiscoveryRun,
    DiscoveryRunPage, ImportPreview, JobStatus, SourceCatalog, SourceRunStatus,
    SyntheticSeedResult,
};
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

const IMPORT_CSV: &str = concat!(
    "name,city,state,zip,website,phone,category\n",
    "Panaderia El Sol,Austin,TX,78701,https://panaderia-el-sol.example,(512) 555-0101,Bakery\n",
    "Techo Seguro Roofing,Miami,FL,33130,techo-seguro.example,(305) 555-0102,Roofing\n",
    "Panaderia El Sol,Austin,TX,78701,https://panaderia-el-sol.example,(512) 555-0101,Bakery\n",
);

fn file_import_criteria() -> serde_json::Value {
    json!({
        "schema_version": 1,
        "geography": [{
            "country_code": "US", "region": "TX", "metro": null,
            "locality": "Austin", "postal_code": null, "radius_km": null
        }],
        "verticals": [], "categories": [], "services": [], "keywords": [],
        "exclusions": { "names": [], "domains": [], "categories": [], "postal_codes": [] },
        "market_languages": ["es"], "size_proxies": [], "freshness_since": null,
        "strategy": "broad",
        "sources": [{
            "connector_key": "file.import", "priority": 50, "max_records": null, "config_json": {}
        }],
        "limits": { "max_candidates": 100, "max_requests": 10, "max_pages_per_source": 5 }
    })
}

/// Polls the run listing the way the interface does, until the run stops moving.
fn settled_run(
    webview: &tauri::WebviewWindow<tauri::test::MockRuntime>,
    campaign_id: &str,
) -> DiscoveryRun {
    for _ in 0..200 {
        let page: DiscoveryRunPage = invoke(
            webview,
            "list_discovery_runs",
            json!({ "query": {
                "project_id": null, "campaign_id": campaign_id, "limit": 10, "cursor": null
            }}),
        )
        .expect("runs")
        .deserialize()
        .expect("typed runs");
        let run = page.items.into_iter().next().expect("one run");
        let working = run.sources.iter().any(|source| {
            matches!(
                source.status,
                SourceRunStatus::Queued | SourceRunStatus::Running
            )
        });
        if !working && !matches!(run.status, JobStatus::Queued | JobStatus::Running) {
            return run;
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    panic!("the discovery run never settled");
}

#[test]
fn the_discovery_flow_runs_end_to_end_through_ipc() {
    let data = tempfile::tempdir().expect("temp dir");
    let supervisor = WorkerSupervisor::new(common::real_worker(data.path()), common::quiet());
    tauri::async_runtime::block_on(supervisor.start()).expect("worker starts");
    let (_app, webview) = mock_app(supervisor.clone(), data.path(), Environment::Development);

    // 1. the catalogue is real: every source states its terms, its limits and its health
    let catalog: SourceCatalog = invoke(
        &webview,
        "list_sources",
        json!({ "query": {"project_id": null} }),
    )
    .expect("sources")
    .deserialize()
    .expect("typed catalogue");
    assert!(catalog.sources.len() >= 4);
    let keyed = catalog
        .sources
        .iter()
        .find(|source| source.metadata.requires_credentials)
        .expect("one source needs credentials");
    assert!(!keyed.enabled, "a source without its key is not enabled");

    // 2. the file the operator picked is previewed before anything is stored
    let preview: ImportPreview = invoke(
        &webview,
        "preview_import",
        json!({ "request": {
            "format": null, "filename": "leads.csv", "content": IMPORT_CSV, "mapping": null
        }}),
    )
    .expect("preview")
    .deserialize()
    .expect("typed preview");
    assert_eq!((preview.total_rows, preview.usable_rows), (3, 3));
    assert_eq!(preview.suggested_mapping.name.as_deref(), Some("name"));

    // 3. a campaign is stored with its criteria, and its warnings come back with it
    let saved: CampaignSaved = invoke(
        &webview,
        "save_campaign",
        json!({ "request": {
            "campaign_id": null, "project_id": null, "name": "IPC import",
            "objective": "Exercise the flow", "criteria": file_import_criteria()
        }}),
    )
    .expect("campaign saved")
    .deserialize()
    .expect("typed campaign");
    assert!(
        saved
            .problems
            .iter()
            .any(|problem| problem.code == "language_is_a_signal"),
        "market language must be reported as a signal, not as proof"
    );
    let campaign_id = saved.campaign.id.to_string();

    // 4. validation with an estimate, marked as an estimate
    let check: CriteriaCheck = invoke(
        &webview,
        "validate_criteria",
        json!({ "query": {
            "project_id": null, "criteria": file_import_criteria(), "with_estimates": true
        }}),
    )
    .expect("validated")
    .deserialize()
    .expect("typed check");
    assert_eq!(check.estimates.len(), 1);
    assert!(!check.estimates[0].basis.is_empty());

    // 5. the run itself, with the file travelling once
    let started: DiscoveryRun = invoke(
        &webview,
        "start_discovery",
        json!({ "request": {
            "campaign_id": campaign_id,
            "connector_keys": [],
            "inputs": { "file.import": {
                "filename": "leads.csv", "format": "csv", "content": IMPORT_CSV, "mapping": null
            }}
        }}),
    )
    .expect("run started")
    .deserialize()
    .expect("typed run");
    assert_eq!(started.campaign_name, "IPC import");

    let run = settled_run(&webview, &campaign_id);
    assert_eq!(run.status, JobStatus::Succeeded);
    assert_eq!((run.candidate_count, run.duplicate_count), (2, 1));

    // 6. candidates, without the repeat unless it is asked for
    let candidates: CandidatePage = invoke(
        &webview,
        "list_candidates",
        json!({ "query": {
            "project_id": null, "campaign_id": campaign_id, "source_run_id": null,
            "search": null, "include_duplicates": false, "limit": 50, "cursor": null
        }}),
    )
    .expect("candidates")
    .deserialize()
    .expect("typed candidates");
    assert_eq!(candidates.total, 2);
    assert!(candidates.items.iter().all(|item| !item.is_duplicate));

    // 7. the raw record behind one of them, with its provenance
    let detail: CandidateDetail = invoke(
        &webview,
        "get_candidate",
        json!({ "query": { "candidate_id": candidates.items[0].id } }),
    )
    .expect("candidate detail")
    .deserialize()
    .expect("typed detail");
    assert_eq!(detail.connector.key, "file.import");
    assert!(detail.raw_record.payload_json.contains_key("name"));
    assert!(detail.acquired_page.expect("page").artifact_path.is_some());

    // 8. a run that cannot start says why, instead of starting a useless one
    let refused = invoke(
        &webview,
        "start_discovery",
        json!({ "request": {
            "campaign_id": campaign_id, "connector_keys": [], "inputs": {}
        }}),
    )
    .expect_err("no file, no run");
    assert_eq!(command_error(refused).code, CommandErrorCode::InvalidInput);

    tauri::async_runtime::block_on(supervisor.stop());
}

fn crawl_settings() -> serde_json::Value {
    json!({
        "max_sites": 10, "max_pages_per_site": 5, "max_depth": 1,
        "max_bytes_per_site": 1_000_000, "max_seconds_per_site": 60, "max_requests": 50,
        "requests_per_minute_per_host": 60, "per_host_concurrency": 1,
        "include_subdomains": false, "include_paths": [], "exclude_paths": [],
        "browser": "never", "screenshots": false
    })
}

/// Crawls addresses that must never be fetched: loopback, a local name and the cloud metadata
/// endpoint. The whole flow runs through IPC and the real worker, and nothing leaves the machine.
#[test]
fn the_crawl_flow_runs_end_to_end_through_ipc() {
    let data = tempfile::tempdir().expect("temp dir");
    let supervisor = WorkerSupervisor::new(common::real_worker(data.path()), common::quiet());
    tauri::async_runtime::block_on(supervisor.start()).expect("worker starts");
    let (_app, webview) = mock_app(supervisor.clone(), data.path(), Environment::Development);

    // 1. the core refuses a crawl with nothing to crawl, or one that would hammer a host
    let empty = invoke(
        &webview,
        "start_crawl",
        json!({ "request": {
            "project_id": null, "campaign_id": null, "urls": [], "settings": crawl_settings()
        }}),
    )
    .expect_err("nothing to crawl");
    assert_eq!(command_error(empty).code, CommandErrorCode::InvalidInput);
    let mut hurried = crawl_settings();
    hurried["requests_per_minute_per_host"] = 5000.into();
    let too_fast = invoke(
        &webview,
        "start_crawl",
        json!({ "request": {
            "project_id": null, "campaign_id": null, "urls": ["https://example.com"],
            "settings": hurried
        }}),
    )
    .expect_err("too fast for a host");
    assert_eq!(command_error(too_fast).code, CommandErrorCode::InvalidInput);

    // 2. a crawl of private targets starts, and every site is refused by the SSRF guard
    let started: CrawlRun = invoke(
        &webview,
        "start_crawl",
        json!({ "request": {
            "project_id": null, "campaign_id": null,
            "urls": ["http://127.0.0.1:9/", "localhost:9", "http://169.254.169.254/latest/meta-data/"],
            "settings": crawl_settings()
        }}),
    )
    .expect("crawl started")
    .deserialize()
    .expect("typed run");
    assert_eq!(started.site_count, 3);

    let mut run = started;
    for _ in 0..200 {
        let page: CrawlRunPage = invoke(
            &webview,
            "list_crawl_runs",
            json!({ "query": { "project_id": null, "campaign_id": null, "limit": 10, "cursor": null }}),
        )
        .expect("runs")
        .deserialize()
        .expect("typed runs");
        run = page.items.into_iter().next().expect("one crawl");
        if !matches!(run.status, JobStatus::Queued | JobStatus::Running) {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    assert_eq!(run.status, JobStatus::Failed);
    assert_eq!(run.sites_done, 3);
    assert!(
        run.sites
            .iter()
            .all(|site| site.error_code.as_deref() == Some("unsafe_target")),
        "{:?}",
        run.sites
    );
    assert_eq!(run.metrics.pages_fetched, 0);

    // 3. every refused URL is on record, with the reason and its detail
    let urls: CrawlUrlPage = invoke(
        &webview,
        "list_crawl_urls",
        json!({ "query": {
            "job_id": run.job_id, "source_run_id": null, "status": null, "limit": 50, "cursor": null
        }}),
    )
    .expect("urls")
    .deserialize()
    .expect("typed urls");
    assert!(!urls.items.is_empty());
    let robots = urls
        .items
        .iter()
        .find(|item| item.url.ends_with("/robots.txt"))
        .expect("robots.txt is always the first request");
    assert_eq!(robots.reason.as_deref(), Some("unsafe_target"));
    let detail: CrawlUrlDetail = invoke(
        &webview,
        "get_crawl_url",
        json!({ "query": { "crawl_url_id": robots.id } }),
    )
    .expect("detail")
    .deserialize()
    .expect("typed detail");
    assert_eq!(detail.job_id, run.job_id);
    assert!(
        detail.acquired_page.is_none(),
        "nothing was received from a refused target"
    );

    // 4. a discovery job id is not a crawl
    let control = invoke(
        &webview,
        "control_crawl",
        json!({ "request": { "job_id": "01900000-0000-7000-8000-000000000001", "action": "cancel" } }),
    )
    .expect_err("unknown crawl");
    assert_eq!(command_error(control).code, CommandErrorCode::NotFound);

    tauri::async_runtime::block_on(supervisor.stop());
}
