import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { WebsiteAuditDetail, WebsiteAuditPage, WebsiteRunPage } from "@lead/shared-types";
import detailFixture from "@lead/shared-types/fixtures/website_audit_detail.json";
import pageFixture from "@lead/shared-types/fixtures/website_audit_page.json";
import runFixture from "@lead/shared-types/fixtures/website_run_page.json";
import { WebsiteIntelligenceView } from "@/features/website/WebsiteIntelligenceView";
import { IpcError, type desktop } from "@/lib/ipc";
import { preferenceDefaults, usePreferences } from "@/stores/preferences";

const mocks = vi.hoisted(() => ({
  listWebsiteAudits: vi.fn<typeof desktop.listWebsiteAudits>(),
  listWebsiteRuns: vi.fn<typeof desktop.listWebsiteRuns>(),
  getWebsiteAudit: vi.fn<typeof desktop.getWebsiteAudit>(),
  startWebsiteAudit: vi.fn<typeof desktop.startWebsiteAudit>(),
  controlWebsiteAudit: vi.fn<typeof desktop.controlWebsiteAudit>(),
}));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

const page = pageFixture as WebsiteAuditPage;
const runs = runFixture as WebsiteRunPage;
const detail = detailFixture as WebsiteAuditDetail;

async function openChangedSite() {
  const user = userEvent.setup();
  render(<WebsiteIntelligenceView />);
  await user.click(await screen.findByText(detail.audit.site));
  const panel = await screen.findByRole("region", { name: "Website intelligence" });
  await within(panel).findByText("Page language declared");
  return { user, panel };
}

describe("WebsiteIntelligenceView", () => {
  beforeEach(() => {
    usePreferences.setState(preferenceDefaults);
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.listWebsiteAudits.mockResolvedValue(page);
    mocks.listWebsiteRuns.mockResolvedValue(runs);
    mocks.getWebsiteAudit.mockResolvedValue(detail);
    mocks.startWebsiteAudit.mockResolvedValue(runs);
  });

  it("lists the latest audit of every crawled site with its coverage", async () => {
    render(<WebsiteIntelligenceView />);
    expect(await screen.findByText("synthetic-roofing-0601.example")).toBeTruthy();
    expect(screen.getByText(detail.audit.site)).toBeTruthy();
    expect(screen.getAllByText("Complete").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Empty").length).toBeGreaterThan(0);
    expect(mocks.listWebsiteAudits).toHaveBeenCalledWith({
      project_id: null,
      site: null,
      limit: 500,
      cursor: null,
    });
  });

  it("opens an audit with actionable findings first, their evidence and what changed", async () => {
    const { panel } = await openChangedSite();
    expect(mocks.getWebsiteAudit).toHaveBeenCalledWith({ audit_id: detail.audit.id });
    expect(within(panel).getAllByText("Fail", { selector: "span" }).length).toBe(
      detail.audit.failed,
    );
    expect(within(panel).queryByText("Tap to call")).toBeNull();
    expect(
      within(panel).getAllByText(`http://${detail.audit.site}/photo.jpg`).length,
    ).toBeGreaterThan(0);
    expect(within(panel).getByText("google_analytics_4")).toBeTruthy();
    expect(within(panel).getByText("conversion.click_to_call")).toBeTruthy();
    expect(within(panel).getByText(/No Core Web Vitals/)).toBeTruthy();
    expect(within(panel).getByText("Audit history")).toBeTruthy();
  });

  it("filters findings by result, category and severity", async () => {
    const { user, panel } = await openChangedSite();
    await user.selectOptions(within(panel).getByLabelText("Category"), "accessibility");
    expect(within(panel).getByText("Page language declared")).toBeTruthy();
    expect(within(panel).queryByText("Home page meta description")).toBeNull();
    await user.selectOptions(within(panel).getByLabelText("Result"), "pass");
    expect(within(panel).queryByText("Page language declared")).toBeNull();
    await user.selectOptions(within(panel).getByLabelText("Category"), "conversion");
    await user.selectOptions(within(panel).getByLabelText("Severity"), "high");
    expect(within(panel).getByText("Tap to call")).toBeTruthy();
  });

  it("audits one site again within its project, and every site from the toolbar", async () => {
    const { user, panel } = await openChangedSite();
    await user.click(within(panel).getByRole("button", { name: /Audit this site again/ }));
    await waitFor(() =>
      expect(mocks.startWebsiteAudit).toHaveBeenCalledWith({
        project_id: detail.audit.project_id,
        sites: [detail.audit.site],
      }),
    );
    await user.click(screen.getByRole("button", { name: /Audit crawled sites/ }));
    await waitFor(() =>
      expect(mocks.startWebsiteAudit).toHaveBeenLastCalledWith({ project_id: null, sites: [] }),
    );
  });

  it("says why the audits could not be read", async () => {
    mocks.listWebsiteAudits.mockRejectedValue(
      new IpcError("worker_timeout", "the worker timed out"),
    );
    render(<WebsiteIntelligenceView />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("The website audits could not be read");
  });
});
