import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ExportCatalog, ExportItem, ExportPage } from "@lead/shared-types";
import catalogFixture from "@lead/shared-types/fixtures/export_catalog.json";
import pageFixture from "@lead/shared-types/fixtures/export_page.json";
import { ExportsScreen } from "@/features/exports/ExportsScreen";
import { IpcError, type desktop } from "@/lib/ipc";

const mocks = vi.hoisted(() => ({
  listExports: vi.fn<typeof desktop.listExports>(),
  getExportCatalog: vi.fn<typeof desktop.getExportCatalog>(),
  cancelExport: vi.fn<typeof desktop.cancelExport>(),
}));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

const page = pageFixture as ExportPage;
const [pending] = page.items as [ExportItem];

function renderExports() {
  render(
    <MemoryRouter>
      <ExportsScreen />
    </MemoryRouter>,
  );
}

describe("ExportsScreen", () => {
  beforeEach(() => {
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.listExports.mockResolvedValue(page);
    mocks.getExportCatalog.mockResolvedValue(catalogFixture as ExportCatalog);
  });

  it("lists who exported what with its status, and the presets with what each writes", async () => {
    renderExports();
    expect(await screen.findByText("leads.csv · CSV")).toBeTruthy();
    expect(screen.getAllByText("Lead Explorer query").length).toBeGreaterThan(0);
    expect(screen.getByText("Pending")).toBeTruthy();
    expect(await screen.findByText(/Sales-ready concise · sales_ready_concise v1/)).toBeTruthy();
    expect(screen.getByText(/Dataset analytical · dataset_analytical v1/)).toBeTruthy();

    mocks.cancelExport.mockResolvedValue({
      ...pending,
      export: { ...pending.export, status: "cancelled" },
    });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Cancel export" }));
    await waitFor(() =>
      expect(mocks.cancelExport).toHaveBeenCalledWith({ export_id: pending.export.id }),
    );
  });

  it("says where exports start when nothing was exported", async () => {
    mocks.listExports.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    renderExports();
    expect(await screen.findByText("Nothing exported yet")).toBeTruthy();
  });

  it("reports why the history could not be read", async () => {
    mocks.listExports.mockRejectedValue(new IpcError("worker_unavailable", "Worker stopped"));
    renderExports();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("The export history could not be read");
    expect(alert.textContent).toContain("Worker stopped");
  });
});
