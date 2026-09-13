//! Tauri IPC surface. Commands validate input, log technical detail, and reject with a
//! `CommandError` whose message the UI can show as-is.

use std::sync::{Arc, Mutex};

use chrono::{DateTime, Utc};
use tauri::State;
use tracing::{Instrument, error, info, info_span, warn};
use tracing_appender::non_blocking::WorkerGuard;
use uuid::Uuid;

use crate::config::Environment;
use crate::data::{
    BusinessDetail, BusinessDetailQuery, BusinessListQuery, BusinessPage, CampaignListQuery,
    CampaignPage, CampaignSaveRequest, CampaignSaved, CandidateDetail, CandidateDetailQuery,
    CandidateListQuery, CandidatePage, CanonicalChoiceRequest, CrawlRun, CrawlRunListQuery,
    CrawlRunPage, CrawlStartRequest, CrawlUrlDetail, CrawlUrlDetailQuery, CrawlUrlListQuery,
    CrawlUrlPage, CriteriaCheck, CriteriaCheckQuery, DatasetListQuery, DatasetPage,
    DiscoveryControlRequest, DiscoveryRun, DiscoveryRunListQuery, DiscoveryRunPage,
    DiscoveryStartRequest, ImportPreview, ImportPreviewRequest, LatinoDetail, LatinoDetailQuery,
    LatinoReviewRequest, LatinoRun, LatinoRunListQuery, LatinoRunPage, LatinoStartRequest,
    MatchActionRequest, MatchDetail, MatchDetailQuery, MatchListQuery, MatchPage, ResolutionRun,
    ResolutionRunListQuery, ResolutionRunPage, ResolutionStartRequest, SourceCatalog,
    SourceCatalogQuery, SyntheticSeedResult,
};
use crate::error::AppError;
use crate::protocol::{
    ComponentState, CoreStatus, DiagnosticsReport, DiagnosticsResult, UiErrorReport, WorkerStatus,
};
use crate::worker::{
    BUSINESSES_CHOOSE_VALUE, BUSINESSES_GET, BUSINESSES_LIST, CAMPAIGNS_LIST, CAMPAIGNS_SAVE,
    CAMPAIGNS_VALIDATE, CRAWL_CONTROL, CRAWL_RUNS, CRAWL_START, CRAWL_URL, CRAWL_URLS,
    DATA_TIMEOUT, DATASETS_LIST, DEV_SEED_SYNTHETIC, DIAGNOSTICS, DIAGNOSTICS_TIMEOUT,
    DISCOVERY_CANDIDATE, DISCOVERY_CANDIDATES, DISCOVERY_CONTROL, DISCOVERY_RUNS, DISCOVERY_START,
    DISCOVERY_TIMEOUT, IMPORT_TIMEOUT, LATINO_CONTROL, LATINO_GET, LATINO_REVIEW, LATINO_RUNS,
    LATINO_START, MATCHES_ACT, MATCHES_GET, MATCHES_LIST, RESOLUTION_CONTROL, RESOLUTION_RUNS,
    RESOLUTION_START, RESOLUTION_TIMEOUT, SEED_TIMEOUT, SOURCES_LIST, SOURCES_PREVIEW_IMPORT,
    WorkerSupervisor,
};

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

/// One keyset page of canonical businesses. The query is validated here and the page is checked
/// against it before it reaches the UI.
#[tauri::command]
pub async fn list_businesses(
    state: State<'_, AppState>,
    query: BusinessListQuery,
) -> Result<BusinessPage, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let page: BusinessPage = state
            .supervisor
            .request_with(BUSINESSES_LIST, &query, DATA_TIMEOUT)
            .await?;
        page.validate(&query).map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("list_businesses"))
    .await
}

/// A business with every observed value and its provenance.
#[tauri::command]
pub async fn get_business(
    state: State<'_, AppState>,
    query: BusinessDetailQuery,
) -> Result<BusinessDetail, AppError> {
    async {
        let detail: BusinessDetail = state
            .supervisor
            .request_with(BUSINESSES_GET, &query, DATA_TIMEOUT)
            .await?;
        detail
            .validate(&query.business_id)
            .map_err(AppError::Protocol)?;
        Ok(detail)
    }
    .instrument(ipc_span("get_business"))
    .await
}

