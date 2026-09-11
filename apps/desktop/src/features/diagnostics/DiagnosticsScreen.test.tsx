import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DiagnosticsReport, WorkerStatus } from "@lead/shared-types";
import reportFixture from "@lead/shared-types/fixtures/diagnostics_report.json";
import { IpcError, type desktop } from "@/lib/ipc";
import { DiagnosticsScreen } from "./DiagnosticsScreen";
import { useDiagnosticsStore } from "./store";

const mocks = vi.hoisted(() => ({
  getDiagnostics: vi.fn<typeof desktop.getDiagnostics>(),
  restartWorker: vi.fn<typeof desktop.restartWorker>(),
  reportUiError: vi.fn<typeof desktop.reportUiError>(),
  onWorkerStatus: vi.fn<typeof desktop.onWorkerStatus>(),
}));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

const report = reportFixture as DiagnosticsReport;

describe("DiagnosticsScreen", () => {
  beforeEach(() => {
    useDiagnosticsStore.setState({
      report: null,
      worker: null,
      loading: false,
      restarting: false,
      error: null,
    });
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.onWorkerStatus.mockResolvedValue(() => {});
    mocks.reportUiError.mockResolvedValue(null);
  });

  it("renders every subsystem with a text state, not colour alone", async () => {
    mocks.getDiagnostics.mockResolvedValue(report);
    render(<DiagnosticsScreen />);
    expect(await screen.findByText("Desktop Core")).toBeTruthy();
    for (const label of ["Python Worker", "SQLite", "DuckDB", "Ollama", "GPU"]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    expect(screen.getAllByText("OK")).toHaveLength(3);
    expect(screen.getByText("Error")).toBeTruthy();
    expect(screen.getByText("Degraded")).toBeTruthy();
    expect(screen.getByText("Unavailable")).toBeTruthy();
  });

  it("shows the failure instead of stale or invented data", async () => {
    mocks.getDiagnostics.mockRejectedValue(
      new IpcError("desktop_unavailable", "The desktop core is not reachable."),
    );
    render(<DiagnosticsScreen />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Desktop core not available");
    expect(screen.queryByText("SQLite")).toBeNull();
  });

  it("restarts the worker on request", async () => {
    mocks.getDiagnostics.mockResolvedValue(report);
    mocks.restartWorker.mockResolvedValue(report.worker);
    render(<DiagnosticsScreen />);
    await userEvent.click(await screen.findByRole("button", { name: /restart worker/i }));
    expect(mocks.restartWorker).toHaveBeenCalledOnce();
  });

  it("re-checks the system when a worker status event reports it running again", async () => {
    let emit: ((status: WorkerStatus) => void) | undefined;
    mocks.onWorkerStatus.mockImplementation(async (handler: (status: WorkerStatus) => void) => {
      emit = handler;
      return () => {};
    });
    mocks.getDiagnostics.mockResolvedValue(report);
    render(<DiagnosticsScreen />);
    await screen.findByText("Desktop Core");
    const calls = mocks.getDiagnostics.mock.calls.length;

    emit?.({ ...report.worker, state: "restarting" });
    expect(await screen.findByText(/Restarting after a failure/)).toBeTruthy();
    emit?.({ ...report.worker, state: "running" });
    await waitFor(() => expect(mocks.getDiagnostics.mock.calls.length).toBe(calls + 1));
  });
});
