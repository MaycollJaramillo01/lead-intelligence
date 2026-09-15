import { invoke, isTauri, type InvokeArgs } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import {
  WORKER_STATUS_EVENT,
  type AiBenchmarkRequest,
  type AiConfigureRequest,
  type AiInferenceDetail,
  type AiInferenceListQuery,
  type AiInferencePage,
  type AiInferenceQuery,
  type AiModelChangeRequest,
  type AiReviewRequest,
  type AiRun,
  type AiRunListQuery,
  type AiRunPage,
  type AiStartRequest,
  type AiStatus,
  type AiStatusQuery,
  type BusinessDetail,
  type BusinessDetailQuery,
  type BusinessListQuery,
  type BusinessPage,
  type CampaignListQuery,
  type CampaignPage,
  type CampaignSaveRequest,
  type CampaignSaved,
  type CandidateDetail,
  type CandidateDetailQuery,
  type CandidateListQuery,
  type CandidatePage,
  type CanonicalChoiceRequest,
  type CommandError,
  type ContactActionRequest,
  type ContactDetail,
  type ContactDetailQuery,
  type ContactListQuery,
  type ContactPage,
  type ContactRun,
  type ContactRunListQuery,
  type ContactRunPage,
  type ContactStartRequest,
  type ContactSuppression,
  type CrawlRun,
  type CrawlRunListQuery,
  type CrawlRunPage,
  type CrawlStartRequest,
  type CrawlUrlDetail,
  type CrawlUrlDetailQuery,
  type CrawlUrlListQuery,
  type CrawlUrlPage,
  type CriteriaCheck,
  type CriteriaCheckQuery,
  type DatasetListQuery,
  type DatasetPage,
  type DecisionMakerItem,
  type DecisionMakerListQuery,
  type DecisionMakerPage,
  type DecisionMakerReviewRequest,
  type DiagnosticsReport,
  type DiscoveryControlRequest,
  type DiscoveryRun,
  type DiscoveryRunListQuery,
  type DiscoveryRunPage,
  type DiscoveryStartRequest,
  type ImportPreview,
  type ImportPreviewRequest,
  type LatinoDetail,
  type LatinoDetailQuery,
  type LatinoReviewRequest,
  type LatinoRun,
  type LatinoRunListQuery,
  type LatinoRunPage,
  type LatinoStartRequest,
  type MatchActionRequest,
  type MatchDetail,
  type MatchDetailQuery,
  type MatchListQuery,
  type MatchPage,
  type ResolutionRun,
  type ResolutionRunListQuery,
  type ResolutionRunPage,
  type ResolutionStartRequest,
  type CalibrationQuery,
  type CalibrationReport,
  type IcpProfileView,
  type IcpSaveRequest,
  type LeadActionRequest,
  type LeadScorecardDetail,
  type LeadScorePage,
  type LeadScoreQuery,
  type BulkRequest,
  type BulkResult,
  type ChecklistCatalog,
  type ChecklistCatalogQuery,
  type ChecklistChangeRequest,
  type ChecklistSaveRequest,
  type EvidenceDetail,
  type EvidenceQuery,
  type ExplorerCatalog,
  type ExplorerCatalogQuery,
  type ExplorerPage,
  type ExplorerQuery,
  type ExportPreview,
  type ExportPreviewRequest,
  type LeadCompareQuery,
  type LeadComparison,
  type ResearchActionRequest,
  type ResearchWorkspace,
  type ResearchWorkspaceQuery,
  type SalesBrief,
  type SalesBriefQuery,
  type SavedViewChangeRequest,
  type SavedViewItem,
  type SavedViewList,
  type SavedViewQuery,
  type SavedViewSaveRequest,
  type TimelinePage,
  type TimelineQuery,
  type ScorecardQuery,
  type ScoreCompareQuery,
  type ScoreComparison,
  type ScoreRunItem,
  type ScoreRunListQuery,
  type ScoreRunPage,
  type ScoringCatalog,
  type ScoringCatalogQuery,
  type ScoringChangeRequest,
  type ScoringModelSaveRequest,
  type ScoringModelView,
  type ScoringSimulation,
  type ScoringSimulationRequest,
  type ScoringStartRequest,
  type SourceCatalog,
  type SourceCatalogQuery,
  type SuppressionChangeRequest,
  type SuppressionExport,
  type SuppressionExportRequest,
  type SuppressionImportRequest,
  type SuppressionImportResult,
  type SuppressionListQuery,
  type SuppressionPage,
  type SyntheticSeedResult,
  type UiErrorReport,
  type WebsiteAuditDetail,
  type WebsiteAuditListQuery,
  type WebsiteAuditPage,
  type WebsiteAuditQuery,
  type WebsiteRun,
  type WebsiteRunListQuery,
  type WebsiteRunPage,
  type WebsiteStartRequest,
  type WorkerStatus,
  type DatasetCleanupRequest,
  type DatasetCleanupResult,
  type DatasetDiff,
  type DatasetDiffQuery,
  type DatasetSnapshotDetail,
  type DatasetSnapshotListQuery,
  type DatasetSnapshotPage,
  type DatasetSnapshotQuery,
  type DatasetSnapshotRequest,
  type DeadLetterRequest,
  type EventCleanupRequest,
  type EventCleanupResult,
  type ExportCancelRequest,
  type ExportCatalog,
  type ExportCatalogQuery,
  type ExportDestination,
  type ExportDraft,
  type ExportFormat,
  type ExportItem,
  type ExportListQuery,
  type ExportPage,
  type ExportPresetSaveRequest,
  type ExportPreviewResult,
  type JobEventPage,
  type JobEventQuery,
  type PipelineListQuery,
  type PipelinePage,
  type PipelineRun,
  type PipelineStartRequest,
} from "@lead/shared-types";

