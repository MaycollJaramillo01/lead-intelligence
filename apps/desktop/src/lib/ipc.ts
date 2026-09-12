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
  type CommandError,
  type CriteriaCheck,
  type CriteriaCheckQuery,
  type DatasetListQuery,
  type DatasetPage,
  type DiagnosticsReport,
  type DiscoveryControlRequest,
  type DiscoveryRun,
  type DiscoveryRunListQuery,
  type DiscoveryRunPage,
  type DiscoveryStartRequest,
  type ImportPreview,
  type ImportPreviewRequest,
  type SourceCatalog,
  type SourceCatalogQuery,
  type SyntheticSeedResult,
  type UiErrorReport,
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
  /** Development builds only; the desktop core refuses it in release builds. */
  seedSyntheticData: () => call<SyntheticSeedResult>("seed_synthetic_data"),
  onWorkerStatus: async (handler: (status: WorkerStatus) => void): Promise<UnlistenFn> => {
    if (!isTauri()) throw toIpcError(null, "listen");
    return listen<WorkerStatus>(WORKER_STATUS_EVENT, (event) => handler(event.payload));
  },
};
