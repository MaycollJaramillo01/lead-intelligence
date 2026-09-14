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
    AiBenchmarkRequest, AiConfigureRequest, AiInferenceDetail, AiInferenceListQuery,
    AiInferencePage, AiInferenceQuery, AiModelChangeRequest, AiReviewRequest, AiRun,
    AiRunListQuery, AiRunPage, AiStartRequest, AiStatus, AiStatusQuery, BulkRequest, BulkResult,
    BusinessDetail, BusinessDetailQuery, BusinessListQuery, BusinessPage, CalibrationQuery,
    CalibrationReport, CampaignListQuery, CampaignPage, CampaignSaveRequest, CampaignSaved,
    CandidateDetail, CandidateDetailQuery, CandidateListQuery, CandidatePage,
    CanonicalChoiceRequest, ChecklistCatalog, ChecklistCatalogQuery, ChecklistChangeRequest,
    ChecklistSaveRequest, ContactActionRequest, ContactDetail, ContactDetailQuery,
    ContactListQuery, ContactPage, ContactRun, ContactRunListQuery, ContactRunPage,
    ContactStartRequest, ContactSuppression, CrawlRun, CrawlRunListQuery, CrawlRunPage,
    CrawlStartRequest, CrawlUrlDetail, CrawlUrlDetailQuery, CrawlUrlListQuery, CrawlUrlPage,
    CriteriaCheck, CriteriaCheckQuery, DatasetListQuery, DatasetPage, DecisionMakerItem,
    DecisionMakerListQuery, DecisionMakerPage, DecisionMakerReviewRequest, DiscoveryControlRequest,
    DiscoveryRun, DiscoveryRunListQuery, DiscoveryRunPage, DiscoveryStartRequest, EvidenceDetail,
    EvidenceQuery, ExplorerCatalog, ExplorerCatalogQuery, ExplorerPage, ExplorerQuery,
    ExportPreview, ExportPreviewRequest, IcpProfileView, IcpSaveRequest, ImportPreview,
    ImportPreviewRequest, LatinoDetail, LatinoDetailQuery, LatinoReviewRequest, LatinoRun,
    LatinoRunListQuery, LatinoRunPage, LatinoStartRequest, LeadActionRequest, LeadCompareQuery,
    LeadComparison, LeadScorePage, LeadScoreQuery, LeadScorecardDetail, MatchActionRequest,
    MatchDetail, MatchDetailQuery, MatchListQuery, MatchPage, ResearchActionRequest,
    ResearchWorkspace, ResearchWorkspaceQuery, ResolutionRun, ResolutionRunListQuery,
    ResolutionRunPage, ResolutionStartRequest, SalesBrief, SalesBriefQuery, SavedViewChangeRequest,
    SavedViewItem, SavedViewList, SavedViewQuery, SavedViewSaveRequest, ScoreCompareQuery,
    ScoreComparison, ScoreRunItem, ScoreRunListQuery, ScoreRunPage, ScorecardQuery, ScoringCatalog,
    ScoringCatalogQuery, ScoringChangeRequest, ScoringModelSaveRequest, ScoringModelView,
    ScoringSimulation, ScoringSimulationRequest, ScoringStartRequest, SourceCatalog,
    SourceCatalogQuery, SuppressionChangeRequest, SuppressionExport, SuppressionExportRequest,
    SuppressionImportRequest, SuppressionImportResult, SuppressionListQuery, SuppressionPage,
    SyntheticSeedResult, TimelinePage, TimelineQuery, WebsiteAuditDetail, WebsiteAuditListQuery,
    WebsiteAuditPage, WebsiteAuditQuery, WebsiteRun, WebsiteRunListQuery, WebsiteRunPage,
    WebsiteStartRequest,
};
use crate::error::AppError;
use crate::protocol::{
    ComponentState, CoreStatus, DiagnosticsReport, DiagnosticsResult, UiErrorReport, WorkerStatus,
};
use crate::worker::{
    AI_BENCHMARK, AI_CONFIGURE, AI_CONTROL, AI_INFERENCE, AI_INFERENCES, AI_MODEL_CHANGE,
    AI_REVIEW, AI_RUNS, AI_START, AI_STATUS, AI_TIMEOUT, BULK_TIMEOUT, BUSINESSES_CHOOSE_VALUE,
    BUSINESSES_GET, BUSINESSES_LIST, CAMPAIGNS_LIST, CAMPAIGNS_SAVE, CAMPAIGNS_VALIDATE,
    CONTACTS_ACT, CONTACTS_CONTROL, CONTACTS_GET, CONTACTS_LIST, CONTACTS_RUNS, CONTACTS_START,
    CONTACTS_TIMEOUT, CRAWL_CONTROL, CRAWL_RUNS, CRAWL_START, CRAWL_URL, CRAWL_URLS, DATA_TIMEOUT,
    DATASETS_LIST, DECISION_MAKERS_LIST, DECISION_MAKERS_REVIEW, DEV_SEED_SYNTHETIC, DIAGNOSTICS,
    DIAGNOSTICS_TIMEOUT, DISCOVERY_CANDIDATE, DISCOVERY_CANDIDATES, DISCOVERY_CONTROL,
    DISCOVERY_RUNS, DISCOVERY_START, DISCOVERY_TIMEOUT, IMPORT_TIMEOUT, LATINO_CONTROL, LATINO_GET,
    LATINO_REVIEW, LATINO_RUNS, LATINO_START, LEADS_BULK, LEADS_CATALOG, LEADS_COMPARE,
    LEADS_EXPLORE, LEADS_EXPORT_PREVIEW, LEADS_TIMEOUT, MATCHES_ACT, MATCHES_GET, MATCHES_LIST,
    RESEARCH_ACT, RESEARCH_BRIEF, RESEARCH_CHANGE_CHECKLIST, RESEARCH_CHECKLISTS,
    RESEARCH_EVIDENCE, RESEARCH_GET, RESEARCH_SAVE_CHECKLIST, RESEARCH_TIMELINE,
    RESOLUTION_CONTROL, RESOLUTION_RUNS, RESOLUTION_START, RESOLUTION_TIMEOUT, SCORING_ACT,
    SCORING_CALIBRATION, SCORING_CATALOG, SCORING_CHANGE, SCORING_COMPARE, SCORING_CONTROL,
    SCORING_LEADS, SCORING_RUNS, SCORING_SAVE_ICP, SCORING_SAVE_MODEL, SCORING_SCORECARD,
    SCORING_SIMULATE, SCORING_START, SCORING_TIMEOUT, SEED_TIMEOUT, SOURCES_LIST,
    SOURCES_PREVIEW_IMPORT, SUPPRESSIONS_CHANGE, SUPPRESSIONS_EXPORT, SUPPRESSIONS_IMPORT,
    SUPPRESSIONS_LIST, VIEWS_CHANGE, VIEWS_LIST, VIEWS_SAVE, WEBSITE_AUDIT, WEBSITE_AUDITS,
    WEBSITE_CONTROL, WEBSITE_RUNS, WEBSITE_START, WorkerSupervisor,
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

// ---- website intelligence (phase 08) ------------------------------------------------------------

/// Audits the crawled sites of one project (or of every project with a finished crawl). The runs
/// continue inside the worker after this returns.
#[tauri::command]
pub async fn start_website_audit(
    state: State<'_, AppState>,
    request: WebsiteStartRequest,
) -> Result<WebsiteRunPage, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let page: WebsiteRunPage = state
            .supervisor
            .request_with(WEBSITE_START, &request, DISCOVERY_TIMEOUT)
            .await?;
        page.items
            .iter()
            .try_for_each(WebsiteRun::validate)
            .map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("start_website_audit"))
    .await
}

