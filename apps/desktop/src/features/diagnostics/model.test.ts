import { describe, expect, it } from "vitest";
import type { DiagnosticsReport } from "@lead/shared-types";
import reportFixture from "@lead/shared-types/fixtures/diagnostics_report.json";
import workerDownFixture from "@lead/shared-types/fixtures/diagnostics_report.worker_down.json";
import { formatDuration, toRows } from "./model";

// Synthetic contract fixtures generated from the Pydantic models (see packages/shared-types).
const report = reportFixture as DiagnosticsReport;
const workerDown = workerDownFixture as DiagnosticsReport;
const NOW = Date.parse("2026-01-01T00:01:00Z");

describe("toRows", () => {
  it("maps every subsystem, in a fixed order, to its reported state", () => {
    const rows = toRows(report, null, NOW);
    expect(rows.map((r) => [r.id, r.state])).toEqual([
      ["core", "ok"],
      ["worker", "ok"],
      ["sqlite", "ok"],
      ["duckdb", "error"],
      ["ollama", "degraded"],
      ["gpu", "unavailable"],
    ]);
    const gpu = rows.find((r) => r.id === "gpu");
    expect(gpu?.details[0]?.[1]).toContain("Synthetic GPU");
    expect(gpu?.details.at(-1)).toEqual(["CUDA", "not available"]);
    expect(rows.find((r) => r.id === "duckdb")?.log).toEqual(["synthetic failure"]);
    expect(rows.find((r) => r.id === "worker")?.details).toContainEqual(["Uptime", "1 min"]);
  });

  it("never invents component states when the worker is down", () => {
    const rows = toRows(workerDown, null, NOW);
    const worker = rows.find((r) => r.id === "worker");
    expect(worker?.state).toBe("error");
    expect(worker?.log).toContain("SyntheticError: fixture");
    for (const id of ["sqlite", "duckdb", "ollama", "gpu"]) {
      const row = rows.find((r) => r.id === id);
      expect(row?.state).toBe("unknown");
      expect(row?.summary).toContain("synthetic fixture: worker unavailable");
    }
  });

  it("prefers a newer live worker status over the one in the report", () => {
    const restarting = { ...report.worker, state: "restarting" as const, last_error: "exit 1" };
    const worker = toRows(report, restarting, NOW).find((r) => r.id === "worker");
    expect(worker?.state).toBe("pending");
    expect(worker?.summary).toBe("Restarting after a failure: exit 1");
  });
});

describe("formatDuration", () => {
  it("uses the largest sensible unit", () => {
    expect(formatDuration(12.7)).toBe("12 s");
    expect(formatDuration(125)).toBe("2 min");
    expect(formatDuration(3 * 3600 + 5 * 60)).toBe("3 h 5 min");
  });
});
