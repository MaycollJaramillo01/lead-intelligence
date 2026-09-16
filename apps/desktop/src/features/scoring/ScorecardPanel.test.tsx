import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { LeadScorecardDetail } from "@lead/shared-types";
import detailFixture from "@lead/shared-types/fixtures/lead_scorecard_detail.json";
import { ScorecardPanel } from "@/features/scoring/ScorecardPanel";
import { IpcError, type desktop } from "@/lib/ipc";
import { preferenceDefaults, usePreferences } from "@/stores/preferences";

const mocks = vi.hoisted(() => ({
  getLeadScorecard: vi.fn<typeof desktop.getLeadScorecard>(),
  actOnLead: vi.fn<typeof desktop.actOnLead>(),
}));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

/** A scorecard the real engine produced over synthetic test data. */
const detail = detailFixture as LeadScorecardDetail;
const run = detail.score_run.score_run_id;

describe("ScorecardPanel", () => {
  beforeEach(() => {
    usePreferences.setState({ ...preferenceDefaults, language: "en" });
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.getLeadScorecard.mockResolvedValue(detail);
    mocks.actOnLead.mockResolvedValue(detail);
  });

  it("explains the score with its gates, reasons, components and heuristic weights", async () => {
    render(<ScorecardPanel businessId={detail.business_id} scoreRunId={run} />);
    const card = detail.scorecard;
    const [reason] = card.reasons;
    if (card.total_score === null || !reason || !card.offer) {
      throw new Error("the fixture lead is scored and offered something");
    }
    expect((await screen.findAllByText(card.total_score.toFixed(1))).length).toBeGreaterThan(0);
    expect(mocks.getLeadScorecard).toHaveBeenCalledWith({
      business_id: detail.business_id,
      score_run_id: run,
    });
    expect(
      screen.getByText("Weights are heuristic: a declared judgement, not fitted to outcomes."),
    ).toBeTruthy();
    expect(screen.getAllByText(/do-not-contact list/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(reason.text).length).toBeGreaterThan(0);
    expect(screen.getByText("Final lead score")).toBeTruthy();
    expect(screen.getAllByText(card.offer.offer_name).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Score confirmed/).length).toBeGreaterThan(0);
  });

  it("records a lost outcome only with a reason and why it was lost", async () => {
    const user = userEvent.setup();
    render(<ScorecardPanel businessId={detail.business_id} scoreRunId={run} />);
    await screen.findByText("Review or record an outcome");
    await user.selectOptions(screen.getByLabelText("Action"), "record_outcome");
    await user.selectOptions(screen.getByLabelText("Stage reached"), "lost");
    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    await user.click(screen.getByLabelText("Reason"));
    await user.paste("Called the owner twice");
    expect(save.disabled).toBe(true);
    await user.click(screen.getByLabelText("Why it was lost"));
    await user.paste("No budget this year");
    await user.click(save);
    await waitFor(() =>
      expect(mocks.actOnLead).toHaveBeenCalledWith({
        score_run_id: run,
        business_id: detail.business_id,
        action: "record_outcome",
        stage: "lost",
        lost_reason: "No budget this year",
        reason: "Called the owner twice",
        channel: null,
        attempt_result: null,
        value_amount: null,
        value_currency: null,
        contact_quality: null,
        notes: null,
        occurred_at: null,
      }),
    );
  });

  it("records a contact attempt with its channel and result, keeping the stage", async () => {
    const user = userEvent.setup();
    render(<ScorecardPanel businessId={detail.business_id} scoreRunId={run} />);
    await screen.findByText("Review or record an outcome");
    await user.selectOptions(screen.getByLabelText("Action"), "record_attempt");
    expect(screen.queryByLabelText("Stage reached")).toBeNull();
    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    await user.click(screen.getByLabelText("Reason"));
    await user.paste("Called the published number");
    expect(save.disabled).toBe(true); // an attempt names its channel and result
    await user.selectOptions(screen.getByLabelText("Channel"), "phone");
    await user.selectOptions(screen.getByLabelText("Result"), "left_message");
    await user.click(screen.getByLabelText("Value (optional)"));
    await user.paste("1200");
    await user.click(save);
    await waitFor(() =>
      expect(mocks.actOnLead).toHaveBeenCalledWith({
        score_run_id: run,
        business_id: detail.business_id,
        action: "record_attempt",
        stage: null,
        lost_reason: null,
        reason: "Called the published number",
        channel: "phone",
        attempt_result: "left_message",
        value_amount: 1200,
        value_currency: "USD",
        contact_quality: null,
        notes: null,
        occurred_at: null,
      }),
    );
  });

  it("says when no finished run has scored the business", async () => {
    mocks.getLeadScorecard.mockRejectedValue(new IpcError("not_found", "not scored"));
    render(<ScorecardPanel businessId={detail.business_id} />);
    expect(await screen.findByText("Not scored by any finished run yet.")).toBeTruthy();
  });
});