/// Pauses, resumes or cancels an audit run; it stops between sites and stays resumable.
#[tauri::command]
pub async fn control_website_audit(
    state: State<'_, AppState>,
    request: DiscoveryControlRequest,
) -> Result<WebsiteRun, AppError> {
    async {
        let run: WebsiteRun = state
            .supervisor
            .request_with(WEBSITE_CONTROL, &request, DISCOVERY_TIMEOUT)
            .await?;
        run.validate().map_err(AppError::Protocol)?;
        Ok(run)
    }
    .instrument(ipc_span("control_website_audit"))
    .await
}

#[tauri::command]
pub async fn list_website_runs(
    state: State<'_, AppState>,
    query: WebsiteRunListQuery,
) -> Result<WebsiteRunPage, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let page: WebsiteRunPage = state
            .supervisor
            .request_with(WEBSITE_RUNS, &query, DATA_TIMEOUT)
            .await?;
        page.validate(&query).map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("list_website_runs"))
    .await
}

/// The latest audit of every crawled site, or the history of one site.
#[tauri::command]
pub async fn list_website_audits(
    state: State<'_, AppState>,
    query: WebsiteAuditListQuery,
) -> Result<WebsiteAuditPage, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let page: WebsiteAuditPage = state
            .supervisor
            .request_with(WEBSITE_AUDITS, &query, DATA_TIMEOUT)
            .await?;
        page.validate(&query).map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("list_website_audits"))
    .await
}

/// One audit: findings with their evidence, subscores, coverage, pages, technologies, conversion
/// elements and what changed since the previous audit of the site.
#[tauri::command]
pub async fn get_website_audit(
    state: State<'_, AppState>,
    query: WebsiteAuditQuery,
) -> Result<WebsiteAuditDetail, AppError> {
    async {
        let detail: WebsiteAuditDetail = state
            .supervisor
            .request_with(WEBSITE_AUDIT, &query, DATA_TIMEOUT)
            .await?;
        detail
            .validate(&query.audit_id)
            .map_err(AppError::Protocol)?;
        Ok(detail)
    }
    .instrument(ipc_span("get_website_audit"))
    .await
}

