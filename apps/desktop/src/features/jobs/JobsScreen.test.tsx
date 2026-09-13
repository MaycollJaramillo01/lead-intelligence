import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  AiRunPage,
  ContactRunPage,
  CrawlRunPage,
  DiscoveryRunPage,
  LatinoRunPage,
  ResolutionRunPage,
  WebsiteRunPage,
} from "@lead/shared-types";
import aiFixture from "@lead/shared-types/fixtures/ai_run_page.json";
import contactFixture from "@lead/shared-types/fixtures/contact_run_page.json";
import crawlFixture from "@lead/shared-types/fixtures/crawl_run_page.json";
import discoveryFixture from "@lead/shared-types/fixtures/discovery_run_page.json";
import latinoFixture from "@lead/shared-types/fixtures/latino_run_page.json";
import resolutionFixture from "@lead/shared-types/fixtures/resolution_run_page.json";
import websiteFixture from "@lead/shared-types/fixtures/website_run_page.json";
import { JobsScreen } from "@/features/jobs/JobsScreen";
import type { desktop } from "@/lib/ipc";
import { preferenceDefaults, usePreferences } from "@/stores/preferences";

const mocks = vi.hoisted(() => ({
  listDiscoveryRuns: vi.fn<typeof desktop.listDiscoveryRuns>(),
  listCrawlRuns: vi.fn<typeof desktop.listCrawlRuns>(),
  listResolutionRuns: vi.fn<typeof desktop.listResolutionRuns>(),
  controlDiscovery: vi.fn<typeof desktop.controlDiscovery>(),
  controlCrawl: vi.fn<typeof desktop.controlCrawl>(),
  controlResolution: vi.fn<typeof desktop.controlResolution>(),
  listLatinoRuns: vi.fn<typeof desktop.listLatinoRuns>(),
  controlLatino: vi.fn<typeof desktop.controlLatino>(),
  listWebsiteRuns: vi.fn<typeof desktop.listWebsiteRuns>(),
  controlWebsiteAudit: vi.fn<typeof desktop.controlWebsiteAudit>(),
  listContactRuns: vi.fn<typeof desktop.listContactRuns>(),
  controlContacts: vi.fn<typeof desktop.controlContacts>(),
  listAiRuns: vi.fn<typeof desktop.listAiRuns>(),
  controlAi: vi.fn<typeof desktop.controlAi>(),
}));
const NO_RESOLUTION: ResolutionRunPage = { items: [], next_cursor: null, total: 0 };
const NO_EVALUATION: LatinoRunPage = { items: [], next_cursor: null, total: 0 };
const NO_AUDIT: WebsiteRunPage = { items: [], next_cursor: null, total: 0 };
const NO_CHECK: ContactRunPage = { items: [], next_cursor: null, total: 0 };
const NO_AI: AiRunPage = { items: [], next_cursor: null, total: 0 };
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

const crawls = crawlFixture as CrawlRunPage;
const discovery = discoveryFixture as DiscoveryRunPage;

function renderJobs() {
  render(
    <MemoryRouter>
      <JobsScreen />
    </MemoryRouter>,
  );
}

describe("JobsScreen", () => {
  beforeEach(() => {
    usePreferences.setState(preferenceDefaults);
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.listDiscoveryRuns.mockResolvedValue(discovery);
    mocks.listCrawlRuns.mockResolvedValue(crawls);
    mocks.listResolutionRuns.mockResolvedValue(resolutionFixture as ResolutionRunPage);
    mocks.listLatinoRuns.mockResolvedValue(latinoFixture as LatinoRunPage);
    mocks.listWebsiteRuns.mockResolvedValue(websiteFixture as WebsiteRunPage);
    mocks.listContactRuns.mockResolvedValue(contactFixture as ContactRunPage);
    mocks.listAiRuns.mockResolvedValue(aiFixture as AiRunPage);
  });

  it("lists discovery, crawl, resolution, evaluation, audit and contact runs together, from real queries", async () => {
    renderJobs();
    expect(await screen.findByText(/^Contact check · Synthetic test workspace/)).toBeTruthy();
    expect(screen.getByText(/^Local AI · Synthetic test workspace/)).toBeTruthy();
    expect(
      screen.getByText("40/40 done · 16 answered · 4 reused · 24 skipped · 0 failed"),
    ).toBeTruthy();
    expect(screen.getByText(/channels · .* likely valid · .* hypotheses/)).toBeTruthy();
    expect(await screen.findByText(/^Crawl · Synthetic discovery campaign/)).toBeTruthy();
    expect(screen.getByText(/^Discovery · Synthetic discovery campaign/)).toBeTruthy();
    expect(screen.getByText(/^Resolution · Synthetic test workspace/)).toBeTruthy();
    expect(screen.getByText(/^Latino evidence · Synthetic test workspace/)).toBeTruthy();
    expect(screen.getAllByText(/^Website audit · Synthetic test workspace/).length).toBe(2);
    expect(screen.getAllByText(/audited · .* unchanged/).length).toBe(2);
    expect(screen.getByText("2 pages · 5 requests · 1 rendered in a browser")).toBeTruthy();
    expect(screen.getByText(/pairs compared/)).toBeTruthy();
    expect(screen.getByText(/with ownership evidence · 1 market affinity/)).toBeTruthy();
    expect(mocks.listCrawlRuns).toHaveBeenCalledWith({
      project_id: null,
      campaign_id: null,
      limit: 25,
      cursor: null,
    });
  });

  it("cancels a running crawl through the crawl engine", async () => {
    const user = userEvent.setup();
    const [run] = crawls.items;
    if (!run) throw new Error("fixture has a crawl");
    mocks.listCrawlRuns.mockResolvedValue({ ...crawls, items: [{ ...run, status: "running" }] });
    mocks.listDiscoveryRuns.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    mocks.listResolutionRuns.mockResolvedValue(NO_RESOLUTION);
    mocks.listLatinoRuns.mockResolvedValue(NO_EVALUATION);
    mocks.listWebsiteRuns.mockResolvedValue(NO_AUDIT);
    mocks.listContactRuns.mockResolvedValue(NO_CHECK);
    mocks.listAiRuns.mockResolvedValue(NO_AI);
    mocks.controlCrawl.mockResolvedValue({ ...run, status: "cancelled" });
    renderJobs();
    await user.click(await screen.findByRole("button", { name: "Cancel job" }));
    await waitFor(() =>
      expect(mocks.controlCrawl).toHaveBeenCalledWith({ job_id: run.job_id, action: "cancel" }),
    );
    expect(mocks.controlDiscovery).not.toHaveBeenCalled();
  });

  it("says so when nothing has run", async () => {
    mocks.listDiscoveryRuns.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    mocks.listCrawlRuns.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    mocks.listResolutionRuns.mockResolvedValue(NO_RESOLUTION);
    mocks.listLatinoRuns.mockResolvedValue(NO_EVALUATION);
    mocks.listWebsiteRuns.mockResolvedValue(NO_AUDIT);
    mocks.listContactRuns.mockResolvedValue(NO_CHECK);
    mocks.listAiRuns.mockResolvedValue(NO_AI);
    renderJobs();
    expect(await screen.findByText("No jobs have run")).toBeTruthy();
  });
});