#[tauri::command]
pub async fn list_datasets(
    state: State<'_, AppState>,
    query: DatasetListQuery,
) -> Result<DatasetPage, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let page: DatasetPage = state
            .supervisor
            .request_with(DATASETS_LIST, &query, DATA_TIMEOUT)
            .await?;
        page.validate(&query).map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("list_datasets"))
    .await
}

/// Development builds only: creates (once) a clearly marked synthetic workspace to exercise the
/// data flow. Release builds refuse here and the worker refuses again without `LEAD_DEV_TOOLS`.
#[tauri::command]
pub async fn seed_synthetic_data(
    state: State<'_, AppState>,
) -> Result<SyntheticSeedResult, AppError> {
    async {
        if state.core.environment != Environment::Development {
            warn!("synthetic seed refused outside development");
            return Err(AppError::NotAllowed(
                "synthetic test data exists only in development builds".into(),
            ));
        }
        state
            .supervisor
            .request_typed::<SyntheticSeedResult>(DEV_SEED_SYNTHETIC, SEED_TIMEOUT)
            .await
    }
    .instrument(ipc_span("seed_synthetic_data"))
    .await
}

// ---- discovery (phase 04) ----------------------------------------------------------------------

/// Every source this build ships, with its capabilities, terms, limits and whether it could run.
#[tauri::command]
pub async fn list_sources(
    state: State<'_, AppState>,
    query: SourceCatalogQuery,
) -> Result<SourceCatalog, AppError> {
    async {
        let catalog: SourceCatalog = state
            .supervisor
            .request_with(SOURCES_LIST, &query, DATA_TIMEOUT)
            .await?;
        catalog.validate().map_err(AppError::Protocol)?;
        Ok(catalog)
    }
    .instrument(ipc_span("list_sources"))
    .await
}

/// What a file the operator picked holds, and what the mapping would make of it. Stores nothing.
#[tauri::command]
pub async fn preview_import(
    state: State<'_, AppState>,
    request: ImportPreviewRequest,
) -> Result<ImportPreview, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let preview: ImportPreview = state
            .supervisor
            .request_with(SOURCES_PREVIEW_IMPORT, &request, IMPORT_TIMEOUT)
            .await?;
        preview.validate().map_err(AppError::Protocol)?;
        Ok(preview)
    }
    .instrument(ipc_span("preview_import"))
    .await
}

#[tauri::command]
pub async fn list_campaigns(
    state: State<'_, AppState>,
    query: CampaignListQuery,
) -> Result<CampaignPage, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let page: CampaignPage = state
            .supervisor
            .request_with(CAMPAIGNS_LIST, &query, DATA_TIMEOUT)
            .await?;
        page.validate(&query).map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("list_campaigns"))
    .await
}

/// Creates or replaces one campaign. A draft with problems is still saved; it just cannot run.
#[tauri::command]
pub async fn save_campaign(
    state: State<'_, AppState>,
    request: CampaignSaveRequest,
) -> Result<CampaignSaved, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        state
            .supervisor
            .request_with(CAMPAIGNS_SAVE, &request, DATA_TIMEOUT)
            .await
    }
    .instrument(ipc_span("save_campaign"))
    .await
}

/// Validates criteria and, when asked, collects one marked estimate per source.
#[tauri::command]
pub async fn validate_criteria(
    state: State<'_, AppState>,
    query: CriteriaCheckQuery,
) -> Result<CriteriaCheck, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let check: CriteriaCheck = state
            .supervisor
            .request_with(CAMPAIGNS_VALIDATE, &query, DISCOVERY_TIMEOUT)
            .await?;
        check.validate().map_err(AppError::Protocol)?;
        Ok(check)
    }
    .instrument(ipc_span("validate_criteria"))
    .await
}