// ---- contact intelligence (phase 09) ------------------------------------------------------------

/// Checks the contacts of one project, or of every project with businesses. The runs continue
/// inside the worker after this returns; mail domains are looked up only when the request allows.
#[tauri::command]
pub async fn start_contacts(
    state: State<'_, AppState>,
    request: ContactStartRequest,
) -> Result<ContactRunPage, AppError> {
    async {
        let page: ContactRunPage = state
            .supervisor
            .request_with(CONTACTS_START, &request, DISCOVERY_TIMEOUT)
            .await?;
        page.items
            .iter()
            .try_for_each(ContactRun::validate)
            .map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("start_contacts"))
    .await
}

/// Pauses, resumes or cancels a contact run; it stops between batches and stays resumable.
#[tauri::command]
pub async fn control_contacts(
    state: State<'_, AppState>,
    request: DiscoveryControlRequest,
) -> Result<ContactRun, AppError> {
    async {
        let run: ContactRun = state
            .supervisor
            .request_with(CONTACTS_CONTROL, &request, DISCOVERY_TIMEOUT)
            .await?;
        run.validate().map_err(AppError::Protocol)?;
        Ok(run)
    }
    .instrument(ipc_span("control_contacts"))
    .await
}

#[tauri::command]
pub async fn list_contact_runs(
    state: State<'_, AppState>,
    query: ContactRunListQuery,
) -> Result<ContactRunPage, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let page: ContactRunPage = state
            .supervisor
            .request_with(CONTACTS_RUNS, &query, DATA_TIMEOUT)
            .await?;
        page.validate(&query).map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("list_contact_runs"))
    .await
}

/// Channels of canonical businesses with their exact status, filtered by channel, quality, role
/// and freshness; hypotheses and suppressed channels only when asked for.
#[tauri::command]
pub async fn list_contacts(
    state: State<'_, AppState>,
    query: ContactListQuery,
) -> Result<ContactPage, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let page: ContactPage = state
            .supervisor
            .request_with(CONTACTS_LIST, &query, CONTACTS_TIMEOUT)
            .await?;
        page.validate(&query).map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("list_contacts"))
    .await
}

/// The contacts of one business: channels with their sources, hypotheses apart, published people,
/// conflicts, contactability and the review history.
#[tauri::command]
pub async fn get_contacts(
    state: State<'_, AppState>,
    query: ContactDetailQuery,
) -> Result<ContactDetail, AppError> {
    async {
        let detail: ContactDetail = state
            .supervisor
            .request_with(CONTACTS_GET, &query, DATA_TIMEOUT)
            .await?;
        detail
            .validate(&query.business_id)
            .map_err(AppError::Protocol)?;
        Ok(detail)
    }
    .instrument(ipc_span("get_contacts"))
    .await
}

/// Validates, reviews, marks as shown or suppresses one channel; nothing is ever sent.
#[tauri::command]
pub async fn act_on_contact(
    state: State<'_, AppState>,
    request: ContactActionRequest,
) -> Result<ContactDetail, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let detail: ContactDetail = state
            .supervisor
            .request_with(CONTACTS_ACT, &request, RESOLUTION_TIMEOUT)
            .await?;
        detail
            .validate(&request.business_id)
            .map_err(AppError::Protocol)?;
        Ok(detail)
    }
    .instrument(ipc_span("act_on_contact"))
    .await
}

/// Published people, by review status: the review queue asks for the ambiguous ones.
#[tauri::command]
pub async fn list_decision_makers(
    state: State<'_, AppState>,
    query: DecisionMakerListQuery,
) -> Result<DecisionMakerPage, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let page: DecisionMakerPage = state
            .supervisor
            .request_with(DECISION_MAKERS_LIST, &query, DATA_TIMEOUT)
            .await?;
        page.validate(&query).map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("list_decision_makers"))
    .await
}

#[tauri::command]
pub async fn review_decision_maker(
    state: State<'_, AppState>,
    request: DecisionMakerReviewRequest,
) -> Result<DecisionMakerItem, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let item: DecisionMakerItem = state
            .supervisor
            .request_with(DECISION_MAKERS_REVIEW, &request, DATA_TIMEOUT)
            .await?;
        if item.decision_maker.id != request.decision_maker_id {
            return Err(AppError::Protocol(
                "the review answered about another person".into(),
            ));
        }
        item.validate().map_err(AppError::Protocol)?;
        Ok(item)
    }
    .instrument(ipc_span("review_decision_maker"))
    .await
}

