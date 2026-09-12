import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  CampaignPage,
  CandidateDetail,
  CandidatePage,
  DiscoveryRunPage,
  ImportPreview,
} from "@lead/shared-types";
import campaignFixture from "@lead/shared-types/fixtures/campaign_page.json";
import candidateFixture from "@lead/shared-types/fixtures/candidate_page.json";
import detailFixture from "@lead/shared-types/fixtures/candidate_detail.json";
import previewFixture from "@lead/shared-types/fixtures/import_preview.json";
import runFixture from "@lead/shared-types/fixtures/discovery_run_page.json";
import { PageActionsProvider } from "@/components/layout/page-chrome";
import { DiscoveryScreen } from "@/features/discovery/DiscoveryScreen";
import { IpcError, type desktop } from "@/lib/ipc";
import { preferenceDefaults, usePreferences } from "@/stores/preferences";

const mocks = vi.hoisted(() => ({
  listCampaigns: vi.fn<typeof desktop.listCampaigns>(),
  listDiscoveryRuns: vi.fn<typeof desktop.listDiscoveryRuns>(),
  listCandidates: vi.fn<typeof desktop.listCandidates>(),
  getCandidate: vi.fn<typeof desktop.getCandidate>(),
  startDiscovery: vi.fn<typeof desktop.startDiscovery>(),
  controlDiscovery: vi.fn<typeof desktop.controlDiscovery>(),
  previewImport: vi.fn<typeof desktop.previewImport>(),
}));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

const campaigns = campaignFixture as CampaignPage;
const runs = runFixture as DiscoveryRunPage;
const candidates = candidateFixture as CandidatePage;
const detail = detailFixture as CandidateDetail;
const preview = previewFixture as ImportPreview;
const CSV = ["name,city", "Panaderia El Sol,Austin", ""].join("\n");

/** The test tsconfig checks indexed access; the fixtures are known to hold these. */
function first<T>(items: readonly T[]): T {
  const [item] = items;
  if (item === undefined) throw new Error("expected at least one item");
  return item;
}

/** The shell hosts screen actions in the top bar; in isolation the test provides that host. */
function renderScreen() {
  const host = document.createElement("div");
  document.body.append(host);
  return render(
    <PageActionsProvider value={host}>
      <DiscoveryScreen />
    </PageActionsProvider>,
  );
}

describe("DiscoveryScreen", () => {
  beforeEach(() => {
    usePreferences.setState(preferenceDefaults);
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.listCampaigns.mockResolvedValue(campaigns);
    mocks.listDiscoveryRuns.mockResolvedValue(runs);
    mocks.listCandidates.mockResolvedValue(candidates);
    mocks.getCandidate.mockResolvedValue(detail);
    mocks.startDiscovery.mockResolvedValue(first(runs.items));
    mocks.controlDiscovery.mockResolvedValue(first(runs.items));
  });

  it("lists candidates of the selected campaign and never calls them leads", async () => {
    renderScreen();
    expect(await screen.findAllByText("Synthetic Bakery 0001")).toBeTruthy();
    expect(screen.getByText("Candidates are references, not qualified leads.")).toBeTruthy();
    await waitFor(() =>
      expect(mocks.listCandidates).toHaveBeenCalledWith(
        expect.objectContaining({
          campaign_id: first(campaigns.items).id,
          include_duplicates: false,
        }),
      ),
    );
  });

  it("hides repeated candidates until they are asked for", async () => {
    const user = userEvent.setup();
    renderScreen();
    await screen.findAllByText("Synthetic Bakery 0001");
    await user.click(screen.getByRole("checkbox", { name: "Show repeated candidates" }));
    await waitFor(() =>
      expect(mocks.listCandidates).toHaveBeenCalledWith(
        expect.objectContaining({ include_duplicates: true }),
      ),
    );
  });

  it("shows what a run did, per source", async () => {
    renderScreen();
    expect((await screen.findAllByText(/Synthetic fixture source/)).length).toBeGreaterThan(0);
    expect(screen.getByText("Pages")).toBeTruthy();
    expect(screen.getByText("Repeats")).toBeTruthy();
  });

  it("opens a candidate and shows the raw record with its provenance", async () => {
    const user = userEvent.setup();
    renderScreen();
    const rows = await screen.findAllByText("Synthetic Bakery 0001");
    await user.click(first(rows));
    expect(await screen.findByText("Raw record")).toBeTruthy();
    expect(screen.getByText(/Exactly what the source returned/)).toBeTruthy();
    expect(screen.getByText(/sha256/)).toBeTruthy();
    expect(mocks.getCandidate).toHaveBeenCalledWith({ candidate_id: first(candidates.items).id });
  });

  it("will not start an import run until a file is chosen, then previews and starts it", async () => {
    const user = userEvent.setup();
    mocks.previewImport.mockResolvedValue(preview);
    renderScreen();

    const start = await screen.findByRole("button", { name: /Start run/ });
    expect((start as HTMLButtonElement).disabled).toBe(true);

    const file = new File([CSV], "leads.csv", { type: "text/csv" });
    await user.upload(screen.getByLabelText("Choose a file"), file);
    expect(await screen.findByText(/leads.csv: 3 rows, 3 usable/)).toBeTruthy();
    await waitFor(() => expect(mocks.previewImport).toHaveBeenCalled());
    expect(first(mocks.previewImport.mock.calls)[0].filename).toBe("leads.csv");

    await user.click(screen.getByRole("button", { name: /Start run/ }));
    await waitFor(() => expect(mocks.startDiscovery).toHaveBeenCalled());
    const [request] = first(mocks.startDiscovery.mock.calls);
    expect(request.campaign_id).toBe(first(campaigns.items).id);
    expect(request.inputs["file.import"]).toMatchObject({ filename: "leads.csv", content: CSV });
  });

  it("says why discovery could not be read", async () => {
    mocks.listCandidates.mockRejectedValue(new IpcError("worker_timeout", "the worker timed out"));
    renderScreen();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Discovery could not be read");
  });
});
