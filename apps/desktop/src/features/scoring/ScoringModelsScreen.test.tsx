import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  CalibrationReport,
  IcpProfileView,
  ScoreComparison,
  ScoreRunItem,
  ScoreRunPage,
  ScoringCatalog,
  ScoringModelView,
} from "@lead/shared-types";
import calibrationFixture from "@lead/shared-types/fixtures/calibration_report.json";
import comparisonFixture from "@lead/shared-types/fixtures/score_comparison.json";
import runsFixture from "@lead/shared-types/fixtures/score_run_page.json";
import catalogFixture from "@lead/shared-types/fixtures/scoring_catalog.json";
import modelFixture from "@lead/shared-types/fixtures/scoring_model_view.json";
import { ScoringModelsScreen } from "@/features/scoring/ScoringModelsScreen";
import type { desktop } from "@/lib/ipc";
import { preferenceDefaults, usePreferences } from "@/stores/preferences";

const mocks = vi.hoisted(() => ({
  getScoringCatalog: vi.fn<typeof desktop.getScoringCatalog>(),
  listScoreRuns: vi.fn<typeof desktop.listScoreRuns>(),
  saveScoringModel: vi.fn<typeof desktop.saveScoringModel>(),
  changeScoring: vi.fn<typeof desktop.changeScoring>(),
  startScoring: vi.fn<typeof desktop.startScoring>(),
  controlScoring: vi.fn<typeof desktop.controlScoring>(),
  compareScoreRuns: vi.fn<typeof desktop.compareScoreRuns>(),
  getCalibration: vi.fn<typeof desktop.getCalibration>(),
}));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

/** Fixtures are synthetic test data produced by the real scoring store and engine. */
const catalog = catalogFixture as ScoringCatalog;
const runs = runsFixture as ScoreRunPage;
const [heuristic] = catalog.models as [ScoringModelView];
const [profile] = catalog.icp_profiles as [IcpProfileView];
/** The newest run scored the stored facts of the original run again, with another model. */
const [rescored, original] = runs.items as [ScoreRunItem, ScoreRunItem];

describe("ScoringModelsScreen", () => {
  beforeEach(() => {
    usePreferences.setState(preferenceDefaults);
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.getScoringCatalog.mockResolvedValue(catalog);
    mocks.listScoreRuns.mockResolvedValue(runs);
    mocks.saveScoringModel.mockResolvedValue(modelFixture as ScoringModelView);
    mocks.startScoring.mockResolvedValue(runs);
    mocks.compareScoreRuns.mockResolvedValue(comparisonFixture as ScoreComparison);
    mocks.getCalibration.mockResolvedValue(calibrationFixture as CalibrationReport);
  });

  it("shows the formula it applies and saves an edited weight as a new version", async () => {
    const user = userEvent.setup();
    render(<ScoringModelsScreen />);
    expect(
      await screen.findByText(
        /FINAL_LEAD_SCORE = \(ICP_MATCH × 35 \+ OPPORTUNITY_SCORE × 40 \+ CONTACTABILITY × 25\)/,
      ),
    ).toBeTruthy();
    expect(
      screen.getByText("Weights are heuristic: a declared judgement, not fitted to outcomes."),
    ).toBeTruthy();
    expect(mocks.listScoreRuns).toHaveBeenCalledWith({ project_id: null, limit: 50, cursor: null });

    fireEvent.change(
      screen.getByRole("spinbutton", { name: "Contactability weight in the final score" }),
      { target: { value: "30" } },
    );
    expect(screen.getByText(/CONTACTABILITY × 30\)/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Save new version" }));
    await waitFor(() => expect(mocks.saveScoringModel).toHaveBeenCalledTimes(1));
    const [request] = mocks.saveScoringModel.mock.calls[0] ?? [];
    expect(request?.model_id).toBe(heuristic.model.id);
    expect(request?.definition.final).toContainEqual({ component: "CONTACTABILITY", weight: 30 });
    expect(request?.definition.features).toHaveLength(catalog.features.length);
  });

  it("scores the stored facts of a run again and compares both runs", async () => {
    const user = userEvent.setup();
    render(<ScoringModelsScreen />);
    await user.click(await screen.findByRole("tab", { name: "Runs" }));

    const start = await screen.findByRole("region", { name: "Score a project" });
    await user.selectOptions(
      within(start).getByRole("combobox", { name: "Facts" }),
      original.score_run_id,
    );
    await user.click(within(start).getByRole("button", { name: "Start scoring" }));
    await waitFor(() =>
      expect(mocks.startScoring).toHaveBeenCalledWith({
        project_id: profile.profile.project_id,
        icp_profile_version_id: profile.profile.active_version_id,
        scoring_model_version_id: heuristic.model.active_version_id,
        facts_from_run_id: original.score_run_id,
      }),
    );

    const compare = screen.getByRole("region", { name: "Compare two runs" });
    await user.selectOptions(
      within(compare).getByRole("combobox", { name: "Left run" }),
      original.score_run_id,
    );
    await user.selectOptions(
      within(compare).getByRole("combobox", { name: "Right run" }),
      rescored.score_run_id,
    );
    await user.click(within(compare).getByRole("button", { name: "Compare runs" }));
    await waitFor(() =>
      expect(mocks.compareScoreRuns).toHaveBeenCalledWith({
        left_run_id: original.score_run_id,
        right_run_id: rescored.score_run_id,
        top_k: 10,
      }),
    );
    expect(await within(compare).findByText("Same facts: only the versions differ.")).toBeTruthy();
  });

  it("calibrates a finished run against later outcomes and flags heuristic weights", async () => {
    const user = userEvent.setup();
    render(<ScoringModelsScreen />);
    await user.click(await screen.findByRole("tab", { name: "Calibration" }));
    expect(await screen.findByText("Heuristic weights, not fitted")).toBeTruthy();
    expect(mocks.getCalibration).toHaveBeenCalledWith({ score_run_id: rescored.score_run_id });
    expect(screen.getByText("No outcome recorded after the facts were read")).toBeTruthy();
    expect(
      screen.getByText(/^20 scored · 0 with an outcome · 0 positive · 1 earlier outcomes left out/),
    ).toBeTruthy();
  });
});
