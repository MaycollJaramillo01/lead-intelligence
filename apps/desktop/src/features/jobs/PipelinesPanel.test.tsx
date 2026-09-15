import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CampaignPage, DatasetPage, PipelinePage, PipelineRun } from "@lead/shared-types";
import campaignFixture from "@lead/shared-types/fixtures/campaign_page.json";
import datasetFixture from "@lead/shared-types/fixtures/dataset_page.json";
import pipelineFixture from "@lead/shared-types/fixtures/pipeline_page.json";
import { PipelinesPanel } from "@/features/jobs/PipelinesPanel";
import { IpcError, type desktop } from "@/lib/ipc";

const mocks = vi.hoisted(() => ({
  listPipelines: vi.fn<typeof desktop.listPipelines>(),
  listCampaigns: vi.fn<typeof desktop.listCampaigns>(),
  listDatasets: vi.fn<typeof desktop.listDatasets>(),
  startPipeline: vi.fn<typeof desktop.startPipeline>(),
  controlPipeline: vi.fn<typeof desktop.controlPipeline>(),
  resolveDeadLetter: vi.fn<typeof desktop.resolveDeadLetter>(),
}));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

const pipelines = pipelineFixture as PipelinePage;
const campaigns = campaignFixture as CampaignPage;
const [run] = pipelines.items as [PipelineRun];

function renderPanel() {
  render(
    <MemoryRouter>
      <PipelinesPanel />
    </MemoryRouter>,
  );
}

describe("PipelinesPanel", () => {
  beforeEach(() => {
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.listPipelines.mockResolvedValue(pipelines);
    mocks.listCampaigns.mockResolvedValue(campaigns);
    mocks.listDatasets.mockResolvedValue(datasetFixture as DatasetPage);
    mocks.startPipeline.mockResolvedValue(run);
  });

  it("shows each pipeline with its stages, the controls its state allows and this machine's resources", async () => {
    renderPanel();
    expect(await screen.findByRole("button", { name: "Pause" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Resume" })).toBeNull();
    expect(screen.getByText(/2 done · 2 skipped · 1 running · 5 queued · 0 failed/)).toBeTruthy();
    expect(screen.getByText("HTTP 0/2")).toBeTruthy();
    expect(screen.getByText(/CPU 8 cores · 13%/)).toBeTruthy();
    expect(screen.getAllByText("No campaign: nothing to discover or fetch")).toHaveLength(2);
    expect(screen.getByRole("link", { name: "Events" }).getAttribute("href")).toBe(
      `/logs?job=${run.job_id}`,
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Pause" }));
    expect(mocks.controlPipeline).toHaveBeenCalledWith({ job_id: run.job_id, action: "pause" });
  });

  it("starts over a campaign and keeps the key of a start that failed, so a retry cannot run twice", async () => {
    const [campaign] = campaigns.items;
    if (!campaign) throw new Error("the fixture holds a campaign");
    renderPanel();
    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText("Run over"), `campaign:${campaign.id}`);
    mocks.startPipeline.mockRejectedValueOnce(
      new IpcError("worker_timeout", "the worker did not answer"),
    );
    await user.click(screen.getByRole("button", { name: "Start pipeline" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Start pipeline" })).toHaveProperty(
        "disabled",
        false,
      ),
    );
    await user.click(screen.getByRole("button", { name: "Start pipeline" }));
    await waitFor(() => expect(mocks.startPipeline).toHaveBeenCalledTimes(2));
    const [first, second] = mocks.startPipeline.mock.calls.map(([request]) => request);
    expect(first).toMatchObject({ project_id: campaign.project_id, campaign_id: campaign.id });
    expect(second?.idempotency_key).toBe(first?.idempotency_key);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Start pipeline" })).toHaveProperty(
        "disabled",
        false,
      ),
    );
    await user.click(screen.getByRole("button", { name: "Start pipeline" }));
    await waitFor(() => expect(mocks.startPipeline).toHaveBeenCalledTimes(3));
    expect(mocks.startPipeline.mock.calls[2]?.[0].idempotency_key).not.toBe(first?.idempotency_key);
  });

  it("retries or skips a stage that failed for good only with a reason", async () => {
    const [letter] = run.dead_letters;
    if (!letter) throw new Error("the fixture holds a dead letter");
    const failed: PipelineRun = {
      ...run,
      status: "failed",
      actions: [],
      dead_letters: [
        {
          ...letter,
          review_status: "open",
          resolved_at: null,
          resolved_by: null,
          resolution_reason: null,
        },
      ],
    };
    mocks.listPipelines.mockResolvedValue({ ...pipelines, items: [failed] });
    mocks.resolveDeadLetter.mockResolvedValue(failed);
    renderPanel();
    const group = await screen.findByRole("group", { name: "Stage waiting for a decision" });
    expect(
      within(group).getByText(/Latino evidence failed for good on attempt 1 \(configuration\)/),
    ).toBeTruthy();
    const retry = within(group).getByRole("button", { name: "Retry stage" });
    expect(retry).toHaveProperty("disabled", true);
    expect(within(group).getByRole("button", { name: "Skip stage" })).toHaveProperty(
      "disabled",
      true,
    );

    const user = userEvent.setup();
    await user.click(within(group).getByLabelText("Reason"));
    await user.paste("The rules file was restored");
    await user.click(retry);
    expect(mocks.resolveDeadLetter).toHaveBeenCalledWith({
      job_id: run.job_id,
      task_id: letter.task_id,
      action: "retry",
      reason: "The rules file was restored",
    });
  });

  it("says when there is nothing to run over and no pipeline yet", async () => {
    mocks.listPipelines.mockResolvedValue({ ...pipelines, items: [], total: 0 });
    mocks.listCampaigns.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    mocks.listDatasets.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    renderPanel();
    expect(await screen.findByText("No pipeline has run yet.")).toBeTruthy();
    expect(screen.getByText("No project yet: create a campaign or load data first.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Start pipeline" })).toHaveProperty("disabled", true);
  });

  it("reports why pipelines could not be read", async () => {
    mocks.listPipelines.mockRejectedValue(
      new IpcError("desktop_unavailable", "Core not reachable"),
    );
    renderPanel();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Pipelines could not be read");
    expect(alert.textContent).toContain("Core not reachable");
  });
});
