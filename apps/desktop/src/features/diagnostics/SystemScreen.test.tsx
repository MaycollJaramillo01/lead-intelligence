import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DiagnosticsReport } from "@lead/shared-types";
import reportFixture from "@lead/shared-types/fixtures/diagnostics_report.json";
import { PageActionsProvider } from "@/components/layout/page-chrome";
import { SystemScreen } from "@/features/diagnostics/SystemScreen";
import { useDiagnosticsStore } from "@/features/diagnostics/store";
import { IpcError, type desktop } from "@/lib/ipc";

const mocks = vi.hoisted(() => ({
  getDiagnostics: vi.fn<typeof desktop.getDiagnostics>(),
  restartWorker: vi.fn<typeof desktop.restartWorker>(),
}));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

const report = reportFixture as DiagnosticsReport;

/** The shell portals screen actions into the top bar; tests provide their own host. */
function renderScreen() {
  const host = document.createElement("div");
  document.body.append(host);
  return render(
    <PageActionsProvider value={host}>
      <SystemScreen />
    </PageActionsProvider>,
  );
}

describe("SystemScreen", () => {
  beforeEach(() => {
    useDiagnosticsStore.setState({
      report,
      worker: report.worker,
      loading: false,
      restarting: false,
      error: null,
    });
    mocks.getDiagnostics.mockReset();
    mocks.restartWorker.mockReset();
  });

  it("lists every subsystem with a text state, not colour alone", () => {
    renderScreen();
    for (const label of ["Desktop Core", "Python Worker", "SQLite", "DuckDB", "Ollama", "GPU"]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    expect(screen.getAllByText("OK")).toHaveLength(3);
    expect(screen.getByText("Error")).toBeTruthy();
    expect(screen.getByText("Degraded")).toBeTruthy();
    expect(screen.getByText("Unavailable")).toBeTruthy();
  });

  it("re-runs the check on demand", async () => {
    const user = userEvent.setup();
    mocks.getDiagnostics.mockResolvedValue(report);
    renderScreen();
    await user.click(screen.getByRole("button", { name: /refresh/i }));
    expect(mocks.getDiagnostics).toHaveBeenCalledOnce();
  });

  it("surfaces a failing desktop core instead of stale certainty", () => {
    useDiagnosticsStore.setState({
      report: null,
      error: new IpcError("desktop_unavailable", "The desktop core is not reachable."),
    });
    renderScreen();
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("not reachable");
    expect(screen.queryByText("SQLite")).toBeNull();
  });
});
