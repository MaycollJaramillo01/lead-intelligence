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
  ScoreRunPage,
  WebsiteRunPage,
} from "@lead/shared-types";
import aiFixture from "@lead/shared-types/fixtures/ai_run_page.json";
import contactFixture from "@lead/shared-types/fixtures/contact_run_page.json";
import crawlFixture from "@lead/shared-types/fixtures/crawl_run_page.json";
import discoveryFixture from "@lead/shared-types/fixtures/discovery_run_page.json";
import latinoFixture from "@lead/shared-types/fixtures/latino_run_page.json";
import resolutionFixture from "@lead/shared-types/fixtures/resolution_run_page.json";
import scoreFixture from "@lead/shared-types/fixtures/score_run_page.json";
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
  listScoreRuns: vi.fn<typeof desktop.listScoreRuns>(),
  controlScoring: vi.fn<typeof desktop.controlScoring>(),
}));
const NO_RESOLUTION: ResolutionRunPage = { items: [], next_cursor: null, total: 0 };
const NO_EVALUATION: LatinoRunPage = { items: [], next_cursor: null, total: 0 };
const NO_AUDIT: WebsiteRunPage = { items: [], next_cursor: null, total: 0 };
const NO_CHECK: ContactRunPage = { items: [], next_cursor: null, total: 0 };
const NO_AI: AiRunPage = { items: [], next_cursor: null, total: 0 };
const NO_SCORE: ScoreRunPage = { items: [], next_cursor: null, total: 0 };
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

const crawls = crawlFixture as CrawlRunPage;
const discovery = discoveryFixture as DiscoveryRunPage;
const scores = scoreFixture as ScoreRunPage;

function renderJobs(path = "/jobs") {
  render(
    <MemoryRouter initialEntries={[path]}>
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
    mocks.listScoreRuns.mockResolvedValue(scores);
  });

  it("lists the runs of every engine together, from real queries", async () => {
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
    expect(
      screen.getAllByText(
        /^Lead scoring · Synthetic test workspace · Synthetic ICP: local services with weak websites v1/,
      ).length,
    ).toBe(2);
    expect(
      screen.getByText("20 businesses · 0 priority · 6 review · 10 insufficient data · 0 excluded"),
    ).toBeTruthy();
    expect(mocks.listCrawlRuns).toHaveBeenCalledWith({
      project_id: null,
      campaign_id: null,
      limit: 25,
      cursor: null,
    });
    expect(mocks.listScoreRuns).toHaveBeenCalledWith({ project_id: null, limit: 25, cursor: null });
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
    mocks.listScoreRuns.mockResolvedValue(NO_SCORE);
    mocks.controlCrawl.mockResolvedValue({ ...run, status: "cancelled" });
    renderJobs();
    await user.click(await screen.findByRole("button", { name: "Cancel job" }));
    await waitFor(() =>
      expect(mocks.controlCrawl).toHaveBeenCalledWith({ job_id: run.job_id, action: "cancel" }),
    );
    expect(mocks.controlDiscovery).not.toHaveBeenCalled();
  });

  it("cancels a running score run through the scoring engine", async () => {
    const user = userEvent.setup();
    const [run] = scores.items;
    if (!run) throw new Error("fixture has a score run");
    mocks.listScoreRuns.mockResolvedValue({
      ...scores,
      items: [{ ...run, status: "running", finished_at: null }],
    });
    mocks.listDiscoveryRuns.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    mocks.listCrawlRuns.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    mocks.listResolutionRuns.mockResolvedValue(NO_RESOLUTION);
    mocks.listLatinoRuns.mockResolvedValue(NO_EVALUATION);
    mocks.listWebsiteRuns.mockResolvedValue(NO_AUDIT);
    mocks.listContactRuns.mockResolvedValue(NO_CHECK);
    mocks.listAiRuns.mockResolvedValue(NO_AI);
    renderJobs();
    await user.click(await screen.findByRole("button", { name: "Cancel job" }));
    await waitFor(() =>
      expect(mocks.controlScoring).toHaveBeenCalledWith({ job_id: run.job_id, action: "cancel" }),
    );
    expect(mocks.controlAi).not.toHaveBeenCalled();
  });

  it("marks the job a link points at and opens a score run in the Lead Explorer", async () => {
    const [crawl] = crawls.items;
    const [score] = scores.items;
    if (!crawl || !score) throw new Error("the fixtures hold a crawl and a score run");
    renderJobs(`/jobs?job=${crawl.job_id}`);
    const name = await screen.findByText(/^Crawl · Synthetic discovery campaign/);
    expect(name.closest("li")?.getAttribute("aria-current")).toBe("true");
    expect(
      screen
        .getAllByRole("link", { name: "Lead Explorer" })
        .map((link) => link.getAttribute("href")),
    ).toContain(`/lead-explorer?run=${score.score_run_id}`);
  });

  it("says when the job a link points at is not among those listed", async () => {
    renderJobs("/jobs?job=01900000-0000-7000-8000-ffffffffffff");
    expect(
      await screen.findByText("The linked job is not among the newest jobs listed"),
    ).toBeTruthy();
  });

  it("says so when nothing has run", async () => {
    mocks.listDiscoveryRuns.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    mocks.listCrawlRuns.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    mocks.listResolutionRuns.mockResolvedValue(NO_RESOLUTION);
    mocks.listLatinoRuns.mockResolvedValue(NO_EVALUATION);
    mocks.listWebsiteRuns.mockResolvedValue(NO_AUDIT);
    mocks.listContactRuns.mockResolvedValue(NO_CHECK);
    mocks.listAiRuns.mockResolvedValue(NO_AI);
    mocks.listScoreRuns.mockResolvedValue(NO_SCORE);
    renderJobs();
    expect(await screen.findByText("No jobs have run")).toBeTruthy();
  });
});
