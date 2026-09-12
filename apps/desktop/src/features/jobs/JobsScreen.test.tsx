import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CrawlRunPage, DiscoveryRunPage, ResolutionRunPage } from "@lead/shared-types";
import crawlFixture from "@lead/shared-types/fixtures/crawl_run_page.json";
import discoveryFixture from "@lead/shared-types/fixtures/discovery_run_page.json";
import resolutionFixture from "@lead/shared-types/fixtures/resolution_run_page.json";
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
}));
const NO_RESOLUTION: ResolutionRunPage = { items: [], next_cursor: null, total: 0 };
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
  });

  it("lists discovery runs, crawls and resolution runs together, from real queries", async () => {
    renderJobs();
    expect(await screen.findByText(/^Crawl · Synthetic discovery campaign/)).toBeTruthy();
    expect(screen.getByText(/^Discovery · Synthetic discovery campaign/)).toBeTruthy();
    expect(screen.getByText(/^Resolution · Synthetic test workspace/)).toBeTruthy();
    expect(screen.getByText("2 pages · 5 requests · 1 rendered in a browser")).toBeTruthy();
    expect(screen.getByText(/pairs compared/)).toBeTruthy();
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
    renderJobs();
    expect(await screen.findByText("No jobs have run")).toBeTruthy();
  });
});
