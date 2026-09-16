import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RouterProvider } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DiagnosticsReport, ExplorerCatalog, SourceCatalog } from "@lead/shared-types";
import reportFixture from "@lead/shared-types/fixtures/diagnostics_report.json";
import explorerFixture from "@lead/shared-types/fixtures/explorer_catalog.json";
import catalogFixture from "@lead/shared-types/fixtures/source_catalog.json";
import { createTestRouter } from "@/app/router";
import { NAV_ITEMS } from "@/app/navigation";
import { en } from "@/lib/locales/en";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useDiagnosticsStore } from "@/features/diagnostics/store";
import { IpcError, type desktop } from "@/lib/ipc";
import { preferenceDefaults, usePreferences } from "@/stores/preferences";
import { useUiStore } from "@/stores/ui";

const mocks = vi.hoisted(() => ({
  getDiagnostics: vi.fn<typeof desktop.getDiagnostics>(),
  restartWorker: vi.fn<typeof desktop.restartWorker>(),
  reportUiError: vi.fn<typeof desktop.reportUiError>(),
  onWorkerStatus: vi.fn<typeof desktop.onWorkerStatus>(),
  listBusinesses: vi.fn<typeof desktop.listBusinesses>(),
  listDatasets: vi.fn<typeof desktop.listDatasets>(),
  listSources: vi.fn<typeof desktop.listSources>(),
  listCampaigns: vi.fn<typeof desktop.listCampaigns>(),
  listDiscoveryRuns: vi.fn<typeof desktop.listDiscoveryRuns>(),
  listCandidates: vi.fn<typeof desktop.listCandidates>(),
  listCrawlRuns: vi.fn<typeof desktop.listCrawlRuns>(),
  listResolutionRuns: vi.fn<typeof desktop.listResolutionRuns>(),
  listLatinoRuns: vi.fn<typeof desktop.listLatinoRuns>(),
  listWebsiteRuns: vi.fn<typeof desktop.listWebsiteRuns>(),
  listContactRuns: vi.fn<typeof desktop.listContactRuns>(),
  listAiRuns: vi.fn<typeof desktop.listAiRuns>(),
  listScoreRuns: vi.fn<typeof desktop.listScoreRuns>(),
  getExplorerCatalog: vi.fn<typeof desktop.getExplorerCatalog>(),
  exploreLeads: vi.fn<typeof desktop.exploreLeads>(),
  getBusiness: vi.fn<typeof desktop.getBusiness>(),
  getSalesBrief: vi.fn<typeof desktop.getSalesBrief>(),
  listExports: vi.fn<typeof desktop.listExports>(),
  getExportCatalog: vi.fn<typeof desktop.getExportCatalog>(),
  listJobEvents: vi.fn<typeof desktop.listJobEvents>(),
}));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

const report = reportFixture as DiagnosticsReport;

function renderApp(path = "/overview") {
  render(
    <TooltipProvider>
      <RouterProvider router={createTestRouter([path])} />
    </TooltipProvider>,
  );
}