export type IpcErrorCode = CommandError["code"] | "desktop_unavailable" | "ipc_failure";

export class IpcError extends Error {
  readonly code: IpcErrorCode;

  constructor(code: IpcErrorCode, message: string) {
    super(message);
    this.name = "IpcError";
    this.code = code;
  }
}

function isCommandError(value: unknown): value is CommandError {
  if (typeof value !== "object" || value === null) return false;
  const { code, message } = value as Record<string, unknown>;
  return typeof code === "string" && typeof message === "string";
}

/** Normalises anything a command can reject with into an IpcError the UI can display. */
export function toIpcError(cause: unknown, command = "request"): IpcError {
  if (cause instanceof IpcError) return cause;
  if (isCommandError(cause)) return new IpcError(cause.code, cause.message);
  if (!isTauri()) {
    return new IpcError(
      "desktop_unavailable",
      "The desktop core is not reachable. Run the app with `pnpm dev` instead of opening the page in a browser.",
    );
  }
  const detail = cause instanceof Error ? cause.message : String(cause);
  return new IpcError("ipc_failure", `${command} failed: ${detail}`);
}

async function call<T>(command: string, args?: InvokeArgs): Promise<T> {
  if (!isTauri()) throw toIpcError(null, command);
  try {
    return await invoke<T>(command, args);
  } catch (cause) {
    throw toIpcError(cause, command);
  }
}