/// Starts one discovery run. It keeps running inside the worker after this returns.
#[tauri::command]
pub async fn start_discovery(
    state: State<'_, AppState>,
    request: DiscoveryStartRequest,
) -> Result<DiscoveryRun, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let run: DiscoveryRun = state
            .supervisor
            .request_with(DISCOVERY_START, &request, DISCOVERY_TIMEOUT)
            .await?;
        run.validate().map_err(AppError::Protocol)?;
        Ok(run)
    }
    .instrument(ipc_span("start_discovery"))
    .await
}

/// Pauses, resumes or cancels a run. The decision is stored, so it survives a restart.
#[tauri::command]
pub async fn control_discovery(
    state: State<'_, AppState>,
    request: DiscoveryControlRequest,
) -> Result<DiscoveryRun, AppError> {
    async {
        let run: DiscoveryRun = state
            .supervisor
            .request_with(DISCOVERY_CONTROL, &request, DATA_TIMEOUT)
            .await?;
        run.validate().map_err(AppError::Protocol)?;
        Ok(run)
    }
    .instrument(ipc_span("control_discovery"))
    .await
}

#[tauri::command]
pub async fn list_discovery_runs(
    state: State<'_, AppState>,
    query: DiscoveryRunListQuery,
) -> Result<DiscoveryRunPage, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let page: DiscoveryRunPage = state
            .supervisor
            .request_with(DISCOVERY_RUNS, &query, DATA_TIMEOUT)
            .await?;
        page.validate(&query).map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("list_discovery_runs"))
    .await
}

/// One keyset page of candidates. They are references, never leads: nothing here is qualified.
#[tauri::command]
pub async fn list_candidates(
    state: State<'_, AppState>,
    query: CandidateListQuery,
) -> Result<CandidatePage, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let page: CandidatePage = state
            .supervisor
            .request_with(DISCOVERY_CANDIDATES, &query, DATA_TIMEOUT)
            .await?;
        page.validate(&query).map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("list_candidates"))
    .await
}

/// A candidate with the chain behind it, down to the payload exactly as it was received.
#[tauri::command]
pub async fn get_candidate(
    state: State<'_, AppState>,
    query: CandidateDetailQuery,
) -> Result<CandidateDetail, AppError> {
    async {
        let detail: CandidateDetail = state
            .supervisor
            .request_with(DISCOVERY_CANDIDATE, &query, DATA_TIMEOUT)
            .await?;
        detail
            .validate(&query.candidate_id)
            .map_err(AppError::Protocol)?;
        Ok(detail)
    }
    .instrument(ipc_span("get_candidate"))
    .await
}

// ---- web acquisition (phase 05) -----------------------------------------------------------------

/// Starts a crawl of a campaign's candidate websites and/or the addresses given, within its limits.
#[tauri::command]
pub async fn start_crawl(
    state: State<'_, AppState>,
    request: CrawlStartRequest,
) -> Result<CrawlRun, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let run: CrawlRun = state
            .supervisor
            .request_with(CRAWL_START, &request, DISCOVERY_TIMEOUT)
            .await?;
        run.validate().map_err(AppError::Protocol)?;
        Ok(run)
    }
    .instrument(ipc_span("start_crawl"))
    .await
}

/// Pauses, resumes or cancels a crawl. Pause and cancel stop at once and close the browser.
#[tauri::command]
pub async fn control_crawl(
    state: State<'_, AppState>,
    request: DiscoveryControlRequest,
) -> Result<CrawlRun, AppError> {
    async {
        let run: CrawlRun = state
            .supervisor
            .request_with(CRAWL_CONTROL, &request, DATA_TIMEOUT)
            .await?;
        run.validate().map_err(AppError::Protocol)?;
        Ok(run)
    }
    .instrument(ipc_span("control_crawl"))
    .await
}

