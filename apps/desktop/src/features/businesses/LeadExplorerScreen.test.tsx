import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { LeadScorePage } from "@lead/shared-types";
import pageFixture from "@lead/shared-types/fixtures/lead_score_page.json";
import { PageActionsProvider } from "@/components/layout/page-chrome";
import { LeadExplorerScreen } from "@/features/businesses/LeadExplorerScreen";
import type { desktop } from "@/lib/ipc";
import { preferenceDefaults, usePreferences } from "@/stores/preferences";
import { useUiStore } from "@/stores/ui";

const mocks = vi.hoisted(() => ({
  listLeadScores: vi.fn<typeof desktop.listLeadScores>(),
}));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

/** Leads ranked by the real scoring engine over synthetic test data. */
const page = pageFixture as LeadScorePage;
const EMPTY: LeadScorePage = { score_run: null, items: [], next_cursor: null, total: 0 };

function renderScreen() {
  const host = document.createElement("div");
  document.body.append(host);
  render(
    <MemoryRouter>
      <PageActionsProvider value={host}>
        <LeadExplorerScreen />
      </PageActionsProvider>
    </MemoryRouter>,
  );
}

describe("LeadExplorerScreen", () => {
  beforeEach(() => {
    usePreferences.setState(preferenceDefaults);
    useUiStore.setState({ inspectorOpen: false, inspectedId: null });
    mocks.listLeadScores.mockReset();
  });

  it("ranks the leads of the newest finished run next to the versions that scored them", async () => {
    mocks.listLeadScores.mockResolvedValue(page);
    renderScreen();
    const [lead] = page.items;
    const run = page.score_run;
    if (!lead?.top_reason || !run) throw new Error("the fixture holds a scored lead");

    expect((await screen.findAllByText(lead.display_name)).length).toBeGreaterThan(0);
    expect(mocks.listLeadScores).toHaveBeenCalledWith({
      score_run_id: null,
      tier: null,
      min_score: null,
      problem: null,
      search: null,
      limit: 200,
      cursor: null,
    });
    expect(screen.getAllByText(lead.top_reason).length).toBeGreaterThan(0);
    expect(
      screen.getByText(
        `${run.icp_profile_name} v${run.icp_profile_version} · ${run.scoring_model_name} v${run.scoring_model_version}`,
      ),
    ).toBeTruthy();
    expect(screen.getByText(`Showing ${page.items.length} of ${page.total}`)).toBeTruthy();
  });

  it("filters by tier in the store and says when no run has scored leads", async () => {
    mocks.listLeadScores.mockResolvedValue(EMPTY);
    renderScreen();
    expect(await screen.findByText("No scored leads yet")).toBeTruthy();

    fireEvent.change(screen.getByRole("combobox", { name: "Tier" }), {
      target: { value: "review" },
    });
    await waitFor(() =>
      expect(mocks.listLeadScores).toHaveBeenLastCalledWith(
        expect.objectContaining({ tier: "review", cursor: null }),
      ),
    );
  });
});
