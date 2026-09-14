import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  IcpProfileView,
  ScoreRunItem,
  ScoreRunPage,
  ScoringCatalog,
  ScoringModelView,
  ScoringSimulation,
} from "@lead/shared-types";
import profileFixture from "@lead/shared-types/fixtures/icp_profile_view.json";
import runsFixture from "@lead/shared-types/fixtures/score_run_page.json";
import catalogFixture from "@lead/shared-types/fixtures/scoring_catalog.json";
import simulationFixture from "@lead/shared-types/fixtures/scoring_simulation.json";
import { IcpBuilderScreen } from "@/features/scoring/IcpBuilderScreen";
import type { desktop } from "@/lib/ipc";
import { preferenceDefaults, usePreferences } from "@/stores/preferences";

const mocks = vi.hoisted(() => ({
  getScoringCatalog: vi.fn<typeof desktop.getScoringCatalog>(),
  listScoreRuns: vi.fn<typeof desktop.listScoreRuns>(),
  saveIcpProfile: vi.fn<typeof desktop.saveIcpProfile>(),
  changeScoring: vi.fn<typeof desktop.changeScoring>(),
  simulateScoring: vi.fn<typeof desktop.simulateScoring>(),
}));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

/** Fixtures are synthetic test data produced by the real scoring store and engine. */
const catalog = catalogFixture as ScoringCatalog;
const runs = runsFixture as ScoreRunPage;
const [profile] = catalog.icp_profiles as [IcpProfileView];
const [heuristic] = catalog.models as [ScoringModelView];
/** A finished run of the profile's project: its stored facts are a snapshot a preview can score. */
const [, original] = runs.items as [ScoreRunItem, ScoreRunItem];

async function openProfile() {
  const user = userEvent.setup();
  render(<IcpBuilderScreen />);
  await user.click(await screen.findByRole("button", { name: new RegExp(profile.profile.name) }));
  return user;
}

describe("IcpBuilderScreen", () => {
  beforeEach(() => {
    usePreferences.setState(preferenceDefaults);
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.getScoringCatalog.mockResolvedValue(catalog);
    mocks.listScoreRuns.mockResolvedValue(runs);
    mocks.saveIcpProfile.mockResolvedValue(profileFixture as IcpProfileView);
    mocks.changeScoring.mockResolvedValue(catalog);
    mocks.simulateScoring.mockResolvedValue(simulationFixture as ScoringSimulation);
  });

  it("saves the edits of a profile as a new version of it, offers included", async () => {
    const user = await openProfile();
    expect(screen.getAllByText("Test data").length).toBeGreaterThan(0);
    const regions = screen.getByRole("textbox", { name: "Excluded regions (comma separated)" });
    expect((regions as HTMLInputElement).value).toBe("NJ");
    await user.clear(regions);
    await user.type(regions, "nj, fl");
    await user.tab();
    await user.click(screen.getByRole("button", { name: "Save new version" }));

    await waitFor(() => expect(mocks.saveIcpProfile).toHaveBeenCalledTimes(1));
    const [request] = mocks.saveIcpProfile.mock.calls[0] ?? [];
    expect(request).toMatchObject({
      profile_id: profile.profile.id,
      project_id: profile.profile.project_id,
      name: profile.profile.name,
    });
    expect(request?.definition.exclusions.regions).toEqual(["NJ", "FL"]);
    expect(request?.definition.offers.map((offer) => offer.key)).toEqual([
      "website_rebuild",
      "local_seo",
    ]);
    await waitFor(() => expect(mocks.getScoringCatalog).toHaveBeenCalledTimes(2));
  });

  it("previews the draft over the facts a run stored and writes nothing", async () => {
    const user = await openProfile();
    expect((await screen.findAllByRole("option", { name: /^Stored facts of / })).length).toBe(2);
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Facts" }),
      original.score_run_id,
    );
    await user.click(screen.getByRole("button", { name: "Preview on real data" }));

    const preview = await screen.findByRole("region", { name: "Preview on real data" });
    expect(mocks.simulateScoring).toHaveBeenCalledWith({
      project_id: profile.profile.project_id,
      facts_from_run_id: original.score_run_id,
      icp: profile.versions[0]?.definition_json,
      scoring: heuristic.versions[0]?.definition_json,
      top: 10,
    });
    expect(
      within(preview).getByText("20 businesses scored in memory: nothing was written."),
    ).toBeTruthy();
    expect(within(preview).getByText("Exclusions")).toBeTruthy();
    expect(within(preview).getByText(/^#1 Synthetic Bakery 0001/)).toBeTruthy();
    expect(mocks.saveIcpProfile).not.toHaveBeenCalled();
    expect(mocks.changeScoring).not.toHaveBeenCalled();
  });

  it("archives a profile only with a reason, sent with the change", async () => {
    const user = await openProfile();
    const archive = screen.getByRole("button", { name: "Archive" }) as HTMLButtonElement;
    expect(archive.disabled).toBe(true);
    await user.click(screen.getByLabelText("Reason for activating, archiving or restoring"));
    await user.paste("Replaced by a narrower profile");
    expect(archive.disabled).toBe(false);
    await user.click(archive);
    await waitFor(() =>
      expect(mocks.changeScoring).toHaveBeenCalledWith({
        target: "icp_profile",
        id: profile.profile.id,
        action: "archive",
        version_id: null,
        reason: "Replaced by a narrower profile",
      }),
    );
  });
});