#[tauri::command]
pub async fn list_crawl_runs(
    state: State<'_, AppState>,
    query: CrawlRunListQuery,
) -> Result<CrawlRunPage, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let page: CrawlRunPage = state
            .supervisor
            .request_with(CRAWL_RUNS, &query, DATA_TIMEOUT)
            .await?;
        page.validate(&query).map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("list_crawl_runs"))
    .await
}

/// The URLs of one crawl: fetched, skipped with the policy that stopped them, or failed.
#[tauri::command]
pub async fn list_crawl_urls(
    state: State<'_, AppState>,
    query: CrawlUrlListQuery,
) -> Result<CrawlUrlPage, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let page: CrawlUrlPage = state
            .supervisor
            .request_with(CRAWL_URLS, &query, DATA_TIMEOUT)
            .await?;
        page.validate(&query).map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("list_crawl_urls"))
    .await
}

/// One URL with how it was obtained, the stored page and the parsed record.
#[tauri::command]
pub async fn get_crawl_url(
    state: State<'_, AppState>,
    query: CrawlUrlDetailQuery,
) -> Result<CrawlUrlDetail, AppError> {
    async {
        let detail: CrawlUrlDetail = state
            .supervisor
            .request_with(CRAWL_URL, &query, DATA_TIMEOUT)
            .await?;
        detail
            .validate(&query.crawl_url_id)
            .map_err(AppError::Protocol)?;
        Ok(detail)
    }
    .instrument(ipc_span("get_crawl_url"))
    .await
}

// ---- entity resolution (phase 06) ---------------------------------------------------------------

/// Starts entity resolution for one project, or for every project with something to resolve. The
/// runs continue inside the worker after this returns.
#[tauri::command]
pub async fn start_resolution(
    state: State<'_, AppState>,
    request: ResolutionStartRequest,
) -> Result<ResolutionRunPage, AppError> {
    async {
        let page: ResolutionRunPage = state
            .supervisor
            .request_with(RESOLUTION_START, &request, DISCOVERY_TIMEOUT)
            .await?;
        page.items
            .iter()
            .try_for_each(ResolutionRun::validate)
            .map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("start_resolution"))
    .await
}

/// Pauses, resumes or cancels a resolution run; it stops between batches and stays resumable.
#[tauri::command]
pub async fn control_resolution(
    state: State<'_, AppState>,
    request: DiscoveryControlRequest,
) -> Result<ResolutionRun, AppError> {
    async {
        let run: ResolutionRun = state
            .supervisor
            .request_with(RESOLUTION_CONTROL, &request, DISCOVERY_TIMEOUT)
            .await?;
        run.validate().map_err(AppError::Protocol)?;
        Ok(run)
    }
    .instrument(ipc_span("control_resolution"))
    .await
}

#[tauri::command]
pub async fn list_resolution_runs(
    state: State<'_, AppState>,
    query: ResolutionRunListQuery,
) -> Result<ResolutionRunPage, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let page: ResolutionRunPage = state
            .supervisor
            .request_with(RESOLUTION_RUNS, &query, DATA_TIMEOUT)
            .await?;
        page.validate(&query).map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("list_resolution_runs"))
    .await
}

/// The review queue: pairs of records by score, filtered by status, risk, score and source.
#[tauri::command]
pub async fn list_matches(
    state: State<'_, AppState>,
    query: MatchListQuery,
) -> Result<MatchPage, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let page: MatchPage = state
            .supervisor
            .request_with(MATCHES_LIST, &query, DATA_TIMEOUT)
            .await?;
        page.validate(&query).map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("list_matches"))
    .await
}

/// One pair side by side, with the evidence, reasons and every decision about it.
#[tauri::command]
pub async fn get_match(
    state: State<'_, AppState>,
    query: MatchDetailQuery,
) -> Result<MatchDetail, AppError> {
    async {
        let detail: MatchDetail = state
            .supervisor
            .request_with(MATCHES_GET, &query, DATA_TIMEOUT)
            .await?;
        detail
            .validate(&query.match_id)
            .map_err(AppError::Protocol)?;
        Ok(detail)
    }
    .instrument(ipc_span("get_match"))
    .await
}