#[tauri::command]
pub async fn list_suppressions(
    state: State<'_, AppState>,
    query: SuppressionListQuery,
) -> Result<SuppressionPage, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let page: SuppressionPage = state
            .supervisor
            .request_with(SUPPRESSIONS_LIST, &query, DATA_TIMEOUT)
            .await?;
        page.validate(&query).map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("list_suppressions"))
    .await
}

/// Adds an entry to the do-not-contact list, or lifts one; both with a reason.
#[tauri::command]
pub async fn change_suppression(
    state: State<'_, AppState>,
    request: SuppressionChangeRequest,
) -> Result<ContactSuppression, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let entry: ContactSuppression = state
            .supervisor
            .request_with(SUPPRESSIONS_CHANGE, &request, CONTACTS_TIMEOUT)
            .await?;
        entry.validate().map_err(AppError::Protocol)?;
        let answered = match &request.suppression_id {
            Some(id) => &entry.id == id && entry.lifted_at.is_some(),
            None => request.kind == Some(entry.kind) && entry.lifted_at.is_none(),
        };
        if !answered {
            return Err(AppError::Protocol(
                "the do-not-contact list answered about another change".into(),
            ));
        }
        Ok(entry)
    }
    .instrument(ipc_span("change_suppression"))
    .await
}

#[tauri::command]
pub async fn import_suppressions(
    state: State<'_, AppState>,
    request: SuppressionImportRequest,
) -> Result<SuppressionImportResult, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let result: SuppressionImportResult = state
            .supervisor
            .request_with(SUPPRESSIONS_IMPORT, &request, RESOLUTION_TIMEOUT)
            .await?;
        result.validate().map_err(AppError::Protocol)?;
        Ok(result)
    }
    .instrument(ipc_span("import_suppressions"))
    .await
}

/// The do-not-contact list as CSV; the core refuses a file with a cell a spreadsheet would run.
#[tauri::command]
pub async fn export_suppressions(
    state: State<'_, AppState>,
    request: SuppressionExportRequest,
) -> Result<SuppressionExport, AppError> {
    async {
        let export: SuppressionExport = state
            .supervisor
            .request_with(SUPPRESSIONS_EXPORT, &request, DATA_TIMEOUT)
            .await?;
        export.validate().map_err(AppError::Protocol)?;
        Ok(export)
    }
    .instrument(ipc_span("export_suppressions"))
    .await
}

// ---- local AI (phase 10) ------------------------------------------------------------------------

/// Runtime, GPU, installed models with estimates, settings, task availability, resources, queue,
/// benchmark results and recommendations; LLM Fit only when asked.
#[tauri::command]
pub async fn get_ai_status(
    state: State<'_, AppState>,
    query: AiStatusQuery,
) -> Result<AiStatus, AppError> {
    async {
        let status: AiStatus = state
            .supervisor
            .request_with(AI_STATUS, &query, AI_TIMEOUT)
            .await?;
        status.validate(&query).map_err(AppError::Protocol)?;
        Ok(status)
    }
    .instrument(ipc_span("get_ai_status"))
    .await
}

/// Assigns models to profiles, profiles to tasks and the memory limits; models must be installed.
#[tauri::command]
pub async fn configure_ai(
    state: State<'_, AppState>,
    request: AiConfigureRequest,
) -> Result<AiStatus, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let status: AiStatus = state
            .supervisor
            .request_with(AI_CONFIGURE, &request, AI_TIMEOUT)
            .await?;
        status
            .validate(&AiStatusQuery {
                include_llm_fit: false,
            })
            .map_err(AppError::Protocol)?;
        Ok(status)
    }
    .instrument(ipc_span("configure_ai"))
    .await
}

/// Starts local AI tasks over a project or some businesses; identical requests reuse results.
#[tauri::command]
pub async fn start_ai(
    state: State<'_, AppState>,
    request: AiStartRequest,
) -> Result<AiRunPage, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let page: AiRunPage = state
            .supervisor
            .request_with(AI_START, &request, CONTACTS_TIMEOUT)
            .await?;
        page.items
            .iter()
            .try_for_each(AiRun::validate)
            .map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("start_ai"))
    .await
}

/// Pauses, resumes or cancels an AI run; the inference in flight is cancelled and recorded.
#[tauri::command]
pub async fn control_ai(
    state: State<'_, AppState>,
    request: DiscoveryControlRequest,
) -> Result<AiRun, AppError> {
    async {
        let run: AiRun = state
            .supervisor
            .request_with(AI_CONTROL, &request, CONTACTS_TIMEOUT)
            .await?;
        run.validate().map_err(AppError::Protocol)?;
        Ok(run)
    }
    .instrument(ipc_span("control_ai"))
    .await
}

#[tauri::command]
pub async fn list_ai_runs(
    state: State<'_, AppState>,
    query: AiRunListQuery,
) -> Result<AiRunPage, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let page: AiRunPage = state
            .supervisor
            .request_with(AI_RUNS, &query, DATA_TIMEOUT)
            .await?;
        page.validate(&query).map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("list_ai_runs"))
    .await
}