/** Typed adapter over the Rust commands. Components never call `invoke` directly. */
export const desktop = {
  getDiagnostics: () => call<DiagnosticsReport>("get_diagnostics"),
  restartWorker: () => call<WorkerStatus>("restart_worker"),
  reportUiError: (report: UiErrorReport) => call<null>("report_ui_error", { report }),
  listBusinesses: (query: BusinessListQuery) => call<BusinessPage>("list_businesses", { query }),
  getBusiness: (query: BusinessDetailQuery) => call<BusinessDetail>("get_business", { query }),
  listDatasets: (query: DatasetListQuery) => call<DatasetPage>("list_datasets", { query }),
  listSources: (query: SourceCatalogQuery) => call<SourceCatalog>("list_sources", { query }),
  previewImport: (request: ImportPreviewRequest) =>
    call<ImportPreview>("preview_import", { request }),
  listCampaigns: (query: CampaignListQuery) => call<CampaignPage>("list_campaigns", { query }),
  saveCampaign: (request: CampaignSaveRequest) => call<CampaignSaved>("save_campaign", { request }),
  validateCriteria: (query: CriteriaCheckQuery) =>
    call<CriteriaCheck>("validate_criteria", { query }),
  startDiscovery: (request: DiscoveryStartRequest) =>
    call<DiscoveryRun>("start_discovery", { request }),
  controlDiscovery: (request: DiscoveryControlRequest) =>
    call<DiscoveryRun>("control_discovery", { request }),
  listDiscoveryRuns: (query: DiscoveryRunListQuery) =>
    call<DiscoveryRunPage>("list_discovery_runs", { query }),
  listCandidates: (query: CandidateListQuery) => call<CandidatePage>("list_candidates", { query }),
  getCandidate: (query: CandidateDetailQuery) => call<CandidateDetail>("get_candidate", { query }),
  startCrawl: (request: CrawlStartRequest) => call<CrawlRun>("start_crawl", { request }),
  controlCrawl: (request: DiscoveryControlRequest) => call<CrawlRun>("control_crawl", { request }),
  listCrawlRuns: (query: CrawlRunListQuery) => call<CrawlRunPage>("list_crawl_runs", { query }),
  listCrawlUrls: (query: CrawlUrlListQuery) => call<CrawlUrlPage>("list_crawl_urls", { query }),
  getCrawlUrl: (query: CrawlUrlDetailQuery) => call<CrawlUrlDetail>("get_crawl_url", { query }),
  startResolution: (request: ResolutionStartRequest) =>
    call<ResolutionRunPage>("start_resolution", { request }),
  controlResolution: (request: DiscoveryControlRequest) =>
    call<ResolutionRun>("control_resolution", { request }),
  listResolutionRuns: (query: ResolutionRunListQuery) =>
    call<ResolutionRunPage>("list_resolution_runs", { query }),
  listMatches: (query: MatchListQuery) => call<MatchPage>("list_matches", { query }),
  getMatch: (query: MatchDetailQuery) => call<MatchDetail>("get_match", { query }),
  actOnMatch: (request: MatchActionRequest) => call<MatchDetail>("act_on_match", { request }),
  chooseCanonicalValue: (request: CanonicalChoiceRequest) =>
    call<BusinessDetail>("choose_canonical_value", { request }),
  startLatino: (request: LatinoStartRequest) => call<LatinoRunPage>("start_latino", { request }),
  controlLatino: (request: DiscoveryControlRequest) =>
    call<LatinoRun>("control_latino", { request }),
  listLatinoRuns: (query: LatinoRunListQuery) => call<LatinoRunPage>("list_latino_runs", { query }),
  getLatino: (query: LatinoDetailQuery) => call<LatinoDetail>("get_latino", { query }),
  reviewLatino: (request: LatinoReviewRequest) => call<LatinoDetail>("review_latino", { request }),
  startWebsiteAudit: (request: WebsiteStartRequest) =>
    call<WebsiteRunPage>("start_website_audit", { request }),
  controlWebsiteAudit: (request: DiscoveryControlRequest) =>
    call<WebsiteRun>("control_website_audit", { request }),
  listWebsiteRuns: (query: WebsiteRunListQuery) =>
    call<WebsiteRunPage>("list_website_runs", { query }),
  listWebsiteAudits: (query: WebsiteAuditListQuery) =>
    call<WebsiteAuditPage>("list_website_audits", { query }),
  getWebsiteAudit: (query: WebsiteAuditQuery) =>
    call<WebsiteAuditDetail>("get_website_audit", { query }),
  startContacts: (request: ContactStartRequest) =>
    call<ContactRunPage>("start_contacts", { request }),
  controlContacts: (request: DiscoveryControlRequest) =>
    call<ContactRun>("control_contacts", { request }),
  listContactRuns: (query: ContactRunListQuery) =>
    call<ContactRunPage>("list_contact_runs", { query }),
  listContacts: (query: ContactListQuery) => call<ContactPage>("list_contacts", { query }),
  getContacts: (query: ContactDetailQuery) => call<ContactDetail>("get_contacts", { query }),
  actOnContact: (request: ContactActionRequest) =>
    call<ContactDetail>("act_on_contact", { request }),
  listDecisionMakers: (query: DecisionMakerListQuery) =>
    call<DecisionMakerPage>("list_decision_makers", { query }),
  reviewDecisionMaker: (request: DecisionMakerReviewRequest) =>
    call<DecisionMakerItem>("review_decision_maker", { request }),
  listSuppressions: (query: SuppressionListQuery) =>
    call<SuppressionPage>("list_suppressions", { query }),
  changeSuppression: (request: SuppressionChangeRequest) =>
    call<ContactSuppression>("change_suppression", { request }),
  importSuppressions: (request: SuppressionImportRequest) =>
    call<SuppressionImportResult>("import_suppressions", { request }),
  exportSuppressions: (request: SuppressionExportRequest) =>
    call<SuppressionExport>("export_suppressions", { request }),
  getAiStatus: (query: AiStatusQuery) => call<AiStatus>("get_ai_status", { query }),
  configureAi: (request: AiConfigureRequest) => call<AiStatus>("configure_ai", { request }),
  startAi: (request: AiStartRequest) => call<AiRunPage>("start_ai", { request }),
  controlAi: (request: DiscoveryControlRequest) => call<AiRun>("control_ai", { request }),
  listAiRuns: (query: AiRunListQuery) => call<AiRunPage>("list_ai_runs", { query }),
  listAiInferences: (query: AiInferenceListQuery) =>
    call<AiInferencePage>("list_ai_inferences", { query }),
  getAiInference: (query: AiInferenceQuery) =>
    call<AiInferenceDetail>("get_ai_inference", { query }),
  reviewAiInference: (request: AiReviewRequest) =>
    call<AiInferenceDetail>("review_ai_inference", { request }),
  runAiBenchmark: (request: AiBenchmarkRequest) => call<AiStatus>("run_ai_benchmark", { request }),
  changeAiModel: (request: AiModelChangeRequest) => call<AiStatus>("change_ai_model", { request }),
  getScoringCatalog: (query: ScoringCatalogQuery) =>
    call<ScoringCatalog>("get_scoring_catalog", { query }),
  saveIcpProfile: (request: IcpSaveRequest) =>
    call<IcpProfileView>("save_icp_profile", { request }),
  saveScoringModel: (request: ScoringModelSaveRequest) =>
    call<ScoringModelView>("save_scoring_model", { request }),
  changeScoring: (request: ScoringChangeRequest) =>
    call<ScoringCatalog>("change_scoring", { request }),
  simulateScoring: (request: ScoringSimulationRequest) =>
    call<ScoringSimulation>("simulate_scoring", { request }),
  startScoring: (request: ScoringStartRequest) => call<ScoreRunPage>("start_scoring", { request }),
  controlScoring: (request: DiscoveryControlRequest) =>
    call<ScoreRunItem>("control_scoring", { request }),
  listScoreRuns: (query: ScoreRunListQuery) => call<ScoreRunPage>("list_score_runs", { query }),
  listLeadScores: (query: LeadScoreQuery) => call<LeadScorePage>("list_lead_scores", { query }),
  getLeadScorecard: (query: ScorecardQuery) =>
    call<LeadScorecardDetail>("get_lead_scorecard", { query }),
  compareScoreRuns: (query: ScoreCompareQuery) =>
    call<ScoreComparison>("compare_score_runs", { query }),
  getCalibration: (query: CalibrationQuery) =>
    call<CalibrationReport>("get_calibration", { query }),
  actOnLead: (request: LeadActionRequest) => call<LeadScorecardDetail>("act_on_lead", { request }),
  getExplorerCatalog: (query: ExplorerCatalogQuery) =>
    call<ExplorerCatalog>("get_explorer_catalog", { query }),
  exploreLeads: (query: ExplorerQuery) => call<ExplorerPage>("explore_leads", { query }),
  previewLeadExport: (request: ExportPreviewRequest) =>
    call<ExportPreview>("preview_lead_export", { request }),
  bulkLeads: (request: BulkRequest) => call<BulkResult>("bulk_leads", { request }),
  compareLeads: (query: LeadCompareQuery) => call<LeadComparison>("compare_leads", { query }),
  listSavedViews: (query: SavedViewQuery) => call<SavedViewList>("list_saved_views", { query }),
  saveSavedView: (request: SavedViewSaveRequest) =>
    call<SavedViewItem>("save_saved_view", { request }),
  changeSavedView: (request: SavedViewChangeRequest) =>
    call<SavedViewItem>("change_saved_view", { request }),
  getResearchWorkspace: (query: ResearchWorkspaceQuery) =>
    call<ResearchWorkspace>("get_research_workspace", { query }),
  actOnResearch: (request: ResearchActionRequest) =>
    call<ResearchWorkspace>("act_on_research", { request }),
  getLeadTimeline: (query: TimelineQuery) => call<TimelinePage>("get_lead_timeline", { query }),
  getEvidenceDetail: (query: EvidenceQuery) =>
    call<EvidenceDetail>("get_evidence_detail", { query }),
  listResearchChecklists: (query: ChecklistCatalogQuery) =>
    call<ChecklistCatalog>("list_research_checklists", { query }),
  saveResearchChecklist: (request: ChecklistSaveRequest) =>
    call<ChecklistCatalog>("save_research_checklist", { request }),
  changeResearchChecklist: (request: ChecklistChangeRequest) =>
    call<ChecklistCatalog>("change_research_checklist", { request }),
  getSalesBrief: (query: SalesBriefQuery) => call<SalesBrief>("get_sales_brief", { query }),
  startPipeline: (request: PipelineStartRequest) =>
    call<PipelineRun>("start_pipeline", { request }),
  controlPipeline: (request: DiscoveryControlRequest) =>
    call<PipelineRun>("control_pipeline", { request }),
  resolveDeadLetter: (request: DeadLetterRequest) =>
    call<PipelineRun>("resolve_dead_letter", { request }),
  listPipelines: (query: PipelineListQuery) => call<PipelinePage>("list_pipelines", { query }),
  listJobEvents: (query: JobEventQuery) => call<JobEventPage>("list_job_events", { query }),
  cleanupJobEvents: (request: EventCleanupRequest) =>
    call<EventCleanupResult>("cleanup_job_events", { request }),
  createDatasetSnapshot: (request: DatasetSnapshotRequest) =>
    call<DatasetSnapshotDetail>("create_dataset_snapshot", { request }),
  listDatasetSnapshots: (query: DatasetSnapshotListQuery) =>
    call<DatasetSnapshotPage>("list_dataset_snapshots", { query }),
  getDatasetSnapshot: (query: DatasetSnapshotQuery) =>
    call<DatasetSnapshotDetail>("get_dataset_snapshot", { query }),
  diffDatasetSnapshots: (query: DatasetDiffQuery) =>
    call<DatasetDiff>("diff_dataset_snapshots", { query }),
  cleanupDataset: (request: DatasetCleanupRequest) =>
    call<DatasetCleanupResult>("cleanup_dataset", { request }),
  getExportCatalog: (query: ExportCatalogQuery) =>
    call<ExportCatalog>("get_export_catalog", { query }),
  saveExportPreset: (request: ExportPresetSaveRequest) =>
    call<ExportCatalog>("save_export_preset", { request }),
  previewExport: (draft: ExportDraft) => call<ExportPreviewResult>("preview_export", { draft }),
  /** Opens the native save dialog; null when it is closed. The page never sees or sends a path. */
  chooseExportDestination: (format: ExportFormat, suggestedName: string) =>
    call<ExportDestination | null>("choose_export_destination", { format, suggestedName }),
  /** Starts the export to the destination a one-time grant from the save dialog stands for. */
  startExport: (draft: ExportDraft, grantId: string) =>
    call<ExportItem>("start_export", { draft, grantId }),
  cancelExport: (request: ExportCancelRequest) => call<ExportItem>("cancel_export", { request }),
  listExports: (query: ExportListQuery) => call<ExportPage>("list_exports", { query }),
  /** Development builds only; the desktop core refuses it in release builds. */
  seedSyntheticData: () => call<SyntheticSeedResult>("seed_synthetic_data"),
  onWorkerStatus: async (handler: (status: WorkerStatus) => void): Promise<UnlistenFn> => {
    if (!isTauri()) throw toIpcError(null, "listen");
    return listen<WorkerStatus>(WORKER_STATUS_EVENT, (event) => handler(event.payload));
  },
};
