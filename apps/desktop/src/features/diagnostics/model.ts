import type {
  ComponentState,
  DatabaseStatus,
  DiagnosticsReport,
  WorkerState,
  WorkerStatus,
} from "@lead/shared-types";

/** Component states plus the two UI-only states: in progress, and never checked. */
export type RowState = ComponentState | "pending" | "unknown";

export interface DiagnosticRow {
  id: "core" | "worker" | "sqlite" | "duckdb" | "ollama" | "gpu";
  label: string;
  state: RowState;
  summary: string;
  details: Array<[label: string, value: string]>;
  /** Raw lines worth showing verbatim (errors, stderr tail, configuration warnings). */
  log: string[];
}

const WORKER_STATE: Record<WorkerState, { state: RowState; label: string }> = {
  running: { state: "ok", label: "Running" },
  starting: { state: "pending", label: "Starting" },
  restarting: { state: "pending", label: "Restarting after a failure" },
  stopping: { state: "pending", label: "Stopping" },
  stopped: { state: "unavailable", label: "Not running" },
  failed: { state: "error", label: "Failed" },
};

/** Worker lifecycle state as a UI state, for the sidebar indicator and compact summaries. */
export function workerRowState(state: WorkerState | null | undefined): RowState {
  return state ? WORKER_STATE[state].state : "unknown";
}

const show = (value: string | number | null | undefined) =>
  value === null || value === undefined || value === "" ? "—" : String(value);
const yesNo = (value: boolean) => (value ? "yes" : "no");

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

function workerRow(worker: WorkerStatus, now: number): DiagnosticRow {
  const { state, label } = WORKER_STATE[worker.state];
  const health = worker.health;
  const running = worker.state === "running";
  const summary =
    !running && worker.last_error
      ? `${label}: ${worker.last_error}`
      : health
        ? `${label} · research-engine ${health.worker_version} on Python ${health.python_version}`
        : label;
  const details: DiagnosticRow["details"] = [
    ["Process ID", show(worker.pid)],
    ["Interpreter PID", show(health?.pid)],
    ["Protocol", show(health?.protocol_version)],
    [
      "Uptime",
      worker.started_at ? formatDuration((now - Date.parse(worker.started_at)) / 1000) : "—",
    ],
    ["Automatic restarts", String(worker.restart_count)],
  ];
  if (running && worker.last_error) details.push(["Recovered from", worker.last_error]);
  return {
    id: "worker",
    label: "Python Worker",
    state,
    summary,
    details,
    log: worker.state === "failed" || worker.state === "restarting" ? worker.stderr_tail : [],
  };
}

function databaseRow(id: "sqlite" | "duckdb", label: string, db: DatabaseStatus): DiagnosticRow {
  return {
    id,
    label,
    state: db.state,
    summary: db.summary,
    details: [
      ["Engine version", show(db.engine_version)],
      ["Schema version", show(db.schema_version)],
      ["File", show(db.path)],
      ["Initialized", show(db.initialized_at)],
      ["Check took", `${db.duration_ms} ms`],
    ],
    log: db.error ? [db.error] : [],
  };
}

export function toRows(
  report: DiagnosticsReport,
  liveWorker: WorkerStatus | null = null,
  now: number = Date.now(),
): DiagnosticRow[] {
  const { core, components } = report;
  const rows: DiagnosticRow[] = [
    {
      id: "core",
      label: "Desktop Core",
      state: core.state,
      summary: core.summary,
      details: [
        ["Version", core.app_version],
        ["Environment", core.environment],
        ["Platform", `${core.os} / ${core.arch}`],
        ["Uptime", formatDuration(core.uptime_s)],
        ["Data directory", core.data_dir],
        ["Log directory", core.log_dir],
        ["Configuration", core.config_sources.join(" → ")],
      ],
      log: core.config_warnings,
    },
    workerRow(liveWorker ?? report.worker, now),
  ];

  if (components === null) {
    const reason = report.components_error?.message ?? "not checked yet";
    const unchecked = [
      ["sqlite", "SQLite"],
      ["duckdb", "DuckDB"],
      ["ollama", "Ollama"],
      ["gpu", "GPU"],
    ] as const;
    for (const [id, label] of unchecked) {
      rows.push({
        id,
        label,
        state: "unknown",
        summary: `Not checked: ${reason}`,
        details: [],
        log: [],
      });
    }
    return rows;
  }

  const { sqlite, duckdb, ollama, gpu } = components;
  rows.push(databaseRow("sqlite", "SQLite", sqlite), databaseRow("duckdb", "DuckDB", duckdb));
  rows.push({
    id: "ollama",
    label: "Ollama",
    state: ollama.state,
    summary: ollama.summary,
    details: [
      ["Endpoint", ollama.base_url],
      ["Installed", yesNo(ollama.installed)],
      ["Running", yesNo(ollama.running)],
      ["Version", show(ollama.version)],
      [
        "Models",
        ollama.models.length === 0
          ? "none"
          : ollama.models
              .map((m) => [m.name, m.parameter_size, m.quantization].filter(Boolean).join(" · "))
              .join(", "),
      ],
    ],
    log: ollama.state !== "ok" && ollama.error ? [ollama.error] : [],
  });
  rows.push({
    id: "gpu",
    label: "GPU",
    state: gpu.state,
    summary: gpu.summary,
    details: [
      ...gpu.devices.map((d): [string, string] => [
        `GPU ${d.index}`,
        `${d.name} · ${show(d.memory_total_mib)} MiB total · ${show(d.memory_free_mib)} MiB free · driver ${show(d.driver_version)}`,
      ]),
      [
        "CUDA",
        gpu.cuda_available
          ? `available (driver API ${show(gpu.cuda_driver_version)})`
          : "not available",
      ],
    ],
    log: gpu.state !== "ok" && gpu.error ? [gpu.error] : [],
  });
  return rows;
}
