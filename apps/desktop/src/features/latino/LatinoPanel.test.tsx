import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { LatinoDetail } from "@lead/shared-types";
import detailFixture from "@lead/shared-types/fixtures/latino_detail.json";
import { LatinoPanel } from "@/features/latino/LatinoPanel";
import type { desktop } from "@/lib/ipc";
import { preferenceDefaults, usePreferences } from "@/stores/preferences";

const mocks = vi.hoisted(() => ({
  getLatino: vi.fn<typeof desktop.getLatino>(),
  reviewLatino: vi.fn<typeof desktop.reviewLatino>(),
}));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

/**
 * A real evaluation of synthetic records that a directory and a newer registry record disagree
 * about, reviewed by a person: the calculation and its contradiction stay visible under the review.
 */
const detail = detailFixture as LatinoDetail;

describe("LatinoPanel", () => {
  beforeEach(() => {
    usePreferences.setState(preferenceDefaults);
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.getLatino.mockResolvedValue(detail);
  });

  it("keeps ownership and market apart and shows every piece of evidence with its source", async () => {
    render(<LatinoPanel businessId={detail.business_id} />);
    expect(await screen.findByText("No ownership evidence counted")).toBeTruthy();
    expect(mocks.getLatino).toHaveBeenCalledWith({ business_id: detail.business_id });
    expect(screen.getByText("No market signals")).toBeTruthy();
    expect(screen.getByText("unknown")).toBeTruthy(); // the stable code beside the label
    expect(screen.getByText("Some evidence speaks against ownership")).toBeTruthy();
    expect(screen.getByText("Evidence against ownership")).toBeTruthy();
    expect(screen.getByText("Contradicted")).toBeTruthy();
    expect(
      screen.getByText(
        "Names, surnames, photos, addresses and presumed nationality are never used.",
      ),
    ).toBeTruthy();
    expect(screen.getAllByText(/Synthetic directory \(test data\)/).length).toBeGreaterThan(0);
    const [decision] = detail.decisions;
    if (!decision) throw new Error("the fixture holds a review decision");
    expect(screen.getByText(decision.reason)).toBeTruthy();
    expect(screen.getByText("Assessment history")).toBeTruthy();
  });

  it("records a review only with an assessment and a reason", async () => {
    mocks.reviewLatino.mockResolvedValue(detail);
    const user = userEvent.setup();
    render(<LatinoPanel businessId={detail.business_id} />);

    const submit = await screen.findByRole("button", { name: "Record review" });
    expect(submit).toHaveProperty("disabled", true);
    await user.selectOptions(screen.getByRole("combobox", { name: "Assessment" }), "withdraw");
    expect(submit).toHaveProperty("disabled", true); // still without a reason
    await user.type(
      screen.getByRole("textbox", { name: "Reason (required)" }),
      "The registry record was checked",
    );
    await user.click(submit);
    await waitFor(() =>
      expect(mocks.reviewLatino).toHaveBeenCalledWith({
        business_id: detail.root_business_id,
        status: null,
        reason: "The registry record was checked",
      }),
    );
  });

  it("shows a merged record the evidence of its whole business and reviews only there", async () => {
    const record = "01900000-0000-7000-8000-000000009999";
    mocks.getLatino.mockResolvedValue({ ...detail, business_id: record });
    render(<LatinoPanel businessId={record} />);
    expect(await screen.findByText(/belongs to another business/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Record review" })).toBeNull();
  });
});
