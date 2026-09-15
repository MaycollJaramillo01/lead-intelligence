//! Exercises the IPC layer the React UI uses (command dispatch + JSON serialization) through
//! Tauri's mock runtime, backed by the real Python worker.

mod common;

use std::path::Path;
use std::sync::{Arc, Mutex};

use chrono::Utc;
use lead_intelligence_lib::commands::{AppState, CoreInfo, ExportGrants};
use lead_intelligence_lib::config::Environment;
use lead_intelligence_lib::data::{
    AiInferencePage, AiRunPage, AiStatus, BusinessDetail, BusinessPage, CalibrationReport,
    CampaignSaved, CandidateDetail, CandidatePage, CanonicalField, ContactDetail, ContactOrigin,
    ContactPage, ContactRunPage, ContactSuppression, CrawlRun, CrawlRunPage, CrawlUrlDetail,
    CrawlUrlPage, CriteriaCheck, DatasetCleanupResult, DatasetDiff, DatasetPage,
    DatasetSnapshotDetail, DecisionMakerPage, DiscoveryRun, DiscoveryRunPage, ExplorerCatalog,
    ExplorerPage, ExportCatalog, ExportFormat, ExportItem, ExportPage, ExportPreviewResult,
    ExportStatus, ImportPreview, JobEventPage, JobStatus, LatinoDetail, LatinoRunPage,
    LatinoStatus, LeadScorePage, LeadScorecardDetail, MatchDetail, MatchPage, MatchStatus,
    PipelinePage, PipelineRun, ResearchStatus, ResearchWorkspace, ResolutionRunPage, SalesBrief,
    SavedViewItem, ScoreComparison, ScoreRunPage, ScoringCatalog, ScoringSimulation,
    SelectionMethod, SourceCatalog, SourceRunStatus, SuppressionExport, SyntheticSeedResult,
    TimelinePage, WebsiteAuditDetail, WebsiteAuditPage, WebsiteRunPage,
};
use lead_intelligence_lib::protocol::{
    CommandError, CommandErrorCode, ComponentState, DiagnosticsReport, WorkerState,
};
use lead_intelligence_lib::register_commands;
use lead_intelligence_lib::worker::WorkerSupervisor;
use serde_json::json;
use tauri::Manager;
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
        .manage(ExportGrants::default())
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

    // Latino evidence: the seed evaluated its situations; filters, detail, review and a new
    // evaluation all go through IPC and the real worker
    let verified: BusinessPage = invoke(
        &dev,
        "list_businesses",
        json!({ "query": {
            "project_id": null, "search": null, "status": null,
            "latino_status": "verified_latino_owned", "latino_min_confidence": 0.5,
            "latino_source_kind": "public_registry", "latino_review": null,
            "sort": "name", "limit": 50, "cursor": null
        }}),
    )
    .expect("filtered businesses")
    .deserialize()
    .expect("typed page");
    assert_eq!(verified.total, 1);
    let certified = verified.items[0].id.to_string();
    let latino: LatinoDetail = invoke(
        &dev,
        "get_latino",
        json!({ "query": { "business_id": certified } }),
    )
    .expect("latino detail")
    .deserialize()
    .expect("typed detail");
    assert_eq!(
        latino.effective.expect("assessed").assessment.status,
        LatinoStatus::VerifiedLatinoOwned
    );
    let blank = invoke(
        &dev,
        "review_latino",
        json!({ "request": { "business_id": certified, "status": "unknown", "reason": "  " } }),
    )
    .expect_err("a blank reason never reaches the worker");
    assert_eq!(command_error(blank).code, CommandErrorCode::InvalidInput);
    let unsupported = invoke(
        &dev,
        "review_latino",
        json!({ "request": {
            "business_id": certified, "status": "spanish_market_affinity", "reason": "IPC test"
        }}),
    )
    .expect_err("no market evidence behind it");
    assert_eq!(
        command_error(unsupported).code,
        CommandErrorCode::InvalidInput
    );
    let reviewed: LatinoDetail = invoke(
        &dev,
        "review_latino",
        json!({ "request": {
            "business_id": certified, "status": "probable_latino_owned", "reason": "IPC test"
        }}),
    )
    .expect("review")
    .deserialize()
    .expect("typed detail");
    assert_eq!(reviewed.decisions[0].reason, "IPC test");
    let mut runs: LatinoRunPage = invoke(
        &dev,
        "start_latino",
        json!({ "request": { "project_id": null } }),
    )
    .expect("evaluation started")
    .deserialize()
    .expect("typed runs");
    assert_eq!(runs.items.len(), 1);
    for _ in 0..400 {
        runs = invoke(
            &dev,
            "list_latino_runs",
            json!({ "query": { "project_id": null, "limit": 10, "cursor": null } }),
        )
        .expect("evaluation runs")
        .deserialize()
        .expect("typed runs");
        if runs
            .items
            .iter()
            .all(|run| !matches!(run.status, JobStatus::Queued | JobStatus::Running))
        {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    assert!(
        runs.items
            .iter()
            .all(|run| run.status == JobStatus::Succeeded)
    );

    // website intelligence: the seed audited its crawled sites twice; audits, a detail with its
    // difference and a new audit all go through IPC and the real worker
    let site_audits: WebsiteAuditPage = invoke(
        &dev,
        "list_website_audits",
        json!({ "query": { "project_id": null, "site": null, "limit": 50, "cursor": null } }),
    )
    .expect("website audits")
    .deserialize()
    .expect("typed audits");
    let changed = site_audits
        .items
        .iter()
        .find(|item| item.previous_audit_id.is_some())
        .expect("a site audited after a second crawl");
    let site_detail: WebsiteAuditDetail = invoke(
        &dev,
        "get_website_audit",
        json!({ "query": { "audit_id": changed.id } }),
    )
    .expect("website audit")
    .deserialize()
    .expect("typed detail");
    assert!(site_detail.diff.is_some() && !site_detail.findings.is_empty());
    let orphan = invoke(
        &dev,
        "start_website_audit",
        json!({ "request": { "project_id": null, "sites": [changed.site] } }),
    )
    .expect_err("sites are chosen within a project");
    assert_eq!(command_error(orphan).code, CommandErrorCode::InvalidInput);
    let mut audit_runs: WebsiteRunPage = invoke(
        &dev,
        "start_website_audit",
        json!({ "request": { "project_id": null, "sites": [] } }),
    )
    .expect("audit started")
    .deserialize()
    .expect("typed runs");
    assert_eq!(audit_runs.items.len(), 1);
    for _ in 0..400 {
        audit_runs = invoke(
            &dev,
            "list_website_runs",
            json!({ "query": { "project_id": null, "limit": 10, "cursor": null } }),
        )
        .expect("audit runs")
        .deserialize()
        .expect("typed runs");
        if audit_runs
            .items
            .iter()
            .all(|run| !matches!(run.status, JobStatus::Queued | JobStatus::Running))
        {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    assert!(
        audit_runs
            .items
            .iter()
            .all(|run| run.status == JobStatus::Succeeded)
    );

    // contact intelligence: the seed checked its contacts; channels, a detail, a review, the review
    // queue, the do-not-contact list and a new run all go through IPC and the real worker
    let listed: ContactPage = invoke(
        &dev,
        "list_contacts",
        json!({ "query": {
            "project_id": null, "contact_type": null, "validation_status": null, "role": null,
            "freshness": null, "include_hypotheses": false, "include_suppressed": false,
            "limit": 500, "cursor": null
        }}),
    )
    .expect("contacts")
    .deserialize()
    .expect("typed contacts");
    assert!(
        listed
            .items
            .iter()
            .all(|item| !item.suppressed && item.origin != ContactOrigin::Hypothesis)
    );
    let form = listed
        .items
        .iter()
        .find(|item| item.key.starts_with("contact_form:"))
        .expect("a published contact form");
    let contacts: ContactDetail = invoke(
        &dev,
        "get_contacts",
        json!({ "query": { "business_id": form.business_id } }),
    )
    .expect("contact detail")
    .deserialize()
    .expect("typed detail");
    assert!(!contacts.hypotheses.is_empty());
    let unexplained = invoke(
        &dev,
        "act_on_contact",
        json!({ "request": {
            "business_id": form.business_id, "key": form.key, "action": "confirm",
            "reason": null, "check_mail_domains": false
        }}),
    )
    .expect_err("a confirmation needs a reason");
    assert_eq!(
        command_error(unexplained).code,
        CommandErrorCode::InvalidInput
    );
    let confirmed: ContactDetail = invoke(
        &dev,
        "act_on_contact",
        json!({ "request": {
            "business_id": form.business_id, "key": form.key, "action": "confirm",
            "reason": "IPC test", "check_mail_domains": false
        }}),
    )
    .expect("confirmed")
    .deserialize()
    .expect("typed detail");
    assert!(
        confirmed
            .decisions
            .iter()
            .any(|decision| decision.reason == "IPC test")
    );
    let queue: DecisionMakerPage = invoke(
        &dev,
        "list_decision_makers",
        json!({ "query": {
            "project_id": null, "review_status": "ambiguous", "limit": 50, "cursor": null
        }}),
    )
    .expect("review queue")
    .deserialize()
    .expect("typed queue");
    assert!(!queue.items.is_empty());
    let entry: ContactSuppression = invoke(
        &dev,
        "change_suppression",
        json!({ "request": {
            "suppression_id": null, "kind": "domain", "value": "synthetic-ipc-0797.example",
            "reason": "IPC test"
        }}),
    )
    .expect("suppression added")
    .deserialize()
    .expect("typed entry");
    let export: SuppressionExport = invoke(
        &dev,
        "export_suppressions",
        json!({ "request": { "include_lifted": true } }),
    )
    .expect("export")
    .deserialize()
    .expect("typed export");
    assert!(export.content.contains(&entry.value));
    let mut contact_runs: ContactRunPage = invoke(
        &dev,
        "start_contacts",
        json!({ "request": { "project_id": null, "check_mail_domains": false } }),
    )
    .expect("contact run started")
    .deserialize()
    .expect("typed runs");
    assert_eq!(contact_runs.items.len(), 1);
    for _ in 0..400 {
        contact_runs = invoke(
            &dev,
            "list_contact_runs",
            json!({ "query": { "project_id": null, "limit": 10, "cursor": null } }),
        )
        .expect("contact runs")
        .deserialize()
        .expect("typed runs");
        if contact_runs
            .items
            .iter()
            .all(|run| !matches!(run.status, JobStatus::Queued | JobStatus::Running))
        {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    let latest = &contact_runs.items[0];
    assert_eq!(latest.status, JobStatus::Succeeded);
    assert!(!latest.check_mail_domains && latest.stats.mail_domains_checked == 0);

    // local AI: without a runtime (a closed port) every task says why it is unavailable, requests
    // are checked in the core, and a run skips instead of failing
    let ai: AiStatus = invoke(
        &dev,
        "get_ai_status",
        json!({ "query": { "include_llm_fit": false } }),
    )
    .expect("ai status")
    .deserialize()
    .expect("typed status");
    assert!(!ai.runtime.running && ai.models.is_empty());
    assert!(ai.tasks.iter().all(|task| !task.available));
    let unconfirmed = invoke(
        &dev,
        "change_ai_model",
        json!({ "request": { "action": "pull", "model": "qwen3:8b", "confirmation": "qwen3" } }),
    )
    .expect_err("a download needs the exact name");
    assert_eq!(
        command_error(unconfirmed).code,
        CommandErrorCode::InvalidInput
    );
    let not_running = invoke(
        &dev,
        "run_ai_benchmark",
        json!({ "request": { "action": "start", "models": ["qwen3:8b"], "task_types": [] } }),
    )
    .expect_err("nothing to benchmark without a runtime");
    assert_eq!(
        command_error(not_running).code,
        CommandErrorCode::InvalidInput
    );
    let mut ai_runs: AiRunPage = invoke(
        &dev,
        "start_ai",
        json!({ "request": {
            "project_id": seed.project_id, "business_ids": [], "task_types": ["language_detection"]
        }}),
    )
    .expect("ai run started")
    .deserialize()
    .expect("typed runs");
    for _ in 0..400 {
        ai_runs = invoke(
            &dev,
            "list_ai_runs",
            json!({ "query": { "project_id": null, "limit": 10, "cursor": null } }),
        )
        .expect("ai runs")
        .deserialize()
        .expect("typed runs");
        if ai_runs
            .items
            .iter()
            .all(|run| !matches!(run.status, JobStatus::Queued | JobStatus::Running))
        {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    let ai_run = &ai_runs.items[0];
    assert_eq!(ai_run.status, JobStatus::Succeeded);
    assert_eq!(ai_run.stats.skipped_unavailable, ai_run.stats.targets);
    let history: AiInferencePage = invoke(
        &dev,
        "list_ai_inferences",
        json!({ "query": {
            "task_type": null, "status": null, "business_id": null, "review_status": null,
            "limit": 50, "cursor": null
        }}),
    )
    .expect("inference history")
    .deserialize()
    .expect("typed history");
    assert_eq!(history.total, 0);

    // lead scoring: the seed scored its project twice; the catalogue, a ranked page, a scorecard, a
    // review without and with a reason, a simulation over stored facts, a new run, a comparison and
    // the calibration all go through IPC and the real worker
    let catalog: ScoringCatalog = invoke(
        &dev,
        "get_scoring_catalog",
        json!({ "query": { "project_id": null } }),
    )
    .expect("scoring catalogue")
    .deserialize()
    .expect("typed catalogue");
    let profile = catalog
        .icp_profiles
        .iter()
        .find(|view| view.profile.project_id == seed.project_id)
        .expect("the synthetic ICP");
    let icp_version = profile
        .versions
        .iter()
        .find(|version| Some(&version.id) == profile.profile.active_version_id.as_ref())
        .expect("an active ICP version");
    let heuristic = catalog
        .models
        .iter()
        .find(|view| view.model.key == "lead_intelligence.heuristic")
        .expect("the default model");
    let leads: LeadScorePage = invoke(
        &dev,
        "list_lead_scores",
        json!({ "query": {
            "score_run_id": null, "tier": null, "min_score": null, "problem": null,
            "search": null, "limit": 5, "cursor": null
        }}),
    )
    .expect("ranked leads")
    .deserialize()
    .expect("typed leads");
    let scored_run = leads.score_run.clone().expect("the newest finished run");
    let lead = &leads.items[0];
    let scorecard: LeadScorecardDetail = invoke(
        &dev,
        "get_lead_scorecard",
        json!({ "query": { "business_id": lead.business_id, "score_run_id": scored_run.score_run_id } }),
    )
    .expect("scorecard")
    .deserialize()
    .expect("typed scorecard");
    assert_eq!(scorecard.rank, lead.rank);
    let review = |reason: &str| {
        invoke(
            &dev,
            "act_on_lead",
            json!({ "request": {
                "score_run_id": scored_run.score_run_id, "business_id": lead.business_id,
                "action": "confirm", "stage": null, "lost_reason": null, "reason": reason
            }}),
        )
    };
    let unexplained = review(" ").expect_err("a review needs a reason");
    assert_eq!(
        command_error(unexplained).code,
        CommandErrorCode::InvalidInput
    );
    let reviewed: LeadScorecardDetail = review("IPC test")
        .expect("reviewed")
        .deserialize()
        .expect("typed scorecard");
    assert!(reviewed.decisions.iter().any(|d| d.reason == "IPC test"));
    let simulation: ScoringSimulation = invoke(
        &dev,
        "simulate_scoring",
        json!({ "request": {
            "project_id": null, "facts_from_run_id": scored_run.score_run_id,
            "icp": icp_version.definition_json, "scoring": heuristic.versions[0].definition_json,
            "top": 3
        }}),
    )
    .expect("simulation")
    .deserialize()
    .expect("typed simulation");
    assert_eq!(simulation.businesses, scored_run.stats.businesses);
    let mut score_runs: ScoreRunPage = invoke(
        &dev,
        "start_scoring",
        json!({ "request": {
            "project_id": seed.project_id, "icp_profile_version_id": icp_version.id,
            "scoring_model_version_id": heuristic.versions[0].id,
            "facts_from_run_id": scored_run.score_run_id
        }}),
    )
    .expect("score run started")
    .deserialize()
    .expect("typed runs");
    for _ in 0..400 {
        score_runs = invoke(
            &dev,
            "list_score_runs",
            json!({ "query": { "project_id": null, "limit": 10, "cursor": null } }),
        )
        .expect("score runs")
        .deserialize()
        .expect("typed runs");
        if score_runs
            .items
            .iter()
            .all(|run| !matches!(run.status, JobStatus::Queued | JobStatus::Running))
        {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    let rescored = &score_runs.items[0];
    assert_eq!(rescored.status, JobStatus::Succeeded);
    let comparison: ScoreComparison = invoke(
        &dev,
        "compare_score_runs",
        json!({ "query": {
            "left_run_id": scored_run.score_run_id, "right_run_id": rescored.score_run_id,
            "top_k": 10
        }}),
    )
    .expect("comparison")
    .deserialize()
    .expect("typed comparison");
    assert!(comparison.same_facts);
    let report: CalibrationReport = invoke(
        &dev,
        "get_calibration",
        json!({ "query": { "score_run_id": scored_run.score_run_id } }),
    )
    .expect("calibration")
    .deserialize()
    .expect("typed report");
    assert!(report.weights_are_heuristic && report.labeled <= report.businesses);

    // lead explorer and research: the catalogue, a window of the newest run, filters nested too
    // deep for the core, a status without and with a reason, a saved view, the brief and the
    // timeline all go through IPC and the real worker
    let explorer: ExplorerCatalog = invoke(
        &dev,
        "get_explorer_catalog",
        json!({ "query": { "scope": "lead_explorer" } }),
    )
    .expect("explorer catalogue")
    .deserialize()
    .expect("typed catalogue");
    let empty_group = json!({ "kind": "group", "join": "and", "negate": false, "children": [] });
    let window_query = |filters: serde_json::Value| {
        json!({ "query": {
            "score_run_id": null, "filters": filters, "search": null, "sort": [], "offset": 0,
            "limit": 10, "with_total": true
        }})
    };
    let window: ExplorerPage = invoke(&dev, "explore_leads", window_query(empty_group.clone()))
        .expect("a window of leads")
        .deserialize()
        .expect("typed window");
    assert_eq!(window.total, Some(seed.business_count));
    let deep = json!({ "kind": "group", "join": "and", "negate": false, "children": [
        { "kind": "group", "join": "or", "negate": false, "children": [
            { "kind": "group", "join": "and", "negate": false, "children": [empty_group] }
        ]}
    ]});
    let too_deep = invoke(&dev, "explore_leads", window_query(deep))
        .expect_err("filters nest at most three groups deep");
    assert_eq!(command_error(too_deep).code, CommandErrorCode::InvalidInput);
    let researched = window.rows[0].business_id.clone();
    let set_status = |reason: serde_json::Value| {
        invoke(
            &dev,
            "act_on_research",
            json!({ "request": {
                "business_id": researched, "action": "set_status", "status": "disqualified",
                "assignee": null, "tag": null, "note": null, "note_id": null, "ref_kind": null,
                "ref": null, "task_id": null, "title": null, "item_key": null, "field": null,
                "value": null, "scopes": [], "reason": reason
            }}),
        )
    };
    let unexplained = set_status(serde_json::Value::Null).expect_err("a status needs a reason");
    assert_eq!(
        command_error(unexplained).code,
        CommandErrorCode::InvalidInput
    );
    let workspace: ResearchWorkspace = set_status(json!("IPC test"))
        .expect("disqualified")
        .deserialize()
        .expect("typed workspace");
    assert_eq!(
        workspace.research.expect("research state").status,
        Some(ResearchStatus::Disqualified)
    );
    let view: SavedViewItem = invoke(
        &dev,
        "save_saved_view",
        json!({ "request": {
            "view_id": null, "scope": "lead_explorer", "name": "IPC view", "description": null,
            "definition": explorer.default_view
        }}),
    )
    .expect("saved view")
    .deserialize()
    .expect("typed view");
    assert_eq!(view.versions.len(), 1);
    let brief: SalesBrief = invoke(
        &dev,
        "get_sales_brief",
        json!({ "query": { "business_id": researched, "score_run_id": null } }),
    )
    .expect("sales brief")
    .deserialize()
    .expect("typed brief");
    assert_eq!(brief.research_status, Some(ResearchStatus::Disqualified));
    let timeline: TimelinePage = invoke(
        &dev,
        "get_lead_timeline",
        json!({ "query": {
            "business_id": researched, "kinds": ["review"], "limit": 20, "cursor": null
        }}),
    )
    .expect("timeline")
    .deserialize()
    .expect("typed timeline");
    assert!(
        timeline
            .entries
            .iter()
            .any(|entry| entry.detail.as_deref() == Some("IPC test"))
    );

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

    // 9. entity resolution: the repeated row becomes part of one business, reversibly
    let started: ResolutionRunPage = invoke(
        &webview,
        "start_resolution",
        json!({ "request": { "project_id": null } }),
    )
    .expect("resolution started")
    .deserialize()
    .expect("typed runs");
    assert_eq!(started.items.len(), 1);
    let mut settled = started;
    for _ in 0..400 {
        settled = invoke(
            &webview,
            "list_resolution_runs",
            json!({ "query": { "project_id": null, "limit": 10, "cursor": null } }),
        )
        .expect("resolution runs")
        .deserialize()
        .expect("typed runs");
        if settled
            .items
            .iter()
            .all(|run| !matches!(run.status, JobStatus::Queued | JobStatus::Running))
        {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    assert_eq!(settled.items[0].status, JobStatus::Succeeded);

    let resolved: BusinessPage = invoke(&webview, "list_businesses", business_query(50))
        .expect("businesses")
        .deserialize()
        .expect("typed page");
    assert_eq!(resolved.total, 2, "three rows, two businesses");
    let sol = resolved
        .items
        .iter()
        .find(|item| item.member_count == 2)
        .expect("the repeated row joined its business");

    let merged: MatchPage = invoke(
        &webview,
        "list_matches",
        json!({ "query": {
            "project_id": null, "status": "merged", "risk": null, "min_score": null,
            "connector_key": "file.import", "limit": 50, "cursor": null
        }}),
    )
    .expect("queue")
    .deserialize()
    .expect("typed queue");
    assert_eq!(merged.total, 1);
    let pair_id = merged.items[0].id.to_string();

    let blank = invoke(
        &webview,
        "act_on_match",
        json!({ "request": { "match_id": pair_id, "action": "defer", "reason": "  " } }),
    )
    .expect_err("a blank reason never reaches the worker");
    assert_eq!(command_error(blank).code, CommandErrorCode::InvalidInput);

    let undone: MatchDetail = invoke(
        &webview,
        "act_on_match",
        json!({ "request": { "match_id": pair_id, "action": "undo", "reason": "IPC test" } }),
    )
    .expect("undo")
    .deserialize()
    .expect("typed detail");
    assert_eq!(undone.pair.status, MatchStatus::Pending);
    assert_eq!(undone.decisions[0].reason, "IPC test");
    let split: BusinessPage = invoke(&webview, "list_businesses", business_query(50))
        .expect("businesses")
        .deserialize()
        .expect("typed page");
    assert_eq!(
        split.total, 3,
        "undoing the merge gives the record back its own business"
    );

    let business: BusinessDetail = invoke(
        &webview,
        "get_business",
        json!({ "query": { "business_id": sol.id } }),
    )
    .expect("detail")
    .deserialize()
    .expect("typed detail");
    let chosen: BusinessDetail = invoke(
        &webview,
        "choose_canonical_value",
        json!({ "request": {
            "business_id": sol.id, "field": "name",
            "observation_id": business.names[0].value.id, "reason": null
        }}),
    )
    .expect("choice")
    .deserialize()
    .expect("typed detail");
    let name = chosen
        .canonical
        .iter()
        .find(|value| value.field == CanonicalField::Name)
        .expect("a name is shown");
    assert_eq!(name.method, SelectionMethod::Manual);

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

/// Polls the pipeline listing the way the Jobs screen does, until the pipeline stops.
fn settled_pipeline(
    webview: &tauri::WebviewWindow<tauri::test::MockRuntime>,
    project_id: &str,
    job_id: &str,
) -> PipelinePage {
    for _ in 0..1200 {
        let page: PipelinePage = invoke(
            webview,
            "list_pipelines",
            json!({ "query": { "project_id": project_id, "limit": 5, "cursor": null } }),
        )
        .expect("pipelines")
        .deserialize()
        .expect("typed pipelines");
        let run = page
            .items
            .iter()
            .find(|run| run.job_id.to_string() == job_id)
            .expect("the pipeline is listed");
        if !matches!(
            run.status,
            JobStatus::Queued | JobStatus::Running | JobStatus::Paused
        ) {
            return page;
        }
        std::thread::sleep(std::time::Duration::from_millis(250));
    }
    panic!("the pipeline never settled");
}

/// A pipeline over the synthetic project drives the real engines; its snapshot is inspected,
/// exported through a save-dialog grant, captured again, compared and retained.
#[test]
fn a_pipeline_snapshot_and_export_run_end_to_end_through_ipc() {
    let data = tempfile::tempdir().expect("temp dir");
    let chosen = tempfile::tempdir().expect("chosen folder");
    let supervisor = WorkerSupervisor::new(common::real_worker(data.path()), common::quiet());
    tauri::async_runtime::block_on(supervisor.start()).expect("worker starts");
    let (app, webview) = mock_app(supervisor.clone(), data.path(), Environment::Development);
    let seed: SyntheticSeedResult = invoke(&webview, "seed_synthetic_data", json!({}))
        .expect("seed")
        .deserialize()
        .expect("typed seed");
    let project = seed.project_id.to_string();

    // 1. a repeated key returns the pipeline it already started
    let start = json!({ "request": {
        "project_id": project, "campaign_id": null, "check_mail_domains": false,
        "priority": 0, "idempotency_key": "ipc-real-pipeline"
    }});
    let run: PipelineRun = invoke(&webview, "start_pipeline", start.clone())
        .expect("pipeline starts")
        .deserialize()
        .expect("typed pipeline");
    let again: PipelineRun = invoke(&webview, "start_pipeline", start)
        .expect("same key")
        .deserialize()
        .expect("typed pipeline");
    assert_eq!(again.job_id, run.job_id);
    let job_id = run.job_id.to_string();

    let page = settled_pipeline(&webview, &project, &job_id);
    let run = page
        .items
        .into_iter()
        .find(|item| item.job_id.to_string() == job_id)
        .expect("listed");
    assert_eq!(run.status, JobStatus::Succeeded, "{:?}", run.dead_letters);
    assert!(page.resources.cpu_count >= 1);
    let snapshot_id = run.snapshot_id.expect("eligibility wrote a snapshot");

    // 2. the persisted log holds the pipeline's events and its engine jobs'
    let events: JobEventPage = invoke(
        &webview,
        "list_job_events",
        json!({ "query": {
            "job_id": job_id, "task_id": null, "business_id": null, "request_id": null,
            "levels": [], "limit": 100, "cursor": null
        }}),
    )
    .expect("events")
    .deserialize()
    .expect("typed events");
    assert!(events.total >= 10);

    // 3. the snapshot has its measured quality report and its lineage
    let detail: DatasetSnapshotDetail = invoke(
        &webview,
        "get_dataset_snapshot",
        json!({ "query": { "snapshot_id": snapshot_id } }),
    )
    .expect("snapshot")
    .deserialize()
    .expect("typed snapshot");
    assert_eq!(detail.quality.rows, detail.item.snapshot.row_count);
    assert!(!detail.lineage.is_empty());

    // 4. exporting needs a grant from the core's own save dialog, used once, for its format
    let catalog: ExportCatalog = invoke(
        &webview,
        "get_export_catalog",
        json!({ "query": { "include_superseded": false } }),
    )
    .expect("catalogue")
    .deserialize()
    .expect("typed catalogue");
    assert!(catalog.formats.contains(&ExportFormat::Csv));
    let draft = json!({
        "source_kind": "snapshot", "query": null, "view_id": null, "snapshot_id": snapshot_id,
        "format": "csv",
        "columns": [
            { "key": "display_name", "header": "Business" }, { "key": "tier", "header": "Tier" }
        ],
        "bom": true, "include_suppressed": false, "suppressed_reason": null,
        "preset_key": null, "preset_version": null
    });
    let preview: ExportPreviewResult =
        invoke(&webview, "preview_export", json!({ "draft": draft }))
            .expect("preview")
            .deserialize()
            .expect("typed preview");
    let unknown = invoke(
        &webview,
        "start_export",
        json!({ "draft": draft, "grantId": "not-a-grant" }),
    )
    .expect_err("no grant");
    assert_eq!(command_error(unknown).code, CommandErrorCode::InvalidInput);
    let grants = app.state::<ExportGrants>();
    let spreadsheet = grants.issue(chosen.path().join("leads.xlsx"), ExportFormat::Xlsx);
    let other_format = invoke(
        &webview,
        "start_export",
        json!({ "draft": draft, "grantId": spreadsheet }),
    )
    .expect_err("a grant for another format");
    assert_eq!(
        command_error(other_format).code,
        CommandErrorCode::InvalidInput
    );
    let destination = chosen.path().join("leads.csv");
    let grant = grants.issue(destination.clone(), ExportFormat::Csv);
    let started: ExportItem = invoke(
        &webview,
        "start_export",
        json!({ "draft": draft, "grantId": grant }),
    )
    .expect("export starts")
    .deserialize()
    .expect("typed export");
    let reused = invoke(
        &webview,
        "start_export",
        json!({ "draft": draft, "grantId": grant }),
    )
    .expect_err("a grant is used once");
    assert_eq!(command_error(reused).code, CommandErrorCode::InvalidInput);

    let mut written = None;
    for _ in 0..600 {
        let exports: ExportPage = invoke(
            &webview,
            "list_exports",
            json!({ "query": { "project_id": project, "limit": 10, "cursor": null } }),
        )
        .expect("exports")
        .deserialize()
        .expect("typed exports");
        let item = exports
            .items
            .into_iter()
            .find(|item| item.export.id == started.export.id)
            .expect("listed");
        if matches!(
            item.export.status,
            ExportStatus::Succeeded | ExportStatus::Failed | ExportStatus::Cancelled
        ) {
            written = Some(item);
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
    let written = written.expect("the export finished");
    assert_eq!(
        written.export.status,
        ExportStatus::Succeeded,
        "{:?}",
        written.export.error_message
    );
    assert_eq!(written.export.row_count, Some(preview.exported));
    assert!(destination.exists());
    assert!(chosen.path().join("leads.csv.manifest.json").exists());

    // 5. a second capture is the next version; the first stays because it was exported
    let second: DatasetSnapshotDetail = invoke(
        &webview,
        "create_dataset_snapshot",
        json!({ "request": {
            "project_id": project, "dataset_name": detail.item.dataset_name,
            "description": null, "score_run_id": null
        }}),
    )
    .expect("second snapshot")
    .deserialize()
    .expect("typed snapshot");
    assert_eq!(
        second.item.snapshot.version,
        detail.item.snapshot.version + 1
    );
    let diff: DatasetDiff = invoke(
        &webview,
        "diff_dataset_snapshots",
        json!({ "query": {
            "before_snapshot_id": snapshot_id, "after_snapshot_id": second.item.snapshot.id,
            "limit": 50
        }}),
    )
    .expect("diff")
    .deserialize()
    .expect("typed diff");
    assert_eq!(diff.added_count, 0, "nothing changed between the captures");
    let retention: DatasetCleanupResult = invoke(
        &webview,
        "cleanup_dataset",
        json!({ "request": {
            "dataset_id": detail.item.snapshot.dataset_id, "keep_latest": 1,
            "mode": "preview", "reason": null
        }}),
    )
    .expect("retention preview")
    .deserialize()
    .expect("typed retention");
    assert!(retention.deleted.is_empty());
    assert!(retention.kept.iter().any(|kept| {
        kept.snapshot_id == detail.item.snapshot.id
            && kept.kept_because.as_deref() == Some("exported")
    }));

    tauri::async_runtime::block_on(supervisor.stop());
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
