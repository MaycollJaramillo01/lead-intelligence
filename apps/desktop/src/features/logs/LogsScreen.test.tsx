import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EventCleanupResult, JobEventPage } from "@lead/shared-types";
import cleanupFixture from "@lead/shared-types/fixtures/event_cleanup_result.json";
import eventsFixture from "@lead/shared-types/fixtures/job_event_page.json";
import { LogsScreen } from "@/features/logs/LogsScreen";
import type { desktop } from "@/lib/ipc";

const mocks = vi.hoisted(() => ({
  listJobEvents: vi.fn<typeof desktop.listJobEvents>(),
  cleanupJobEvents: vi.fn<typeof desktop.cleanupJobEvents>(),
}));
vi.mock("@/lib/ipc", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/ipc")>()),
  desktop: mocks,
}));

const events = eventsFixture as JobEventPage;
const JOB = "01900000-0000-7000-8000-000000001670";

function renderLogs(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <LogsScreen />
    </MemoryRouter>,
  );
}

describe("LogsScreen", () => {
  beforeEach(() => {
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.listJobEvents.mockResolvedValue(events);
  });

  it("reads the log of the job a link names and narrows it by level", async () => {
    renderLogs(`/logs?job=${JOB}`);
    expect(await screen.findByText("pipeline_failed (configuration)")).toBeTruthy();
    expect(mocks.listJobEvents).toHaveBeenCalledWith(
      expect.objectContaining({ job_id: JOB, task_id: null, levels: [] }),
    );

    const user = userEvent.setup();
    await user.click(screen.getAllByRole("checkbox", { name: "Error" })[0] as HTMLElement);
    await waitFor(() =>
      expect(mocks.listJobEvents).toHaveBeenLastCalledWith(
        expect.objectContaining({ job_id: JOB, levels: ["error"] }),
      ),
    );
  });

  it("ignores an id in the address that is not one, and says so", async () => {
    renderLogs("/logs?job=../../etc");
    expect(
      await screen.findByText("A filter in the address is not a valid id and was ignored"),
    ).toBeTruthy();
    expect(mocks.listJobEvents).toHaveBeenCalledWith(expect.objectContaining({ job_id: null }));
  });

  it("previews a cleanup and deletes old events only with a reason, never errors", async () => {
    const preview: EventCleanupResult = {
      ...(cleanupFixture as EventCleanupResult),
      matched: 12,
      kept_errors: 3,
    };
    mocks.cleanupJobEvents.mockResolvedValueOnce(preview);
    mocks.cleanupJobEvents.mockResolvedValueOnce({ ...preview, mode: "apply", deleted: 12 });
    renderLogs("/logs");
    const user = userEvent.setup();
    await screen.findByText("pipeline_failed (configuration)");
    expect(
      screen.getByText("Error events are never deleted: they explain what failed."),
    ).toBeTruthy();
    const apply = screen.getByRole("button", { name: "Delete events" });
    expect(apply).toHaveProperty("disabled", true);

    await user.click(screen.getByRole("button", { name: "Preview" }));
    expect(mocks.cleanupJobEvents).toHaveBeenCalledWith({
      mode: "preview",
      older_than_days: 30,
      levels: ["debug", "info"],
      reason: null,
    });
    expect(await screen.findByText("12 events would be deleted · 3 old errors kept")).toBeTruthy();
    expect(apply).toHaveProperty("disabled", true);

    await user.click(screen.getByLabelText("Reason"));
    await user.paste("Monthly retention");
    await user.click(apply);
    await waitFor(() =>
      expect(mocks.cleanupJobEvents).toHaveBeenLastCalledWith({
        mode: "apply",
        older_than_days: 30,
        levels: ["debug", "info"],
        reason: "Monthly retention",
      }),
    );
    expect(await screen.findByText("12 events deleted · 3 old errors kept")).toBeTruthy();
  });
});
