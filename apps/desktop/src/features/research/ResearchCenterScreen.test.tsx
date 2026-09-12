import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  CampaignPage,
  CrawlRunPage,
  CrawlStartRequest,
  CrawlUrlDetail,
  CrawlUrlPage,
} from "@lead/shared-types";
import campaignFixture from "@lead/shared-types/fixtures/campaign_page.json";
import runFixture from "@lead/shared-types/fixtures/crawl_run_page.json";
import startFixture from "@lead/shared-types/fixtures/crawl_start_request.json";
import detailFixture from "@lead/shared-types/fixtures/crawl_url_detail.json";
import urlFixture from "@lead/shared-types/fixtures/crawl_url_page.json";
import { PageActionsProvider } from "@/components/layout/page-chrome";
import { ResearchCenterScreen } from "@/features/research/ResearchCenterScreen";
import { DEFAULT_CRAWL_SETTINGS, reasonLabel } from "@/features/research/settings";
import { translate } from "@/lib/i18n";
import { IpcError, type desktop } from "@/lib/ipc";
import { preferenceDefaults, usePreferences } from "@/stores/preferences";

const mocks = vi.hoisted(() => ({
  listCampaigns: vi.fn<typeof desktop.listCampaigns>(),
  listCrawlRuns: vi.fn<typeof desktop.listCrawlRuns>(),
  listCrawlUrls: vi.fn<typeof desktop.listCrawlUrls>(),
  getCrawlUrl: vi.fn<typeof desktop.getCrawlUrl>(),
  startCrawl: vi.fn<typeof desktop.startCrawl>(),
  controlCrawl: vi.fn<typeof desktop.controlCrawl>(),
}));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

const campaigns = campaignFixture as CampaignPage;
const runs = runFixture as CrawlRunPage;
const urls = urlFixture as CrawlUrlPage;
const detail = detailFixture as CrawlUrlDetail;

function first<T>(items: readonly T[]): T {
  const [item] = items;
  if (item === undefined) throw new Error("expected at least one item");
  return item;
}

function renderScreen() {
  const host = document.createElement("div");
  document.body.append(host);
  render(
    <PageActionsProvider value={host}>
      <ResearchCenterScreen />
    </PageActionsProvider>,
  );
  return host;
}

describe("ResearchCenterScreen", () => {
  beforeEach(() => {
    usePreferences.setState(preferenceDefaults);
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.listCampaigns.mockResolvedValue(campaigns);
    mocks.listCrawlRuns.mockResolvedValue(runs);
    mocks.listCrawlUrls.mockResolvedValue(urls);
    mocks.getCrawlUrl.mockResolvedValue(detail);
    mocks.startCrawl.mockResolvedValue(first(runs.items));
    mocks.controlCrawl.mockResolvedValue(first(runs.items));
  });

  it("shows what a crawl did: budget, HTTP against browser, and failures grouped", async () => {
    renderScreen();
    const run = await screen.findByRole("region", { name: "Crawl" });
    expect(within(run).getByText("Requests used / left")).toBeTruthy();
    expect(within(run).getByText("5 / 1995")).toBeTruthy();
    expect(within(run).getByText("Pages HTTP / browser")).toBeTruthy();
    expect(within(run).getByText("1 / 1")).toBeTruthy();
    expect(within(run).getByText("Failed pages, grouped")).toBeTruthy();
    expect(within(run).getByText("Not found")).toBeTruthy();
    expect(screen.getByText(/Pages are acquired, not judged/)).toBeTruthy();
    await waitFor(() =>
      expect(mocks.listCrawlUrls).toHaveBeenCalledWith(
        expect.objectContaining({ job_id: first(runs.items).job_id, status: null }),
      ),
    );
  });

  it("lists every URL with the policy or error that decided it", async () => {
    renderScreen();
    expect((await screen.findAllByText("Disallowed by robots.txt")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Private area").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Empty page shell (needs JavaScript)").length).toBeGreaterThan(0);
  });

  it("opens a page with the reason and cost of its browser fallback", async () => {
    const user = userEvent.setup();
    renderScreen();
    const contact = await screen.findByText(detail.crawl_url.url);
    await user.click(contact);
    expect(await screen.findByText("How it was fetched")).toBeTruthy();
    expect(screen.getByText(/Empty page shell \(needs JavaScript\) · 900 ms/)).toBeTruthy();
    expect(screen.getAllByText(/hello@synthetic-bakery-0001.example/).length).toBeGreaterThan(0);
    expect(mocks.getCrawlUrl).toHaveBeenCalledWith({ crawl_url_id: detail.crawl_url.id });
  });

  it("starts a crawl of the addresses typed, with the limits chosen", async () => {
    const user = userEvent.setup();
    const host = renderScreen();
    await screen.findByRole("region", { name: "Crawl" });
    const start = within(host).getByRole("button", { name: /Start crawl/ });
    expect((start as HTMLButtonElement).disabled).toBe(true);

    // jsdom gives every element a zero-sized box, so the resizable panels' global pointer handler
    // claims each synthetic click; focusing and typing without one is what a keyboard user does
    const area = screen.getByLabelText("Website addresses");
    area.focus();
    await user.type(area, "https://example.com{enter}example.org", { skipClick: true });
    await user.click(within(host).getByRole("button", { name: /Limits/ }));
    const pages = screen.getByLabelText("Pages per site");
    pages.focus();
    await user.keyboard("{Control>}a{/Control}5");
    await user.selectOptions(screen.getByLabelText("Browser"), "never");
    await user.click(start);

    await waitFor(() => expect(mocks.startCrawl).toHaveBeenCalled());
    const [request] = first(mocks.startCrawl.mock.calls) as [CrawlStartRequest];
    expect(request.campaign_id).toBeNull();
    expect(request.urls).toEqual(["https://example.com", "example.org"]);
    expect(request.settings.max_pages_per_site).toBe(5);
    expect(request.settings.browser).toBe("never");
  });

  it("says why the crawl could not be read", async () => {
    mocks.listCrawlRuns.mockRejectedValue(new IpcError("worker_timeout", "the worker timed out"));
    renderScreen();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("The crawl could not be read");
  });
});

describe("crawl settings in the interface", () => {
  it("start from the same defaults as the worker", () => {
    expect(DEFAULT_CRAWL_SETTINGS).toEqual((startFixture as CrawlStartRequest).settings);
  });

  it("name known reasons and show unknown ones as they are", () => {
    const t = (key: Parameters<typeof translate>[1]) => translate("es", key);
    expect(reasonLabel(t, "robots")).toBe("Prohibida por robots.txt");
    expect(reasonLabel(t, "something_new")).toBe("something_new");
  });
});