/// The inference history with its failures, by task, status, business and review.
#[tauri::command]
pub async fn list_ai_inferences(
    state: State<'_, AppState>,
    query: AiInferenceListQuery,
) -> Result<AiInferencePage, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let page: AiInferencePage = state
            .supervisor
            .request_with(AI_INFERENCES, &query, DATA_TIMEOUT)
            .await?;
        page.validate(&query).map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("list_ai_inferences"))
    .await
}

/// One inference: raw and validated answers, the inputs it cites and the review decisions.
#[tauri::command]
pub async fn get_ai_inference(
    state: State<'_, AppState>,
    query: AiInferenceQuery,
) -> Result<AiInferenceDetail, AppError> {
    async {
        let detail: AiInferenceDetail = state
            .supervisor
            .request_with(AI_INFERENCE, &query, DATA_TIMEOUT)
            .await?;
        detail
            .validate(&query.inference_run_id)
            .map_err(AppError::Protocol)?;
        Ok(detail)
    }
    .instrument(ipc_span("get_ai_inference"))
    .await
}

/// A person accepts, rejects or withdraws their review of an answer, with a reason.
#[tauri::command]
pub async fn review_ai_inference(
    state: State<'_, AppState>,
    request: AiReviewRequest,
) -> Result<AiInferenceDetail, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let detail: AiInferenceDetail = state
            .supervisor
            .request_with(AI_REVIEW, &request, CONTACTS_TIMEOUT)
            .await?;
        detail
            .validate(&request.inference_run_id)
            .map_err(AppError::Protocol)?;
        Ok(detail)
    }
    .instrument(ipc_span("review_ai_inference"))
    .await
}

/// Starts or cancels a benchmark of installed models over the synthetic labelled corpus.
#[tauri::command]
pub async fn run_ai_benchmark(
    state: State<'_, AppState>,
    request: AiBenchmarkRequest,
) -> Result<AiStatus, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let status: AiStatus = state
            .supervisor
            .request_with(AI_BENCHMARK, &request, AI_TIMEOUT)
            .await?;
        status
            .validate(&AiStatusQuery {
                include_llm_fit: false,
            })
            .map_err(AppError::Protocol)?;
        Ok(status)
    }
    .instrument(ipc_span("run_ai_benchmark"))
    .await
}

/// Downloads or deletes a model, only with the exact model name typed as confirmation.
#[tauri::command]
pub async fn change_ai_model(
    state: State<'_, AppState>,
    request: AiModelChangeRequest,
) -> Result<AiStatus, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let status: AiStatus = state
            .supervisor
            .request_with(AI_MODEL_CHANGE, &request, AI_TIMEOUT)
            .await?;
        status
            .validate(&AiStatusQuery {
                include_llm_fit: false,
            })
            .map_err(AppError::Protocol)?;
        Ok(status)
    }
    .instrument(ipc_span("change_ai_model"))
    .await
}

// ---- ICP and lead scoring (phase 11) ------------------------------------------------------------

/// ICP profiles and scoring models with their versions, the engine's features and blank templates.
#[tauri::command]
pub async fn get_scoring_catalog(
    state: State<'_, AppState>,
    query: ScoringCatalogQuery,
) -> Result<ScoringCatalog, AppError> {
    async {
        let catalog: ScoringCatalog = state
            .supervisor
            .request_with(SCORING_CATALOG, &query, DATA_TIMEOUT)
            .await?;
        catalog.validate().map_err(AppError::Protocol)?;
        Ok(catalog)
    }
    .instrument(ipc_span("get_scoring_catalog"))
    .await
}

/// Creates an ICP profile, or a new version of one; a draft with problems is saved, never run.
#[tauri::command]
pub async fn save_icp_profile(
    state: State<'_, AppState>,
    request: IcpSaveRequest,
) -> Result<IcpProfileView, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let view: IcpProfileView = state
            .supervisor
            .request_with(SCORING_SAVE_ICP, &request, DATA_TIMEOUT)
            .await?;
        view.validate().map_err(AppError::Protocol)?;
        Ok(view)
    }
    .instrument(ipc_span("save_icp_profile"))
    .await
}

/// Creates a scoring model, or a new version of one.
#[tauri::command]
pub async fn save_scoring_model(
    state: State<'_, AppState>,
    request: ScoringModelSaveRequest,
) -> Result<ScoringModelView, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let view: ScoringModelView = state
            .supervisor
            .request_with(SCORING_SAVE_MODEL, &request, DATA_TIMEOUT)
            .await?;
        view.validate().map_err(AppError::Protocol)?;
        Ok(view)
    }
    .instrument(ipc_span("save_scoring_model"))
    .await
}

