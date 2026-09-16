import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  BriefDraft,
  BriefSection,
  EvidenceDetail,
  ResearchWorkspace,
  SalesBrief,
} from "@lead/shared-types";
import evidenceFixture from "@lead/shared-types/fixtures/evidence_detail.json";
import workspaceFixture from "@lead/shared-types/fixtures/research_workspace.json";
import briefFixture from "@lead/shared-types/fixtures/sales_brief.json";
import { BriefClaims, BriefDraftPanel } from "@/features/leads/BriefPanel";
import { researchRequest } from "@/features/leads/research";
import type { desktop } from "@/lib/ipc";
import { preferenceDefaults, usePreferences } from "@/stores/preferences";

const mocks = vi.hoisted(() => ({
  getEvidenceDetail: vi.fn<typeof desktop.getEvidenceDetail>(),
  actOnResearch: vi.fn<typeof desktop.actOnResearch>(),
  startAi: vi.fn<typeof desktop.startAi>(),
  reviewAiInference: vi.fn<typeof desktop.reviewAiInference>(),
}));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

/** A brief the real worker built over synthetic data: every claim cites stored rows. */
const brief = briefFixture as SalesBrief;
const evidence = evidenceFixture as EvidenceDetail;
const WHY: readonly BriefSection[] = [
  "business_snapshot",
  "icp_match",
  "verified_opportunity",
  "contact_route",
  "decision_maker",
  "conversation_hooks",
  "last_verified",
];

describe("sales brief", () => {
  beforeEach(() => {
    usePreferences.setState({ ...preferenceDefaults, language: "en" });
    for (const mock of Object.values(mocks)) mock.mockReset();
  });

  it("keeps facts apart from inferences, each claim citing the stored rows behind it", async () => {
    const user = userEvent.setup();
    const onPinned = vi.fn<() => void>();
    mocks.getEvidenceDetail.mockResolvedValue(evidence);
    mocks.actOnResearch.mockResolvedValue(workspaceFixture as ResearchWorkspace);
    render(<BriefClaims brief={brief} sections={WHY} split onPinned={onPinned} />);

    const claims = brief.sections
      .filter((view) => WHY.includes(view.section))
      .flatMap((view) => view.claims);
    const inferences = claims.filter((claim) => claim.is_inference).length;
    expect(
      screen.getByRole("heading", { name: `Facts (${claims.length - inferences})` }),
    ).toBeTruthy();
    expect(screen.getByRole("heading", { name: `Inferences (${inferences})` })).toBeTruthy();
    expect(
      screen.getByText("Decision maker: The business publishes no decision maker"),
    ).toBeTruthy();

    // nothing is read until a cited row is opened
    expect(mocks.getEvidenceDetail).not.toHaveBeenCalled();
    const [chip] = screen.getAllByRole("button", { name: `Show the row behind ${evidence.label}` });
    if (!chip) throw new Error("a claim cites the business record");
    await user.click(chip);
    await waitFor(() =>
      expect(mocks.getEvidenceDetail).toHaveBeenCalledWith({
        business_id: brief.business_id,
        kind: evidence.kind,
        ref: evidence.ref,
      }),
    );
    expect(await screen.findByText(/Status candidate/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Pin" }));
    await waitFor(() =>
      expect(mocks.actOnResearch).toHaveBeenCalledWith(
        researchRequest(brief.business_id, "pin", { ref_kind: evidence.kind, ref: evidence.ref }),
      ),
    );
    expect(onPinned).toHaveBeenCalled();
  });

  it("words the brief with the local model only when asked, and reviews the answer without deleting it", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn<() => void>();
    mocks.startAi.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    mocks.reviewAiInference.mockResolvedValue({} as never);
    const { rerender } = render(
      <BriefDraftPanel brief={{ ...brief, draft: null }} onChanged={onChanged} />,
    );
    expect(screen.getByText("No model wording yet")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Word it with the local model" }));
    await waitFor(() =>
      expect(mocks.startAi).toHaveBeenCalledWith({
        project_id: null,
        business_ids: [brief.business_id],
        task_types: ["sales_brief"],
      }),
    );
    expect(
      await screen.findByText("Wording requested; it appears here once the model answers"),
    ).toBeTruthy();

    const draft: BriefDraft = {
      inference_run_id: "01900000-0000-7000-8000-000000009001",
      model: "synthetic-model",
      review_status: "unreviewed",
      finished_at: brief.generated_at,
      sections: [
        {
          section: "business_snapshot",
          sentences: [{ text: "A bakery listed in Austin, TX.", claims: ["B1"], evidence: [] }],
        },
      ],
      dropped: 1,
    };
    rerender(<BriefDraftPanel brief={{ ...brief, draft }} onChanged={onChanged} />);
    expect(screen.getByText("A bakery listed in Austin, TX.")).toBeTruthy();
    expect(screen.getByText("Restates B1")).toBeTruthy();
    expect(
      screen.getByText("1 sentences dropped: the claims they restated are no longer in the brief"),
    ).toBeTruthy();
    expect((screen.getByRole("button", { name: "Reject" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    await user.click(screen.getByLabelText("Why"));
    await user.paste("It restates an old address");
    await user.click(screen.getByRole("button", { name: "Reject" }));
    await waitFor(() =>
      expect(mocks.reviewAiInference).toHaveBeenCalledWith({
        inference_run_id: draft.inference_run_id,
        decision: "reject",
        reason: "It restates an old address",
      }),
    );
    expect(onChanged).toHaveBeenCalled();
  });
});
