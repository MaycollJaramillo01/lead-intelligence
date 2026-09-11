# Architecture

## Processes

```text
┌──────────────────────────── Lead Intelligence.exe ────────────────────────────┐
│  WebView2 (React UI)  ── Tauri IPC (commands + events) ──►  Rust desktop core │
│                                                             • config, logging │
│                                                             • WorkerSupervisor│
└──────────────────────────────────────────────┬────────────────────────────────┘
                                               │ stdin / stdout: JSON Lines (protocol v1)
                                               │ stderr: JSON log records
                                   ┌───────────▼────────────┐
                                   │ research-engine (Python)│── SQLite  data/lead.sqlite3
                                   │ one process, asyncio    │── DuckDB  data/analytics.duckdb
                                   └───────────┬────────────┘
                                               ├── HTTP → Ollama (127.0.0.1:11434, optional)
                                               └── nvidia-smi + nvcuda.dll (optional)
```

| Layer         | Owns                                                                 | Never does                                       |
| ------------- | -------------------------------------------------------------------- | ------------------------------------------------ |
| React UI      | Presentation, ephemeral UI state (Zustand), calls typed IPC adapters | Run SQL, spawn processes, call `invoke` ad hoc   |
| Rust core     | Window, IPC surface, configuration, logging, worker lifecycle        | Open the database files, interpret research data |
| Python worker | Databases and migrations, probes, (later) every research engine      | Talk to the UI directly, print to stdout         |

## Contracts

`services/research-engine/src/research_engine/contracts.py` (Pydantic v2) is the single source of
truth for every cross-process message: the worker envelope and payloads, and the Rust → UI
diagnostics report. `pnpm contracts` exports:

- `packages/shared-types/schema/contracts.schema.json` — JSON Schema (2020-12)
- `packages/shared-types/src/contracts.ts` — TypeScript types (json-schema-to-typescript)
- `packages/shared-types/fixtures/*.json` — synthetic golden samples

Rust mirrors the models in `src-tauri/src/protocol.rs` with `deny_unknown_fields`;
`src-tauri/tests/contracts.rs` round-trips every fixture and fails on any drift. `pnpm
contracts:check` (part of `pnpm check` and `pnpm build`) fails when generated files are stale.

### Envelope (protocol version 1)

```json
{
  "request_id": "0190…",
  "protocol_version": 1,
  "type": "diagnostics",
  "timestamp": "2026-09-11T20:02:46.930Z",
  "payload": {},
  "error": null
}
```

- Requests (core → worker): `health`, `diagnostics`, `shutdown`. The answer echoes `request_id`
  with type `<request>.result` and carries either `payload` or `error`, never both.
- Events (worker → core): `worker.ready` (handshake, carries a health payload) and
  `worker.protocol_error` (malformed input; correlated to the request id when recoverable).
- Errors: `{code, message, retryable}` with codes `invalid_message`, `unsupported_protocol`,
  `unknown_type`, `invalid_payload`, `timeout`, `shutting_down`, `internal`.
- Limits: one UTF-8 frame per line, at most 8 MiB. Oversized or invalid frames are rejected on
  both sides without losing stream synchronisation.
- Compatibility: `protocol_version` must match exactly. Additive changes (new message types, new
  nullable fields) keep the version; anything else bumps it.

## Worker lifecycle

1. `start()` (single instance per supervisor) spawns the worker with `CREATE_NO_WINDOW`, piped
   stdio and an explicit environment (`LEAD_DATA_DIR`, `LEAD_LOG_LEVEL`, `LEAD_OLLAMA_URL`, …).
2. The worker migrates both stores, then emits `worker.ready`. The core then sends an explicit
   `health` request; only after both it reports `running`. Startup is bounded by
   `worker.startup_timeout_ms` and cancelled if the app is closing.
3. A watchdog sends `health` every 30 s. Two consecutive failures count as a hang.
4. Crash (stdout EOF) or hang → the process is reaped/killed and restarted with exponential backoff
   (0.5 s, 1 s, 2 s…). More than 3 automatic restarts in 5 minutes → `failed`; the user restarts it
   from the UI, which also resets the budget.
5. Shutdown: `shutdown` request → stdin closed → wait 5 s → hard kill. The worker also exits by
   itself when stdin reaches EOF, which is what happens when the desktop core dies abruptly.

State changes are pushed to the UI as the `worker://status` event. The last 40 stderr lines are
kept in the status so a failed start shows its traceback in the diagnostics screen.

## Data flow: diagnostics screen

```text
DiagnosticsScreen ─ useDiagnosticsStore.refresh() ─ desktop.getDiagnostics()      (apps/desktop)
  → invoke("get_diagnostics")                                                     (Tauri IPC)
  → commands::get_diagnostics → supervisor.request_typed("diagnostics", 25 s)    (src-tauri)
  → worker: sqlite + duckdb checks (migrate + real queries), Ollama probe,
    nvidia-smi + CUDA driver probe, concurrently, bounded at 20 s                (research-engine)
  ← DiagnosticsResult validated by serde ← DiagnosticsReport {core, worker, components}
```

If the worker cannot answer, the report carries `components: null` and a `components_error`; the
UI shows those subsystems as "Unknown — not checked" instead of guessing.

## Errors

- Python: every request runs inside one dispatcher; `WorkerError` becomes a typed error envelope,
  anything else is logged with its traceback and answered as `internal` with a safe message.
- Rust: one `AppError` enum; technical detail is logged where it happens and commands reject with
  `CommandError {code, message}`.
- UI: `IpcError` normalises every rejection (including "not running inside Tauri"). A global error
  boundary plus `window.onerror`/`unhandledrejection` forward UI failures to the desktop log via
  the `report_ui_error` command.

## Logging

- Rust: `tracing` → JSON lines in `logs/desktop.YYYY-MM-DD.log`, rotated daily, `max_files`
  kept (default 14). Development also prints a compact console log.
- Python: JSON records on stderr; the core re-emits them into the same file under target
  `worker`, keeping `request_id`/`job_id`. Unstructured stderr (tracebacks) is logged as warnings.
- Correlation: IPC commands run in an `ipc` span; worker calls in a `worker_request` span whose
  `request_id` is the envelope id the worker logs.
- Payloads are never logged. Python log fields whose names look secret (`token`, `password`,
  `api_key`, …) are redacted.

## Paths

| What        | Development                | Release (Windows)                                    |
| ----------- | -------------------------- | ---------------------------------------------------- |
| Data        | `<repo>/data`              | `%LOCALAPPDATA%\com.leadintelligence.desktop\data`   |
| Logs        | `<repo>/logs`              | `%LOCALAPPDATA%\com.leadintelligence.desktop\logs`   |
| User config | `<repo>/config/local.toml` | `%APPDATA%\com.leadintelligence.desktop\config.toml` |
| Worker      | uv virtualenv python       | `research-engine.exe` next to the app (phase 14)     |

Nothing is hardcoded: paths come from Tauri's path resolver or `CARGO_MANIFEST_DIR` (debug builds
only) and can be overridden in configuration.
