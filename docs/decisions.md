# Technical decisions

Each entry records the context, the decision and its consequences. Newer phases append entries;
a superseded decision is marked, not deleted.

## D-001 · Stack and repository layout (phase 01)

**Context.** The product is a Windows 11 desktop tool; the mandatory stack is Tauri 2, React +
strict TypeScript + Vite, Rust, and a Python 3.12+ research sidecar managed with `uv`.

**Decision.** pnpm workspace with `apps/desktop` (UI) and `packages/shared-types` (contracts);
`src-tauri/` at the repository root, pointing at the UI through `build.devUrl`/`frontendDist`;
`services/research-engine/` as an independent uv project. TypeScript tooling keeps the Vite
template's **oxlint** (fast, zero-config React/TS rules) plus **Prettier**; Rust uses rustfmt +
clippy (`-D warnings`); Python uses ruff (lint + format) and mypy `--strict`. TanStack Table and
ECharts are deferred to the phases that first use them (02 and later), per the prompt.

**Consequences.** One command surface (`pnpm …`) drives all three toolchains. Rust uses edition
2024 and forbids `unsafe`.

## D-002 · Rust ↔ Python transport: JSON Lines over stdio (phase 01)

**Context.** The prompt allows JSON Lines over stdio or localhost with an ephemeral port.

**Decision.** JSON Lines over the worker's stdin/stdout, one envelope per line, stderr reserved for
logs. The worker redirects `sys.stdout` to stderr at startup so stray prints can never corrupt a
frame.

**Why not localhost.** A socket adds a listening port (firewall prompts, other local processes
can connect, needs a token), port allocation races and a separate liveness channel. Stdio has none
of that and ties the worker's lifetime to its parent: when the core dies, the pipe closes and the
worker sees EOF and exits.

**Consequences.** One in-flight channel multiplexed by `request_id`; large results must stay under
the 8 MiB frame limit (bulk data will travel through files/DuckDB, not IPC, in later phases).

## D-003 · Contracts: Pydantic is the source of truth (phase 01)

**Decision.** Contracts are Pydantic v2 models. JSON Schema, TypeScript types and golden fixtures
are generated from them (`pnpm contracts`); Rust mirrors them by hand and proves equivalence by
round-tripping every fixture. `contracts:check` blocks stale outputs.

**Why.** Almost every future contract (discovery, evidence, scoring) originates in the Python
engines. Generating Rust types would add a second generator for little gain; a round-trip test
catches drift just as well.

## D-004 · The worker owns SQLite and DuckDB (phase 01)

**Decision.** Only the Python worker opens `lead.sqlite3` and `analytics.duckdb`. Rust never
touches the files; the UI never runs SQL. Both stores are versioned by forward-only SQL migrations
in `research_engine/migrations/<engine>/NNNN_name.sql`, applied one transaction each, recorded in
`schema_migrations` with a SHA-256 checksum (line endings normalised). Editing an applied migration,
or opening a database created by a newer build, is an error.

**Why.** DuckDB allows a single writing process; the research engines that write live in Python.
One owner means one migration system and no cross-process locking issues. SQLite runs in WAL mode
with foreign keys and a 5 s busy timeout.

**Consequences.** When the worker is down, data views show "unavailable" instead of stale data.
Backups before destructive migrations arrive with the first destructive migration (phase 03).

## D-005 · Layered configuration with embedded defaults (phase 01)

**Decision.** `config/default.toml` + `config/<environment>.toml` are compiled into the binary; a
user file and `LEAD_*` variables override them (see operations). Unknown keys and out-of-range
values reject the override layers as a whole: the app starts on built-in defaults and shows the
warning in diagnostics. Secrets never live in configuration (OS credential store, phase 14).

## D-006 · Structured, correlated logging (phase 01)

**Decision.** Rust `tracing` writes JSON lines to a daily-rotated file; the worker logs JSON to
stderr and the core re-emits each record into the same file. Correlation uses `request_id`
(envelope) and, from phase 04, `job_id`. Payloads are never logged.

