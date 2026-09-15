import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  DatasetCleanupResult,
  DatasetDiff,
  DatasetListItem,
  DatasetPage,
  DatasetSnapshotDetail,
  DatasetSnapshotPage,
} from "@lead/shared-types";
import cleanupFixture from "@lead/shared-types/fixtures/dataset_cleanup_result.json";
import datasetFixture from "@lead/shared-types/fixtures/dataset_page.json";
import diffFixture from "@lead/shared-types/fixtures/dataset_diff.json";
import detailFixture from "@lead/shared-types/fixtures/dataset_snapshot_detail.json";
import snapshotsFixture from "@lead/shared-types/fixtures/dataset_snapshot_page.json";
import { SnapshotsPanel } from "@/features/datasets/SnapshotsPanel";
import type { desktop } from "@/lib/ipc";

const mocks = vi.hoisted(() => ({
  listDatasetSnapshots: vi.fn<typeof desktop.listDatasetSnapshots>(),
  getDatasetSnapshot: vi.fn<typeof desktop.getDatasetSnapshot>(),
  createDatasetSnapshot: vi.fn<typeof desktop.createDatasetSnapshot>(),
  diffDatasetSnapshots: vi.fn<typeof desktop.diffDatasetSnapshots>(),
  cleanupDataset: vi.fn<typeof desktop.cleanupDataset>(),
  getExportCatalog: vi.fn<typeof desktop.getExportCatalog>(),
}));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

const [dataset] = (datasetFixture as DatasetPage).items as [DatasetListItem];
const snapshots = snapshotsFixture as DatasetSnapshotPage;
const detail = detailFixture as DatasetSnapshotDetail;
const [newest, older] = snapshots.items;

function renderPanel() {
  const onChanged = vi.fn<() => void>();
  render(<SnapshotsPanel dataset={dataset} linkedSnapshot={null} onChanged={onChanged} />);
  return onChanged;
}

describe("SnapshotsPanel", () => {
  beforeEach(() => {
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.listDatasetSnapshots.mockResolvedValue(snapshots);
    mocks.getDatasetSnapshot.mockResolvedValue(detail);
    mocks.diffDatasetSnapshots.mockResolvedValue(diffFixture as DatasetDiff);
  });

  it("opens the newest version with its measured quality and lineage, and compares it with the one before", async () => {
    renderPanel();
    expect(await screen.findByRole("region", { name: "Quality" })).toBeTruthy();
    expect(mocks.getDatasetSnapshot).toHaveBeenCalledWith({ snapshot_id: newest?.snapshot.id });
    expect(screen.getByText("primary_domain")).toBeTruthy();
    expect(screen.getByText("16 (80%)")).toBeTruthy();
    expect(screen.getAllByText(/Synthetic fixture source \(test data\)/).length).toBeGreaterThan(0);

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Compare with version 1" }));
    expect(mocks.diffDatasetSnapshots).toHaveBeenCalledWith({
      before_snapshot_id: older?.snapshot.id,
      after_snapshot_id: newest?.snapshot.id,
      limit: 100,
    });
    const diff = diffFixture as DatasetDiff;
    expect(
      await screen.findByText(
        `${diff.added_count} added · ${diff.removed_count} removed · ${diff.changed_count} changed · ${diff.unchanged_count} unchanged`,
      ),
    ).toBeTruthy();
  });

  it("previews retention, then deletes old versions only with a reason", async () => {
    const preview = cleanupFixture as DatasetCleanupResult;
    mocks.cleanupDataset.mockResolvedValueOnce(preview);
    mocks.cleanupDataset.mockResolvedValueOnce({ ...preview, mode: "apply" });
    const onChanged = renderPanel();
    const user = userEvent.setup();
    const apply = await screen.findByRole("button", { name: "Delete old versions" });
    expect(apply).toHaveProperty("disabled", true);

    await user.click(screen.getByRole("button", { name: "Preview" }));
    expect(mocks.cleanupDataset).toHaveBeenCalledWith({
      dataset_id: dataset.dataset.id,
      keep_latest: 3,
      mode: "preview",
      reason: null,
    });
    expect(await screen.findByText(/1 to delete · 1 kept/)).toBeTruthy();
    expect(screen.getByText("Version 2 · newest")).toBeTruthy();
    expect(apply).toHaveProperty("disabled", true);

    await user.click(screen.getByLabelText("Reason"));
    await user.paste("Only the current version is used");
    await user.click(apply);
    await waitFor(() =>
      expect(mocks.cleanupDataset).toHaveBeenLastCalledWith({
        dataset_id: dataset.dataset.id,
        keep_latest: 3,
        mode: "apply",
        reason: "Only the current version is used",
      }),
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("says a dataset without versions has none", async () => {
    mocks.listDatasetSnapshots.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    renderPanel();
    expect(await screen.findByRole("button", { name: "New snapshot" })).toBeTruthy();
    expect(mocks.getDatasetSnapshot).not.toHaveBeenCalled();
  });
});
