import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  ExportCatalog,
  ExportDraft,
  ExportItem,
  ExportPreviewResult,
} from "@lead/shared-types";
import catalogFixture from "@lead/shared-types/fixtures/export_catalog.json";
import draftFixture from "@lead/shared-types/fixtures/export_draft.json";
import itemFixture from "@lead/shared-types/fixtures/export_item.json";
import previewFixture from "@lead/shared-types/fixtures/export_preview_result.json";
import { ExportWizard } from "@/features/exports/ExportWizard";
import type { desktop } from "@/lib/ipc";

const mocks = vi.hoisted(() => ({
  getExportCatalog: vi.fn<typeof desktop.getExportCatalog>(),
  saveExportPreset: vi.fn<typeof desktop.saveExportPreset>(),
  previewExport: vi.fn<typeof desktop.previewExport>(),
  chooseExportDestination: vi.fn<typeof desktop.chooseExportDestination>(),
  startExport: vi.fn<typeof desktop.startExport>(),
  cancelExport: vi.fn<typeof desktop.cancelExport>(),
  listExports: vi.fn<typeof desktop.listExports>(),
}));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

const draft = draftFixture as ExportDraft;
const pending = itemFixture as ExportItem;
const GRANT = "01900000-0000-7000-8000-00000000abcd";

function renderWizard() {
  const query = draft.query;
  if (!query) throw new Error("the fixture drafts a query export");
  render(
    <ExportWizard
      onOpenChange={() => undefined}
      source={{ kind: "query", query, viewId: null, columns: ["name", "tier", "total_score"] }}
      suggestedName="leads"
    />,
  );
}

describe("ExportWizard", () => {
  beforeEach(() => {
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.getExportCatalog.mockResolvedValue(catalogFixture as ExportCatalog);
    mocks.previewExport.mockResolvedValue(previewFixture as ExportPreviewResult);
    mocks.chooseExportDestination.mockResolvedValue({
      grant_id: GRANT,
      file_name: "leads.csv",
      folder: "C:\\Users\\analyst\\Documents",
      format: "csv",
    });
    mocks.startExport.mockResolvedValue(pending);
  });

  it("previews the rows as written, takes the destination from the core's dialog and follows the file to its end", async () => {
    renderWizard();
    const user = userEvent.setup();
    const exportButton = await screen.findByRole("button", { name: "Export" });
    expect(exportButton).toHaveProperty("disabled", true);
    expect(screen.getByRole("button", { name: "Choose where to save…" })).toHaveProperty(
      "disabled",
      true,
    );

    await user.click(screen.getByRole("button", { name: "Preview" }));
    const sent = mocks.previewExport.mock.calls[0]?.[0];
    expect(sent?.columns.map((column) => column.key)).toEqual(["name", "tier", "total_score"]);
    expect(sent).toMatchObject({ format: "csv", bom: true, include_suppressed: false });
    expect(
      await screen.findByText(/20 match · 1 left out \(do not contact\) · 19 will be written/),
    ).toBeTruthy();
    expect(screen.getByText("Synthetic Plumbing 0701")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Choose where to save…" }));
    expect(mocks.chooseExportDestination).toHaveBeenCalledWith("csv", "leads");
    expect(await screen.findByText("leads.csv in C:\\Users\\analyst\\Documents")).toBeTruthy();

    const written: ExportItem = {
      ...pending,
      export: {
        ...pending.export,
        status: "succeeded",
        row_count: 19,
        file_sha256: "ab".repeat(32),
      },
      rows_written: 19,
      rows_total: 19,
    };
    mocks.listExports.mockResolvedValue({ items: [written], next_cursor: null, total: 1 });
    await user.click(screen.getByRole("button", { name: "Export" }));
    expect(mocks.startExport).toHaveBeenCalledWith(
      expect.objectContaining({ format: "csv" }),
      GRANT,
    );
    expect(await screen.findByText("Pending · leads.csv")).toBeTruthy();
    expect(
      await screen.findByText("Written · leads.csv", undefined, { timeout: 4000 }),
    ).toBeTruthy();
    expect(screen.getByText(`SHA-256 ${"ab".repeat(32)}`)).toBeTruthy();
  });

  it("leaves do-not-contact leads out unless they are included on purpose, with a reason", async () => {
    renderWizard();
    const user = userEvent.setup();
    await user.click(await screen.findByLabelText("Include leads on the do-not-contact list"));
    expect(screen.getByText("Including do-not-contact leads needs a reason")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Preview" })).toHaveProperty("disabled", true);

    await user.click(screen.getByLabelText("Why they are included"));
    await user.paste("Legal review of the list");
    await user.click(screen.getByRole("button", { name: "Preview" }));
    expect(mocks.previewExport).toHaveBeenCalledWith(
      expect.objectContaining({
        include_suppressed: true,
        suppressed_reason: "Legal review of the list",
      }),
    );
  });

  it("chooses nothing when the save dialog is closed", async () => {
    mocks.chooseExportDestination.mockResolvedValue(null);
    renderWizard();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Preview" }));
    await screen.findByText("Synthetic Plumbing 0701");
    await user.click(screen.getByRole("button", { name: "Choose where to save…" }));
    await waitFor(() => expect(mocks.chooseExportDestination).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "Export" })).toHaveProperty("disabled", true);
    expect(mocks.startExport).not.toHaveBeenCalled();
  });

  it("refuses to write two columns under one header", async () => {
    renderWizard();
    const user = userEvent.setup();
    const tier = await screen.findByLabelText("Header for tier");
    await user.clear(tier);
    await user.click(tier);
    await user.paste("business");
    expect(screen.getByText("Two columns have the same header")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Preview" })).toHaveProperty("disabled", true);
    expect(mocks.previewExport).not.toHaveBeenCalled();
  });
});