## D-007 · Worker supervision policy (phase 01)

**Decision.** Single instance; ready handshake + health check before `running`; per-request
timeouts; watchdog every 30 s (two failures = hang); at most 3 automatic restarts per 5 minutes
with exponential backoff, then `failed` until the user restarts it; graceful shutdown → stdin EOF →
kill after 5 s.

**Orphan prevention.** The worker exits on stdin EOF, so it cannot outlive a crashed core. In
development the venv `python.exe` launcher additionally runs the interpreter inside a Windows job
object. When Chromium arrives (phase 05) the core should put the whole worker tree in a
kill-on-close job object.

## D-008 · Detection without side effects (phase 01)

**Decision.** Ollama: `GET /api/version` and `/api/tags` with a short timeout, nothing is pulled or
deleted; installed-but-stopped is distinguished from not installed. On Windows a closed loopback
port often answers with a connect _timeout_ (~2 s of SYN retries), so connect timeouts count as
"not running", while read timeouts count as "running but unresponsive". GPU: `nvidia-smi
--query-gpu` for name/VRAM/driver plus `cuDriverGetVersion` from the CUDA driver library for CUDA
availability; no GPU model is assumed and every missing piece degrades explicitly.

## D-009 · Content Security Policy (phase 01)

**Decision.** Release builds use a strict CSP (`default-src 'self'`, IPC only, no remote
origins), so the app cannot load anything from the internet. Development disables the CSP
(`devCsp: null`) because Vite's HMR needs inline scripts and websockets. Tauri capabilities stay at
`core:default` until the hardening phase (14) narrows them.

## D-010 · Routing: hash router over a data-driven registry (phase 02)

**Decision.** `react-router` with `createHashRouter`; every route is generated from
`src/app/navigation.ts`, and modules without an implementation render the shared "not configured"
page instead of being hidden.

**Why hash.** The packaged app is served from a custom protocol with no server rewrites, so a
path-based route would 404 on reload. Hash routing also keeps deep links working (phase 12).

**Why a registry.** Sidebar, breadcrumbs, command palette, shortcuts, the overview and the
placeholder pages all describe the same 16 modules; one table keeps them from drifting apart.

## D-011 · TanStack Table v8, not v9 (phase 02)

**Decision.** Pin `@tanstack/react-table` to v8 plus `@tanstack/react-virtual` v3.

**Why.** v9 ships a different, still-moving API (feature objects, renamed row models). v8 is stable
and gives everything the dense table needs: sorting, selection, sizing, pinning, visibility and
filters. Rows are virtualized above 50 rows; below that the DOM stays simple.

## D-012 · A small typed dictionary instead of an i18n framework (phase 02)

**Decision.** `src/lib/i18n.ts` with flat keys, `en` as the source of truth and `es` as a partial
override; missing keys fall back to English. `TranslationKey` is derived from the English
dictionary, so an unknown key is a type error.

**Why.** The app needs two languages, interpolation and a fallback. That is around forty lines;
i18next would add a dependency, async loading and configuration for no gain at this size. Swapping
it later is contained to one module.

## D-013 · Shortcuts as data, conflicts as a test (phase 02)

**Decision.** One registry of combos with group, description key and target; a test fails when two
shortcuts claim the same combo, and the help panel renders from the same list. Shortcuts do not
fire while typing, except the command palette.

## D-014 · Contrast is validated, not asserted (phase 02)

**Decision.** The design tokens stay in CSS, and a unit test converts them from oklch to sRGB and
checks WCAG ratios for both themes (4.5:1 for text pairs, 3:1 for focus ring and control borders).

**Consequence.** Two tokens changed to pass: the dark primary and destructive surfaces were
darkened, and the form-control border was strengthened in both themes. Row separators stay
deliberately subtle and are excluded: they are decoration, not information.
