import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DiagnosticsReport } from "@lead/shared-types";
import reportFixture from "@lead/shared-types/fixtures/diagnostics_report.json";
import { IpcError, type desktop } from "@/lib/ipc";
import { useDiagnosticsStore } from "./store";

const mocks = vi.hoisted(() => ({
  getDiagnostics: vi.fn<typeof desktop.getDiagnostics>(),
  restartWorker: vi.fn<typeof desktop.restartWorker>(),
}));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

const report = reportFixture as DiagnosticsReport;

describe("diagnostics store", () => {
  beforeEach(() => {
    useDiagnosticsStore.setState({ report: null, worker: null, loading: false, error: null });
    mocks.getDiagnostics.mockReset();
    mocks.restartWorker.mockReset();
  });

  it("keeps only the newest of overlapping refreshes", async () => {
    let resolveOld: (value: DiagnosticsReport) => void = () => {};
    mocks.getDiagnostics
      .mockReturnValueOnce(new Promise((resolve) => (resolveOld = resolve)))
      .mockResolvedValueOnce(report);
    const stale = useDiagnosticsStore.getState().refresh();
    await useDiagnosticsStore.getState().refresh();
    resolveOld({ ...report, generated_at: "2000-01-01T00:00:00Z" });
    await stale;
    expect(useDiagnosticsStore.getState().report?.generated_at).toBe(report.generated_at);
    expect(useDiagnosticsStore.getState().loading).toBe(false);
  });

  it("records IPC failures as displayable errors", async () => {
    mocks.getDiagnostics.mockRejectedValue(new IpcError("worker_timeout", "too slow"));
    await useDiagnosticsStore.getState().refresh();
    expect(useDiagnosticsStore.getState().error).toMatchObject({ code: "worker_timeout" });
  });

  it("stores the worker status returned by a restart", async () => {
    mocks.restartWorker.mockResolvedValue({ ...report.worker, pid: 99 });
    await useDiagnosticsStore.getState().restartWorker();
    expect(useDiagnosticsStore.getState().worker?.pid).toBe(99);
    expect(useDiagnosticsStore.getState().restarting).toBe(false);
  });
});