/// Activates a version, or archives or restores a profile or model, with a reason.
#[tauri::command]
pub async fn change_scoring(
    state: State<'_, AppState>,
    request: ScoringChangeRequest,
) -> Result<ScoringCatalog, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let catalog: ScoringCatalog = state
            .supervisor
            .request_with(SCORING_CHANGE, &request, DATA_TIMEOUT)
            .await?;
        catalog.validate().map_err(AppError::Protocol)?;
        Ok(catalog)
    }
    .instrument(ipc_span("change_scoring"))
    .await
}

/// Scores drafts in memory over a project or a run's stored facts; nothing is written.
#[tauri::command]
pub async fn simulate_scoring(
    state: State<'_, AppState>,
    request: ScoringSimulationRequest,
) -> Result<ScoringSimulation, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let simulation: ScoringSimulation = state
            .supervisor
            .request_with(SCORING_SIMULATE, &request, SCORING_TIMEOUT)
            .await?;
        simulation.validate(&request).map_err(AppError::Protocol)?;
        Ok(simulation)
    }
    .instrument(ipc_span("simulate_scoring"))
    .await
}

/// Starts a score run of one ICP version and one scoring version; it continues in the worker.
#[tauri::command]
pub async fn start_scoring(
    state: State<'_, AppState>,
    request: ScoringStartRequest,
) -> Result<ScoreRunPage, AppError> {
    async {
        let page: ScoreRunPage = state
            .supervisor
            .request_with(SCORING_START, &request, CONTACTS_TIMEOUT)
            .await?;
        page.items
            .iter()
            .try_for_each(ScoreRunItem::validate)
            .map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("start_scoring"))
    .await
}

/// Pauses, resumes or cancels a score run; it stops between batches and stays resumable.
#[tauri::command]
pub async fn control_scoring(
    state: State<'_, AppState>,
    request: DiscoveryControlRequest,
) -> Result<ScoreRunItem, AppError> {
    async {
        let run: ScoreRunItem = state
            .supervisor
            .request_with(SCORING_CONTROL, &request, CONTACTS_TIMEOUT)
            .await?;
        run.validate().map_err(AppError::Protocol)?;
        Ok(run)
    }
    .instrument(ipc_span("control_scoring"))
    .await
}

#[tauri::command]
pub async fn list_score_runs(
    state: State<'_, AppState>,
    query: ScoreRunListQuery,
) -> Result<ScoreRunPage, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let page: ScoreRunPage = state
            .supervisor
            .request_with(SCORING_RUNS, &query, DATA_TIMEOUT)
            .await?;
        page.validate(&query).map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("list_score_runs"))
    .await
}

/// The ranked leads of a run (the newest finished one by default), with their filters.
#[tauri::command]
pub async fn list_lead_scores(
    state: State<'_, AppState>,
    query: LeadScoreQuery,
) -> Result<LeadScorePage, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let page: LeadScorePage = state
            .supervisor
            .request_with(SCORING_LEADS, &query, CONTACTS_TIMEOUT)
            .await?;
        page.validate(&query).map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("list_lead_scores"))
    .await
}

/// One lead's scorecard with its history, outcomes, reviews and checked model wording.
#[tauri::command]
pub async fn get_lead_scorecard(
    state: State<'_, AppState>,
    query: ScorecardQuery,
) -> Result<LeadScorecardDetail, AppError> {
    async {
        let detail: LeadScorecardDetail = state
            .supervisor
            .request_with(SCORING_SCORECARD, &query, DATA_TIMEOUT)
            .await?;
        detail.validate(&query).map_err(AppError::Protocol)?;
        Ok(detail)
    }
    .instrument(ipc_span("get_lead_scorecard"))
    .await
}

/// Two runs side by side: who entered or left the top K and how ranks moved.
#[tauri::command]
pub async fn compare_score_runs(
    state: State<'_, AppState>,
    query: ScoreCompareQuery,
) -> Result<ScoreComparison, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let comparison: ScoreComparison = state
            .supervisor
            .request_with(SCORING_COMPARE, &query, CONTACTS_TIMEOUT)
            .await?;
        comparison.validate(&query).map_err(AppError::Protocol)?;
        Ok(comparison)
    }
    .instrument(ipc_span("compare_score_runs"))
    .await
}

/// Offline metrics of a run against the outcomes recorded after its facts were read.
#[tauri::command]
pub async fn get_calibration(
    state: State<'_, AppState>,
    query: CalibrationQuery,
) -> Result<CalibrationReport, AppError> {
    async {
        let report: CalibrationReport = state
            .supervisor
            .request_with(SCORING_CALIBRATION, &query, CONTACTS_TIMEOUT)
            .await?;
        report.validate(&query).map_err(AppError::Protocol)?;
        Ok(report)
    }
    .instrument(ipc_span("get_calibration"))
    .await
}

