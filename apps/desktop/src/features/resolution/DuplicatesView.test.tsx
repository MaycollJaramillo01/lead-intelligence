import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  BusinessPage,
  MatchDetail,
  MatchPage,
  ResolutionRunPage,
  SourceCatalog,
} from "@lead/shared-types";
import detailFixture from "@lead/shared-types/fixtures/match_detail.json";
import pageFixture from "@lead/shared-types/fixtures/match_page.json";
import runFixture from "@lead/shared-types/fixtures/resolution_run_page.json";
import catalogFixture from "@lead/shared-types/fixtures/source_catalog.json";
import { PageActionsProvider } from "@/components/layout/page-chrome";
import { BusinessesScreen } from "@/features/businesses/BusinessesScreen";
import type { desktop } from "@/lib/ipc";
import { preferenceDefaults, usePreferences } from "@/stores/preferences";
import { useUiStore } from "@/stores/ui";

const mocks = vi.hoisted(() => ({
  listBusinesses: vi.fn<typeof desktop.listBusinesses>(),
  getBusiness: vi.fn<typeof desktop.getBusiness>(),
  seedSyntheticData: vi.fn<typeof desktop.seedSyntheticData>(),
  listMatches: vi.fn<typeof desktop.listMatches>(),
  getMatch: vi.fn<typeof desktop.getMatch>(),
  actOnMatch: vi.fn<typeof desktop.actOnMatch>(),
  listResolutionRuns: vi.fn<typeof desktop.listResolutionRuns>(),
  startResolution: vi.fn<typeof desktop.startResolution>(),
  listSources: vi.fn<typeof desktop.listSources>(),
  listLatinoRuns: vi.fn<typeof desktop.listLatinoRuns>(),
}));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

/** Fixtures come from a real resolution run over synthetic duplicates. */
const page = pageFixture as MatchPage;
const detail = detailFixture as MatchDetail;
const runs = runFixture as ResolutionRunPage;
const EMPTY: BusinessPage = { items: [], next_cursor: null, total: 0 };

async function openDuplicates() {
  const host = document.createElement("div");
  document.body.append(host);
  render(
    <PageActionsProvider value={host}>
      <BusinessesScreen />
    </PageActionsProvider>,
  );
  const user = userEvent.setup();
  await user.click(within(host).getByRole("button", { name: "Possible duplicates" }));
  return { host, user };
}

describe("Possible duplicates", () => {
  beforeEach(() => {
    usePreferences.setState(preferenceDefaults);
    useUiStore.setState({ inspectorOpen: true, inspectedId: null });
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.listBusinesses.mockResolvedValue(EMPTY);
    mocks.listMatches.mockResolvedValue(page);
    mocks.getMatch.mockResolvedValue(detail);
    mocks.listResolutionRuns.mockResolvedValue(runs);
    mocks.listSources.mockResolvedValue(catalogFixture as SourceCatalog);
    mocks.listLatinoRuns.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
  });

  it("lists the pairs to review from the store and filters them there", async () => {
    await openDuplicates();
    const risky = page.items.find((item) => item.status === "pending" && item.risk === "high");
    if (!risky) throw new Error("the fixture holds a risky pending pair");
    expect(
      await screen.findByText(`${risky.left.display_name} ↔ ${risky.right.display_name}`),
    ).toBeTruthy();
    expect(screen.getByText(/^Last run: 12 records/)).toBeTruthy();
    expect(mocks.listMatches).toHaveBeenLastCalledWith({
      project_id: null,
      status: "pending",
      risk: null,
      min_score: null,
      connector_key: null,
      limit: 100,
      cursor: null,
    });

    fireEvent.change(screen.getByRole("combobox", { name: /Risk/ }), {
      target: { value: "high" },
    });
    fireEvent.change(screen.getByRole("spinbutton", { name: /Minimum score/ }), {
      target: { value: "90" },
    });
    await waitFor(() =>
      expect(mocks.listMatches).toHaveBeenLastCalledWith(
        expect.objectContaining({ status: "pending", risk: "high", min_score: 0.9 }),
      ),
    );
  });

  it("compares a pair side by side and records a decision with its reason", async () => {
    mocks.actOnMatch.mockResolvedValue({
      ...detail,
      match: { ...detail.match, status: "merged", is_manual: true },
    });
    const { user } = await openDuplicates();
    const name = `${detail.left.business.display_name} ↔ ${detail.right.business.display_name}`;
    await user.click(await screen.findByText(name));

    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(await within(inspector).findByText("Field by field")).toBeTruthy();
    expect(mocks.getMatch).toHaveBeenCalledWith({ match_id: detail.match.id });
    expect(within(inspector).getByText("Probably moved")).toBeTruthy();
    expect(within(inspector).getAllByText("Different").length).toBeGreaterThan(0);
    expect(within(inspector).getByText("Evidence and sources")).toBeTruthy();

    const reason = within(inspector).getByRole("textbox", { name: "Reason (optional)" });
    reason.focus();
    await user.type(reason, "Same bakery, new address", { skipClick: true });
    await user.click(within(inspector).getByRole("button", { name: "Merge" }));
    await waitFor(() =>
      expect(mocks.actOnMatch).toHaveBeenCalledWith({
        match_id: detail.match.id,
        action: "merge",
        reason: "Same bakery, new address",
      }),
    );
    await waitFor(() =>
      expect(within(inspector).getByRole("button", { name: "Merge" })).toHaveProperty(
        "disabled",
        true,
      ),
    );
    expect(within(inspector).getByRole("button", { name: "Undo" })).toHaveProperty(
      "disabled",
      false,
    );
    expect(mocks.listMatches.mock.calls.length).toBeGreaterThan(1); // the queue is read again
  });

  it("starts the resolution of every workspace", async () => {
    mocks.listResolutionRuns.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    mocks.startResolution.mockResolvedValue(runs);
    const { host, user } = await openDuplicates();
    await user.click(await within(host).findByRole("button", { name: "Resolve duplicates" }));
    await waitFor(() => expect(mocks.startResolution).toHaveBeenCalledWith({ project_id: null }));
    expect(mocks.listResolutionRuns.mock.calls.length).toBeGreaterThan(1);
  });
});
