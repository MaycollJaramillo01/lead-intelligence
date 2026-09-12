import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  invoke: vi.fn<(command: string, args?: unknown) => Promise<unknown>>(),
  tauri: { value: true },
}));
vi.mock("@tauri-apps/api/core", () => ({
  invoke: mocks.invoke,
  isTauri: () => mocks.tauri.value,
}));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn<() => Promise<() => void>>() }));

import { desktop, IpcError, toIpcError } from "./ipc";

describe("desktop IPC adapter", () => {
  beforeEach(() => {
    mocks.invoke.mockReset();
    mocks.tauri.value = true;
  });

  it("calls the matching Rust command and returns its result", async () => {
    mocks.invoke.mockResolvedValue({ state: "running" });
    await expect(desktop.restartWorker()).resolves.toEqual({ state: "running" });
    expect(mocks.invoke).toHaveBeenCalledWith("restart_worker", undefined);
  });

  it("wraps arguments by name for commands that take input", async () => {
    mocks.invoke.mockResolvedValue(null);
    const report = { source: "test", message: "boom", stack: null };
    await desktop.reportUiError(report);
    expect(mocks.invoke).toHaveBeenCalledWith("report_ui_error", { report });
  });

  it("passes data queries as the typed `query` argument", async () => {
    mocks.invoke.mockResolvedValue({ items: [], next_cursor: null, total: 0 });
    const query = { project_id: null, limit: 10, cursor: null };
    await desktop.listDatasets(query);
    expect(mocks.invoke).toHaveBeenCalledWith("list_datasets", { query });
    await desktop.getBusiness({ business_id: "01900000-0000-7000-8000-000000000001" });
    expect(mocks.invoke).toHaveBeenLastCalledWith("get_business", {
      query: { business_id: "01900000-0000-7000-8000-000000000001" },
    });
  });

  it("turns a CommandError rejection into an IpcError with the same code", async () => {
    mocks.invoke.mockRejectedValue({ code: "worker_unavailable", message: "not running" });
    const error = await desktop.getDiagnostics().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(IpcError);
    expect(error).toMatchObject({ code: "worker_unavailable", message: "not running" });
  });

  it("reports unexpected rejections as ipc_failure", () => {
    expect(toIpcError("invalid args", "get_diagnostics")).toMatchObject({
      code: "ipc_failure",
      message: "get_diagnostics failed: invalid args",
    });
  });

  it("refuses to call anything outside the desktop shell", async () => {
    mocks.tauri.value = false;
    await expect(desktop.getDiagnostics()).rejects.toMatchObject({ code: "desktop_unavailable" });
    await expect(desktop.onWorkerStatus(() => {})).rejects.toMatchObject({
      code: "desktop_unavailable",
    });
    expect(mocks.invoke).not.toHaveBeenCalled();
  });
});