describe("application shell", () => {
  beforeEach(() => {
    usePreferences.setState({ ...preferenceDefaults, language: "en", showAdvancedTools: true });
    useUiStore.setState({ commandPaletteOpen: false, shortcutsHelpOpen: false, inspectedId: null });
    useDiagnosticsStore.setState({ report, worker: report.worker, error: null, loading: false });
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.getDiagnostics.mockResolvedValue(report);
    mocks.onWorkerStatus.mockResolvedValue(() => {});
    mocks.reportUiError.mockResolvedValue(null);
    mocks.listBusinesses.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    mocks.listDatasets.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    mocks.listSources.mockResolvedValue(catalogFixture as SourceCatalog);
    mocks.listCampaigns.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    mocks.listDiscoveryRuns.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    mocks.listCandidates.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    mocks.listCrawlRuns.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    mocks.listResolutionRuns.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    mocks.listLatinoRuns.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    mocks.listWebsiteRuns.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    mocks.listContactRuns.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    mocks.listAiRuns.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    mocks.listScoreRuns.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    mocks.getExplorerCatalog.mockResolvedValue(explorerFixture as ExplorerCatalog);
    mocks.exploreLeads.mockResolvedValue({
      score_run: null,
      offset: 0,
      rows: [],
      total: 0,
      generated_at: "2026-01-01T00:00:00.000Z",
    });
  });

  it("shows every module in the sidebar, grouped in sections", () => {
    renderApp();
    const sidebar = screen.getByRole("navigation", { name: "Lead Intelligence" });
    for (const item of NAV_ITEMS) {
      // the System link also carries the worker indicator, so its name starts with the module name
      expect(
        within(sidebar).getByRole("link", { name: new RegExp(en[item.titleKey]) }),
      ).toBeTruthy();
    }
    expect(within(sidebar).getByRole("heading", { name: "System" })).toBeTruthy();
  });

  it("navigates from the sidebar and keeps breadcrumbs useful", async () => {
    const user = userEvent.setup();
    renderApp();
    const sidebar = screen.getByRole("navigation", { name: "Lead Intelligence" });
    await user.click(within(sidebar).getByRole("link", { name: /businesses/i }));

    expect(await screen.findByText("No businesses yet")).toBeTruthy();
    const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(breadcrumb.textContent).toContain("Research");
    expect(breadcrumb.textContent).toContain("Businesses");
  });

  it("serves the datasets module from real queries", async () => {
    renderApp("/datasets");
    expect(await screen.findByText("No datasets yet")).toBeTruthy();
    expect(mocks.listDatasets).toHaveBeenCalledWith({ project_id: null, limit: 100, cursor: null });
  });

  it("serves the acquisition modules from real queries", async () => {
    renderApp("/sources");
    expect(await screen.findByText("USAspending.gov award recipients")).toBeTruthy();
    expect(mocks.listSources).toHaveBeenCalledWith({ project_id: null });
  });

  it("opens the exports and the event log, the last modules of phase 13", async () => {
    mocks.listExports.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    mocks.getExportCatalog.mockRejectedValue(new IpcError("worker_unavailable", "stopped"));
    mocks.listJobEvents.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    renderApp("/exports");
    expect(await screen.findByText("Nothing exported yet")).toBeTruthy();
    expect(screen.queryByText("This module is not configured yet")).toBeNull();
  });

  it("opens the event log", async () => {
    mocks.listJobEvents.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    renderApp("/logs");
    expect(await screen.findByText("No events")).toBeTruthy();
    expect(screen.queryByText("This module is not configured yet")).toBeNull();
  });

  it("opens a business in full from a deep link, under its module", async () => {
    mocks.getBusiness.mockRejectedValue(new IpcError("not_found", "no such business"));
    mocks.getSalesBrief.mockRejectedValue(new IpcError("not_found", "no such business"));
    renderApp("/businesses/01900000-0000-7000-8000-000000000999");
    expect(await screen.findByText("This business is no longer available")).toBeTruthy();
    const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(breadcrumb.textContent).toContain("Businesses");
  });

  it("lands on a not-found page for unknown routes", async () => {
    renderApp("/nope");
    expect(await screen.findByText("/nope")).toBeTruthy();
  });

  it("opens the command palette with Ctrl+K and navigates from it", async () => {
    const user = userEvent.setup();
    renderApp();
    await user.keyboard("{Control>}k{/Control}");

    const palette = await screen.findByRole("dialog");
    await user.type(within(palette).getByRole("combobox"), "jobs");
    await user.keyboard("{Enter}");

    expect(await screen.findByText("No jobs have run")).toBeTruthy();
  });

  it("jumps between modules with keyboard shortcuts", async () => {
    const user = userEvent.setup();
    renderApp();
    await user.keyboard("{Alt>}3{/Alt}");
    expect(await screen.findByText("No scored leads yet")).toBeTruthy();
  });

  it("toggles theme and sidebar from the keyboard and stores the choice", async () => {
    const user = userEvent.setup();
    renderApp();
    await user.keyboard("{Alt>}t{/Alt}");
    expect(usePreferences.getState().theme).toBe("light");

    await user.keyboard("{Control>}b{/Control}");
    expect(usePreferences.getState().sidebarCollapsed).toBe(true);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeTruthy(),
    );
  });

  it("lists the shortcuts in a help panel", async () => {
    const user = userEvent.setup();
    renderApp();
    await user.keyboard("?");
    const dialog = await screen.findByRole("dialog", { name: "Keyboard shortcuts" });
    expect(within(dialog).getByText("Open the command palette")).toBeTruthy();
    expect(within(dialog).getByText("Jump to Businesses")).toBeTruthy();
  });

  it("renders the workspace paths reported by the desktop core in settings", async () => {
    renderApp("/settings");
    expect(await screen.findByText(report.core.data_dir)).toBeTruthy();
    expect(screen.getByText(report.core.log_dir)).toBeTruthy();
  });
});
