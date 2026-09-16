import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DatasetPage, DatasetSnapshotDetail, DatasetSnapshotPage } from "@lead/shared-types";
import datasetFixture from "@lead/shared-types/fixtures/dataset_page.json";
import detailFixture from "@lead/shared-types/fixtures/dataset_snapshot_detail.json";
import snapshotsFixture from "@lead/shared-types/fixtures/dataset_snapshot_page.json";
import { DatasetsScreen } from "@/features/datasets/DatasetsScreen";
import { IpcError, type desktop } from "@/lib/ipc";
import { preferenceDefaults, usePreferences } from "@/stores/preferences";

const mocks = vi.hoisted(() => ({
  listDatasets: vi.fn<typeof desktop.listDatasets>(),
  listDatasetSnapshots: vi.fn<typeof desktop.listDatasetSnapshots>(),
  getDatasetSnapshot: vi.fn<typeof desktop.getDatasetSnapshot>(),
}));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

const datasets = datasetFixture as DatasetPage;

function renderDatasets(path = "/datasets") {
  render(
    <MemoryRouter initialEntries={[path]}>
      <DatasetsScreen />
    </MemoryRouter>,
  );
}

describe("DatasetsScreen", () => {
  beforeEach(() => {
    usePreferences.setState({ ...preferenceDefaults, language: "en" });
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.listDatasetSnapshots.mockResolvedValue(snapshotsFixture as DatasetSnapshotPage);
    mocks.getDatasetSnapshot.mockResolvedValue(detailFixture as DatasetSnapshotDetail);
  });

  it("lists datasets with their latest snapshot and marks test data", async () => {
    mocks.listDatasets.mockResolvedValue(datasets);
    renderDatasets();
    expect(await screen.findByText("Synthetic businesses")).toBeTruthy();
    expect(screen.getByText("Test data")).toBeTruthy();
    expect(screen.getAllByText("1")).toHaveLength(2); // one snapshot, version 1
    expect(screen.getByText("3")).toBeTruthy(); // rows in that snapshot
    expect(screen.getByText("Showing 1 of 1")).toBeTruthy();
  });

  it("marks the dataset a link points at and opens its versions", async () => {
    const [item] = datasets.items;
    if (!item) throw new Error("the fixture holds a dataset");
    mocks.listDatasets.mockResolvedValue(datasets);
    renderDatasets(`/datasets?dataset=${item.dataset.id}`);
    const row = (await screen.findByText("Synthetic businesses")).closest("tr");
    expect(row?.className).toContain("bg-accent");
    expect(await screen.findByText("Snapshots of Synthetic businesses")).toBeTruthy();
    expect(mocks.listDatasetSnapshots).toHaveBeenCalledWith({
      dataset_id: item.dataset.id,
      limit: 50,
      cursor: null,
    });
  });

  it("opens the dataset of the snapshot a link points at", async () => {
    const detail = detailFixture as DatasetSnapshotDetail;
    const dataset = datasets.items[0];
    if (!dataset) throw new Error("the fixture holds a dataset");
    const linked = { ...dataset.dataset, id: detail.item.snapshot.dataset_id };
    mocks.listDatasets.mockResolvedValue({ ...datasets, items: [{ ...dataset, dataset: linked }] });
    renderDatasets(`/datasets?snapshot=${detail.item.snapshot.id}`);
    expect(await screen.findByText("Snapshots of Synthetic businesses")).toBeTruthy();
    expect(mocks.getDatasetSnapshot).toHaveBeenCalledWith({ snapshot_id: detail.item.snapshot.id });
  });

  it("shows zero datasets as an honest empty state", async () => {
    mocks.listDatasets.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    renderDatasets();
    expect(await screen.findByText("No datasets yet")).toBeTruthy();
    expect(screen.queryByText(/Showing/)).toBeNull();
  });

  it("reports why datasets could not be loaded", async () => {
    mocks.listDatasets.mockRejectedValue(new IpcError("desktop_unavailable", "Core not reachable"));
    renderDatasets();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Datasets could not be loaded");
    expect(alert.textContent).toContain("Core not reachable");
  });
});
