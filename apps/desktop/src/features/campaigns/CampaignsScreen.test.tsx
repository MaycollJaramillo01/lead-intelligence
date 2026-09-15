import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CampaignPage, CampaignSaved, CriteriaCheck, SourceCatalog } from "@lead/shared-types";
import campaignFixture from "@lead/shared-types/fixtures/campaign_page.json";
import savedFixture from "@lead/shared-types/fixtures/campaign_saved.json";
import checkFixture from "@lead/shared-types/fixtures/criteria_check.json";
import catalogFixture from "@lead/shared-types/fixtures/source_catalog.json";
import { CampaignsScreen } from "@/features/campaigns/CampaignsScreen";
import { IpcError, type desktop } from "@/lib/ipc";

const mocks = vi.hoisted(() => ({
  listCampaigns: vi.fn<typeof desktop.listCampaigns>(),
  listSources: vi.fn<typeof desktop.listSources>(),
  saveCampaign: vi.fn<typeof desktop.saveCampaign>(),
  validateCriteria: vi.fn<typeof desktop.validateCriteria>(),
}));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

const campaigns = campaignFixture as CampaignPage;
const catalog = catalogFixture as SourceCatalog;
const check = checkFixture as CriteriaCheck;
const saved = savedFixture as CampaignSaved;

/** The test tsconfig checks indexed access; the fixtures are known to hold these. */
function first<T>(items: readonly T[]): T {
  const [item] = items;
  if (item === undefined) throw new Error("expected at least one item");
  return item;
}

describe("CampaignsScreen", () => {
  beforeEach(() => {
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.listCampaigns.mockResolvedValue(campaigns);
    mocks.listSources.mockResolvedValue(catalog);
    mocks.validateCriteria.mockResolvedValue(check);
    mocks.saveCampaign.mockResolvedValue(saved);
  });

  it("lists campaigns with what their runs produced", async () => {
    render(<CampaignsScreen />);
    expect(await screen.findByText("Synthetic discovery campaign")).toBeTruthy();
    expect(screen.getByText("Test data")).toBeTruthy();
    expect(screen.getByText("Runs")).toBeTruthy();
    expect(screen.getByText("Candidates")).toBeTruthy();
  });

  it("shows an honest empty state before any campaign exists", async () => {
    mocks.listCampaigns.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    render(<CampaignsScreen />);
    expect(await screen.findByText("No campaigns yet")).toBeTruthy();
  });

  it("builds a new campaign, validates it while editing, and saves it", async () => {
    const user = userEvent.setup();
    render(<CampaignsScreen />);
    await user.click(await screen.findByRole("button", { name: /New campaign/ }));

    await user.type(screen.getByLabelText("Name"), "Hialeah builders");
    await user.click(screen.getByRole("checkbox", { name: /USAspending/ }));
    await waitFor(() => {
      const calls = mocks.validateCriteria.mock.calls;
      const [last] = first(calls.slice(-1));
      expect(last.criteria.sources.map((source) => source.connector_key)).toEqual([
        "usaspending.recipients",
      ]);
      expect(last.with_estimates).toBe(false);
    });

    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(mocks.saveCampaign).toHaveBeenCalled());
    const [request] = first(mocks.saveCampaign.mock.calls);
    expect(request.campaign_id).toBeNull();
    expect(request.name).toBe("Hialeah builders");
  });

  it("picks types of business and a state by name instead of codes", async () => {
    const user = userEvent.setup();
    render(<CampaignsScreen />);
    await user.click(await screen.findByRole("button", { name: /New campaign/ }));
    const lastCriteria = () => first(mocks.validateCriteria.mock.calls.slice(-1))[0].criteria;

    await user.click(screen.getByRole("button", { name: "Type of business" }));
    await user.click(await screen.findByRole("menuitemcheckbox", { name: "Plumbing" }));
    await user.click(screen.getByRole("menuitemcheckbox", { name: "HVAC" }));
    await user.keyboard("{Escape}");
    await user.selectOptions(screen.getByRole("combobox", { name: "State" }), "TX");
    await waitFor(() => {
      const criteria = lastCriteria();
      expect(criteria.categories).toEqual(["plumbing", "hvac"]);
      expect(criteria.verticals).toEqual(["238220"]);
      expect(criteria.geography[0]?.region).toBe("TX");
    });

    // dropping plumbing keeps the NAICS code HVAC still needs
    await user.click(screen.getByRole("button", { name: "Type of business" }));
    await user.click(await screen.findByRole("menuitemcheckbox", { name: "Plumbing" }));
    await waitFor(() => {
      expect(lastCriteria().categories).toEqual(["hvac"]);
      expect(lastCriteria().verticals).toEqual(["238220"]);
    });
  });

  it("edits an existing campaign in place and duplicates it as a new one", async () => {
    const user = userEvent.setup();
    render(<CampaignsScreen />);
    await user.click(await screen.findByRole("button", { name: /^Edit/ }));
    expect(screen.getByRole("heading", { name: "Edit campaign" })).toBeTruthy();
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe(
      "Synthetic discovery campaign",
    );
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    await user.click(await screen.findByRole("button", { name: /Duplicate/ }));
    expect(screen.getByRole("heading", { name: "New campaign" })).toBeTruthy();
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toContain("(copy)");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(mocks.saveCampaign).toHaveBeenCalled());
    expect(first(mocks.saveCampaign.mock.calls)[0].campaign_id).toBeNull();
  });

  it("shows the warnings a campaign carries instead of hiding them", async () => {
    const user = userEvent.setup();
    render(<CampaignsScreen />);
    await user.click(await screen.findByRole("button", { name: /New campaign/ }));
    expect(await screen.findByText(/Market language is an affinity signal/)).toBeTruthy();
  });

  it("reports why campaigns could not be read", async () => {
    mocks.listCampaigns.mockRejectedValue(new IpcError("worker_error", "worker is down"));
    render(<CampaignsScreen />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Campaigns could not be read");
  });
});
