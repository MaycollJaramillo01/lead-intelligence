import { create } from "zustand";
import type { DiagnosticsReport, WorkerStatus } from "@lead/shared-types";
import { desktop, toIpcError, type IpcError } from "@/lib/ipc";

interface DiagnosticsStore {
  report: DiagnosticsReport | null;
  /** Latest worker status; events can make it newer than `report.worker`. */
  worker: WorkerStatus | null;
  loading: boolean;
  restarting: boolean;
  error: IpcError | null;
  refresh: () => Promise<void>;
  restartWorker: () => Promise<void>;
  applyWorkerStatus: (status: WorkerStatus) => void;
}

let latestRefresh = 0;

export const useDiagnosticsStore = create<DiagnosticsStore>()((set) => ({
  report: null,
  worker: null,
  loading: false,
  restarting: false,
  error: null,

  refresh: async () => {
    const id = ++latestRefresh;
    set({ loading: true });
    try {
      const report = await desktop.getDiagnostics();
      if (id === latestRefresh) set({ report, worker: report.worker, loading: false, error: null });
    } catch (cause) {
      if (id === latestRefresh)
        set({ loading: false, error: toIpcError(cause, "get_diagnostics") });
    }
  },

  restartWorker: async () => {
    set({ restarting: true });
    try {
      const worker = await desktop.restartWorker();
      set({ worker, restarting: false, error: null });
    } catch (cause) {
      set({ restarting: false, error: toIpcError(cause, "restart_worker") });
    }
  },

  applyWorkerStatus: (worker) => set({ worker }),
}));