/// A person's review of a score, or an outcome of working the lead, with a reason.
#[tauri::command]
pub async fn act_on_lead(
    state: State<'_, AppState>,
    request: LeadActionRequest,
) -> Result<LeadScorecardDetail, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let detail: LeadScorecardDetail = state
            .supervisor
            .request_with(SCORING_ACT, &request, DATA_TIMEOUT)
            .await?;
        detail
            .validate(&ScorecardQuery {
                business_id: request.business_id.clone(),
                score_run_id: Some(request.score_run_id.clone()),
            })
            .map_err(AppError::Protocol)?;
        Ok(detail)
    }
    .instrument(ipc_span("act_on_lead"))
    .await
}

/// What the Lead Explorer can filter, sort and show, with its limits and default view.
#[tauri::command]
pub async fn get_explorer_catalog(
    state: State<'_, AppState>,
    query: ExplorerCatalogQuery,
) -> Result<ExplorerCatalog, AppError> {
    async {
        let catalog: ExplorerCatalog = state
            .supervisor
            .request_with(LEADS_CATALOG, &query, DATA_TIMEOUT)
            .await?;
        catalog.validate().map_err(AppError::Protocol)?;
        Ok(catalog)
    }
    .instrument(ipc_span("get_explorer_catalog"))
    .await
}

/// One window of a run's leads: filters, a quick search, sort keys and an offset.
#[tauri::command]
pub async fn explore_leads(
    state: State<'_, AppState>,
    query: ExplorerQuery,
) -> Result<ExplorerPage, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let page: ExplorerPage = state
            .supervisor
            .request_with(LEADS_EXPLORE, &query, LEADS_TIMEOUT)
            .await?;
        page.validate(&query).map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("explore_leads"))
    .await
}

/// What an export of a query would hold, with a sample; nothing is written.
#[tauri::command]
pub async fn preview_lead_export(
    state: State<'_, AppState>,
    request: ExportPreviewRequest,
) -> Result<ExportPreview, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let preview: ExportPreview = state
            .supervisor
            .request_with(LEADS_EXPORT_PREVIEW, &request, LEADS_TIMEOUT)
            .await?;
        preview.validate(&request).map_err(AppError::Protocol)?;
        Ok(preview)
    }
    .instrument(ipc_span("preview_lead_export"))
    .await
}

/// A safe action over many leads, previewed first and applied with a reason, lead by lead.
#[tauri::command]
pub async fn bulk_leads(
    state: State<'_, AppState>,
    request: BulkRequest,
) -> Result<BulkResult, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let result: BulkResult = state
            .supervisor
            .request_with(LEADS_BULK, &request, BULK_TIMEOUT)
            .await?;
        result.validate(&request).map_err(AppError::Protocol)?;
        Ok(result)
    }
    .instrument(ipc_span("bulk_leads"))
    .await
}

/// Two to four leads side by side, each with its scorecard.
#[tauri::command]
pub async fn compare_leads(
    state: State<'_, AppState>,
    query: LeadCompareQuery,
) -> Result<LeadComparison, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let comparison: LeadComparison = state
            .supervisor
            .request_with(LEADS_COMPARE, &query, CONTACTS_TIMEOUT)
            .await?;
        comparison.validate(&query).map_err(AppError::Protocol)?;
        Ok(comparison)
    }
    .instrument(ipc_span("compare_leads"))
    .await
}

#[tauri::command]
pub async fn list_saved_views(
    state: State<'_, AppState>,
    query: SavedViewQuery,
) -> Result<SavedViewList, AppError> {
    async {
        let list: SavedViewList = state
            .supervisor
            .request_with(VIEWS_LIST, &query, DATA_TIMEOUT)
            .await?;
        list.validate(&query).map_err(AppError::Protocol)?;
        Ok(list)
    }
    .instrument(ipc_span("list_saved_views"))
    .await
}

/// Saves a view, or a new version of it when its definition changed.
#[tauri::command]
pub async fn save_saved_view(
    state: State<'_, AppState>,
    request: SavedViewSaveRequest,
) -> Result<SavedViewItem, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let item: SavedViewItem = state
            .supervisor
            .request_with(VIEWS_SAVE, &request, DATA_TIMEOUT)
            .await?;
        item.validate().map_err(AppError::Protocol)?;
        Ok(item)
    }
    .instrument(ipc_span("save_saved_view"))
    .await
}

/// Archives or restores a view, with a reason.
#[tauri::command]
pub async fn change_saved_view(
    state: State<'_, AppState>,
    request: SavedViewChangeRequest,
) -> Result<SavedViewItem, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let item: SavedViewItem = state
            .supervisor
            .request_with(VIEWS_CHANGE, &request, DATA_TIMEOUT)
            .await?;
        item.validate().map_err(AppError::Protocol)?;
        Ok(item)
    }
    .instrument(ipc_span("change_saved_view"))
    .await
}

