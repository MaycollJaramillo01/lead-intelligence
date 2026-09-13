import { invoke, isTauri, type InvokeArgs } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import {
  WORKER_STATUS_EVENT,
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
  /** Development builds only; the desktop core refuses it in release builds. */
  seedSyntheticData: () => call<SyntheticSeedResult>("seed_synthetic_data"),
  onWorkerStatus: async (handler: (status: WorkerStatus) => void): Promise<UnlistenFn> => {
    if (!isTauri()) throw toIpcError(null, "listen");
    return listen<WorkerStatus>(WORKER_STATUS_EVENT, (event) => handler(event.payload));
  },
};
