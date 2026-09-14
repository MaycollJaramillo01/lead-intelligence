import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ResearchWorkspace } from "@lead/shared-types";
import workspaceFixture from "@lead/shared-types/fixtures/research_workspace.json";
import { ResearchPanel } from "@/features/leads/ResearchPanel";
import { researchRequest } from "@/features/leads/research";
import type { desktop } from "@/lib/ipc";
import { preferenceDefaults, usePreferences } from "@/stores/preferences";

const mocks = vi.hoisted(() => ({
  getResearchWorkspace: vi.fn<typeof desktop.getResearchWorkspace>(),
  actOnResearch: vi.fn<typeof desktop.actOnResearch>(),
  getEvidenceDetail: vi.fn<typeof desktop.getEvidenceDetail>(),
}));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

/** A synthetic lead whose checklist still misses one required manual item. */
const workspace = workspaceFixture as ResearchWorkspace;
const id = workspace.business_id;

async function typeReason(text: string) {
  const user = userEvent.setup();
  await user.click(screen.getByLabelText("Reason"));
  await user.paste(text);
}

describe("ResearchPanel", () => {
  beforeEach(() => {
    usePreferences.setState(preferenceDefaults);
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.getResearchWorkspace.mockResolvedValue(workspace);
    mocks.actOnResearch.mockResolvedValue(workspace);
  });

  it("lists the exact sales-ready criteria and keeps sales-ready closed until every one holds", async () => {
    render(<ResearchPanel businessId={id} />);
    expect(await screen.findByText("Checklist complete")).toBeTruthy();
    for (const label of [
      "Not on the do-not-contact list",
      "An active business",
      "Scored on recent facts",
      "Priority or review tier",
      "A verified opportunity",
      "A usable contact route",
      "Contact values agreed",
    ]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(screen.getByText("Sales-ready opens once every criterion holds.")).toBeTruthy();
    const salesReady = screen.getByRole("option", { name: "Sales-ready" });
    expect((salesReady as HTMLOptionElement).disabled).toBe(true);
    // every status needs a reason
    expect((screen.getByRole("button", { name: "Set status" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("sets a status with its reason and undoes it with another", async () => {
    const user = userEvent.setup();
    const research = workspace.research;
    if (!research) throw new Error("the fixture holds a research status");
    mocks.actOnResearch.mockResolvedValue({
      ...workspace,
      research: { ...research, status: "disqualified", status_reason: "Closed for good" },
    });
    render(<ResearchPanel businessId={id} />);
    await screen.findByText("Checklist complete");
    await typeReason("Closed for good");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Research status" }),
      "disqualified",
    );
    await user.click(screen.getByRole("button", { name: "Set status" }));
    await waitFor(() =>
      expect(mocks.actOnResearch).toHaveBeenCalledWith(
        researchRequest(id, "set_status", { status: "disqualified", reason: "Closed for good" }),
      ),
    );
    expect(await screen.findByText(/^Disqualified · dev-seed/)).toBeTruthy();
    expect((screen.getByLabelText("Reason") as HTMLTextAreaElement).value).toBe("");

    await typeReason("Set by mistake");
    await user.click(screen.getByRole("button", { name: "Undo the last status change" }));
    await waitFor(() =>
      expect(mocks.actOnResearch).toHaveBeenLastCalledWith(
        researchRequest(id, "undo_status", { reason: "Set by mistake" }),
      ),
    );
  });

  it("checks an item, dismisses a task and corrects a value with a reason, and asks for a refresh", async () => {
    const user = userEvent.setup();
    const [task] = workspace.tasks;
    if (!task) throw new Error("the fixture holds a task");
    render(<ResearchPanel businessId={id} />);

    await user.click(await screen.findByRole("button", { name: "Check" }));
    await waitFor(() =>
      expect(mocks.actOnResearch).toHaveBeenLastCalledWith(
        researchRequest(id, "check_item", { item_key: "contact_route_reviewed" }),
      ),
    );

    const dismiss = () => screen.getAllByRole("button", { name: "Dismiss" })[0];
    expect((dismiss() as HTMLButtonElement).disabled).toBe(true);
    await typeReason("Not needed for this lead");
    const button = dismiss();
    if (!button) throw new Error("an open task can be dismissed");
    await user.click(button);
    await waitFor(() =>
      expect(mocks.actOnResearch).toHaveBeenLastCalledWith(
        researchRequest(id, "dismiss_task", {
          task_id: task.id,
          reason: "Not needed for this lead",
        }),
      ),
    );

    // a correction overrides the value shown, never the source; it needs a reason
    await user.click(screen.getByRole("textbox", { name: "Correct value" }));
    await user.paste("+15125550199");
    const correct = screen.getByRole("button", { name: "Correct a value" }) as HTMLButtonElement;
    expect(correct.disabled).toBe(true);
    await typeReason("Confirmed on the business's own site");
    await user.click(screen.getByRole("button", { name: "Correct a value" }));
    await waitFor(() =>
      expect(mocks.actOnResearch).toHaveBeenLastCalledWith(
        researchRequest(id, "correct_value", {
          field: "phone",
          value: "+15125550199",
          reason: "Confirmed on the business's own site",
        }),
      ),
    );

    await user.click(screen.getByRole("checkbox", { name: "Published contacts" }));
    await user.click(screen.getByRole("button", { name: "Request" }));
    await waitFor(() =>
      expect(mocks.actOnResearch).toHaveBeenLastCalledWith(
        researchRequest(id, "request_refresh", { scopes: ["contacts"] }),
      ),
    );
  });
});