/// Merge, keep separate, defer or undo one pair. Recorded with its reason and reversible.
#[tauri::command]
pub async fn act_on_match(
    state: State<'_, AppState>,
    request: MatchActionRequest,
) -> Result<MatchDetail, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let detail: MatchDetail = state
            .supervisor
            .request_with(MATCHES_ACT, &request, RESOLUTION_TIMEOUT)
            .await?;
        detail
            .validate(&request.match_id)
            .map_err(AppError::Protocol)?;
        Ok(detail)
    }
    .instrument(ipc_span("act_on_match"))
    .await
}

/// Makes one observation the value a business shows for a field.
#[tauri::command]
pub async fn choose_canonical_value(
    state: State<'_, AppState>,
    request: CanonicalChoiceRequest,
) -> Result<BusinessDetail, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let detail: BusinessDetail = state
            .supervisor
            .request_with(BUSINESSES_CHOOSE_VALUE, &request, RESOLUTION_TIMEOUT)
            .await?;
        detail
            .validate(&request.business_id)
            .map_err(AppError::Protocol)?;
        Ok(detail)
    }
    .instrument(ipc_span("choose_canonical_value"))
    .await
}

// ---- Latino evidence (phase 07) -----------------------------------------------------------------

/// Evaluates the Latino evidence of one project, or of every project with businesses. The runs
/// continue inside the worker after this returns.
#[tauri::command]
pub async fn start_latino(
    state: State<'_, AppState>,
    request: LatinoStartRequest,
) -> Result<LatinoRunPage, AppError> {
    async {
        let page: LatinoRunPage = state
            .supervisor
            .request_with(LATINO_START, &request, DISCOVERY_TIMEOUT)
            .await?;
        page.items
            .iter()
            .try_for_each(LatinoRun::validate)
            .map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("start_latino"))
    .await
}

/// Pauses, resumes or cancels an evaluation; it stops between batches and stays resumable.
#[tauri::command]
pub async fn control_latino(
    state: State<'_, AppState>,
    request: DiscoveryControlRequest,
) -> Result<LatinoRun, AppError> {
    async {
        let run: LatinoRun = state
            .supervisor
            .request_with(LATINO_CONTROL, &request, DISCOVERY_TIMEOUT)
            .await?;
        run.validate().map_err(AppError::Protocol)?;
        Ok(run)
    }
    .instrument(ipc_span("control_latino"))
    .await
}

#[tauri::command]
pub async fn list_latino_runs(
    state: State<'_, AppState>,
    query: LatinoRunListQuery,
) -> Result<LatinoRunPage, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let page: LatinoRunPage = state
            .supervisor
            .request_with(LATINO_RUNS, &query, DATA_TIMEOUT)
            .await?;
        page.validate(&query).map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("list_latino_runs"))
    .await
}

/// The Latino evidence of one business: what it shows, the calculation, every piece of evidence
/// with its source and the history of assessments and reviews.
#[tauri::command]
pub async fn get_latino(
    state: State<'_, AppState>,
    query: LatinoDetailQuery,
) -> Result<LatinoDetail, AppError> {
    async {
        let detail: LatinoDetail = state
            .supervisor
            .request_with(LATINO_GET, &query, DATA_TIMEOUT)
            .await?;
        detail
            .validate(&query.business_id)
            .map_err(AppError::Protocol)?;
        Ok(detail)
    }
    .instrument(ipc_span("get_latino"))
    .await
}

/// Records a person's assessment with its mandatory reason, or withdraws it.
#[tauri::command]
pub async fn review_latino(
    state: State<'_, AppState>,
    request: LatinoReviewRequest,
) -> Result<LatinoDetail, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let detail: LatinoDetail = state
            .supervisor
            .request_with(LATINO_REVIEW, &request, RESOLUTION_TIMEOUT)
            .await?;
        detail
            .validate(&request.business_id)
            .map_err(AppError::Protocol)?;
        Ok(detail)
    }
    .instrument(ipc_span("review_latino"))
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
