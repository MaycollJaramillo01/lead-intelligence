import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DatasetPage } from "@lead/shared-types";
import datasetFixture from "@lead/shared-types/fixtures/dataset_page.json";
import { DatasetsScreen } from "@/features/datasets/DatasetsScreen";
import { IpcError, type desktop } from "@/lib/ipc";
import { preferenceDefaults, usePreferences } from "@/stores/preferences";

const mocks = vi.hoisted(() => ({ listDatasets: vi.fn<typeof desktop.listDatasets>() }));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

describe("DatasetsScreen", () => {
  beforeEach(() => {
    usePreferences.setState(preferenceDefaults);
    mocks.listDatasets.mockReset();
  });

  it("lists datasets with their latest snapshot and marks test data", async () => {
    mocks.listDatasets.mockResolvedValue(datasetFixture as DatasetPage);
    render(<DatasetsScreen />);
    expect(await screen.findByText("Synthetic businesses")).toBeTruthy();
    expect(screen.getByText("Test data")).toBeTruthy();
    expect(screen.getAllByText("1")).toHaveLength(2); // one snapshot, version 1
    expect(screen.getByText("3")).toBeTruthy(); // rows in that snapshot
    expect(screen.getByText("Showing 1 of 1")).toBeTruthy();
  });

  it("shows zero datasets as an honest empty state", async () => {
    mocks.listDatasets.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    render(<DatasetsScreen />);
    expect(await screen.findByText("No datasets yet")).toBeTruthy();
    expect(screen.queryByText(/Showing/)).toBeNull();
  });

  it("reports why datasets could not be loaded", async () => {
    mocks.listDatasets.mockRejectedValue(new IpcError("desktop_unavailable", "Core not reachable"));
    render(<DatasetsScreen />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Datasets could not be loaded");
    expect(alert.textContent).toContain("Core not reachable");
  });
});