/// A lead's research: criteria, checklist, tasks, pins, notes, corrections and decisions.
#[tauri::command]
pub async fn get_research_workspace(
    state: State<'_, AppState>,
    query: ResearchWorkspaceQuery,
) -> Result<ResearchWorkspace, AppError> {
    async {
        let workspace: ResearchWorkspace = state
            .supervisor
            .request_with(RESEARCH_GET, &query, CONTACTS_TIMEOUT)
            .await?;
        workspace.validate().map_err(AppError::Protocol)?;
        Ok(workspace)
    }
    .instrument(ipc_span("get_research_workspace"))
    .await
}

/// One audited change to a lead's research; a refresh starts the engines' jobs.
#[tauri::command]
pub async fn act_on_research(
    state: State<'_, AppState>,
    request: ResearchActionRequest,
) -> Result<ResearchWorkspace, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let workspace: ResearchWorkspace = state
            .supervisor
            .request_with(RESEARCH_ACT, &request, LEADS_TIMEOUT)
            .await?;
        workspace.validate().map_err(AppError::Protocol)?;
        Ok(workspace)
    }
    .instrument(ipc_span("act_on_research"))
    .await
}

/// What happened to a lead, newest first.
#[tauri::command]
pub async fn get_lead_timeline(
    state: State<'_, AppState>,
    query: TimelineQuery,
) -> Result<TimelinePage, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let page: TimelinePage = state
            .supervisor
            .request_with(RESEARCH_TIMELINE, &query, CONTACTS_TIMEOUT)
            .await?;
        page.validate(&query).map_err(AppError::Protocol)?;
        Ok(page)
    }
    .instrument(ipc_span("get_lead_timeline"))
    .await
}

/// One stored row behind a claim, within the lead it belongs to.
#[tauri::command]
pub async fn get_evidence_detail(
    state: State<'_, AppState>,
    query: EvidenceQuery,
) -> Result<EvidenceDetail, AppError> {
    async {
        query.validate().map_err(AppError::InvalidInput)?;
        let detail: EvidenceDetail = state
            .supervisor
            .request_with(RESEARCH_EVIDENCE, &query, DATA_TIMEOUT)
            .await?;
        detail.validate(&query).map_err(AppError::Protocol)?;
        Ok(detail)
    }
    .instrument(ipc_span("get_evidence_detail"))
    .await
}

#[tauri::command]
pub async fn list_research_checklists(
    state: State<'_, AppState>,
    query: ChecklistCatalogQuery,
) -> Result<ChecklistCatalog, AppError> {
    async {
        let catalog: ChecklistCatalog = state
            .supervisor
            .request_with(RESEARCH_CHECKLISTS, &query, DATA_TIMEOUT)
            .await?;
        catalog.validate(&query).map_err(AppError::Protocol)?;
        Ok(catalog)
    }
    .instrument(ipc_span("list_research_checklists"))
    .await
}

/// Saves a checklist, or a new version of it activated later with a reason.
#[tauri::command]
pub async fn save_research_checklist(
    state: State<'_, AppState>,
    request: ChecklistSaveRequest,
) -> Result<ChecklistCatalog, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let catalog: ChecklistCatalog = state
            .supervisor
            .request_with(RESEARCH_SAVE_CHECKLIST, &request, CONTACTS_TIMEOUT)
            .await?;
        catalog
            .validate(&ChecklistCatalogQuery {
                include_archived: true,
            })
            .map_err(AppError::Protocol)?;
        Ok(catalog)
    }
    .instrument(ipc_span("save_research_checklist"))
    .await
}

/// Activates a checklist version, or archives or restores a checklist, with a reason.
#[tauri::command]
pub async fn change_research_checklist(
    state: State<'_, AppState>,
    request: ChecklistChangeRequest,
) -> Result<ChecklistCatalog, AppError> {
    async {
        request.validate().map_err(AppError::InvalidInput)?;
        let catalog: ChecklistCatalog = state
            .supervisor
            .request_with(RESEARCH_CHANGE_CHECKLIST, &request, CONTACTS_TIMEOUT)
            .await?;
        catalog
            .validate(&ChecklistCatalogQuery {
                include_archived: true,
            })
            .map_err(AppError::Protocol)?;
        Ok(catalog)
    }
    .instrument(ipc_span("change_research_checklist"))
    .await
}

/// A lead's sales brief: claims restated from stored rows, and a model's checked wording.
#[tauri::command]
pub async fn get_sales_brief(
    state: State<'_, AppState>,
    query: SalesBriefQuery,
) -> Result<SalesBrief, AppError> {
    async {
        let brief: SalesBrief = state
            .supervisor
            .request_with(RESEARCH_BRIEF, &query, CONTACTS_TIMEOUT)
            .await?;
        brief.validate(&query).map_err(AppError::Protocol)?;
        Ok(brief)
    }
    .instrument(ipc_span("get_sales_brief"))
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
