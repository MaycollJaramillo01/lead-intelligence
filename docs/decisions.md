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

## D-015 · Data contract in `domain.py`, SQL written by hand and proven equal (phase 03)

**Context.** The model needs canonical Pydantic schemas, TypeScript types and a Rust validator, plus
a SQL schema with real constraints, triggers and partial indexes.

**Decision.** `research_engine/domain.py` is the source of truth for records, enumerations, state
machines, queries and pages; the SQL migration is written by hand. Equality is enforced by tests
instead of generation: every table is compared column by column with its record (name, type,
nullability), every enum with its lookup table, every enum column with its foreign key and the
state machines with `status_transitions`. TypeScript types are generated; Rust mirrors only what
crosses IPC (`src-tauri/src/data.rs`) and round-trips fixtures produced by the real queries.

**Why not generate the DDL.** CHECKs, triggers, partial unique indexes and FTS would need a metadata
layer richer than the SQL itself, and migrations must be frozen, checksummed files anyway.

## D-016 · UUIDv7 ids and one UTC timestamp format (phase 03)

**Decision.** Ids are UUIDv7 text generated with a per-process monotonic counter (Python 3.12 has no
`uuid7`), so they sort by creation order and keyset tie-breaks are meaningful. Timestamps are UTC
text `YYYY-MM-DDTHH:MM:SS.sssZ` in SQLite and over IPC; a `CHECK` using `strftime` rejects any other
form or impossible date, Pydantic normalises to UTC milliseconds, and Rust keeps them as validated
text so they round-trip byte for byte. DuckDB stores UTC `TIMESTAMP` (fetching `TIMESTAMPTZ` into
Python would need `pytz`).

## D-017 · Enumerations as lookup tables, transitions as data plus triggers (phase 03)

**Decision.** Each enumeration is a one-column lookup table referenced by a foreign key, and allowed
status changes live in `status_transitions`, enforced by `BEFORE UPDATE OF` triggers. The repository
checks the same machine first to return a readable error and writes the audit entry.

**Why.** A `CHECK (x IN (...))` cannot be extended without rebuilding the table in SQLite; a lookup
row is a one-line forward migration. Triggers make the rules hold for any writer, not only the
repository.

## D-018 · Provenance through extractions (phase 03)

**Decision.** Observations do not repeat source columns. Each references one `extraction` (extractor
name + version over one immutable `raw_record`), which references its source run, page and
connector. Confidence stays on the observation. AI output, assessments and scores live in their own
tables and cite evidence; they never overwrite observations.

**Consequence.** Lineage of any value is a chain of foreign keys (`repository.trace_lineage`) and
re-running an extractor version is idempotent.

## D-019 · One contact table, soft delete with audit (phase 03)

**Decision.** Phones, emails, contact/booking forms and social profiles share `contact_points`
(discriminated by `contact_type`, with type-specific checks) so validations, suppressions and
primary flags work the same for all of them. Important entities are soft-deleted (`deleted_at` +
mandatory `deleted_reason`) and audited in the same transaction; a trigger refuses to delete a live
row, so purging requires the audited soft delete first. Audit entries are written by the repository
(SQLite triggers cannot know the actor).

## D-020 · Full-text search keyed by `businesses.seq` (phase 03)

**Decision.** FTS5 `business_search` holds the display name, all observed names and places, kept in
sync by triggers. Its rowid is `businesses.seq`, an `INTEGER PRIMARY KEY` that exists only for this
purpose: the implicit rowid of a table with a text primary key can change on `VACUUM`, which would
silently point index rows at the wrong businesses.

**Trade-off.** Two index updates per new business cap ingestion at roughly 800 businesses per second
with every constraint on. Bulk imports that need more can rebuild the index in one pass.

## D-021 · Snapshots: DuckDB rows, Parquet copy, SQLite registration (phase 03)

**Decision.** A snapshot writes its rows to DuckDB, copies them to Parquet partitioned by `region`
with the same explicit schema, and only then registers itself (with lineage) in SQLite; any failure
removes the DuckDB rows and the Parquet directory. Rows reach DuckDB through a newline-delimited
JSON file read with explicit column types.

**Why not `executemany`.** In DuckDB 1.5 it took 2.3 s for 500 rows (20,000 rows would take minutes);
the JSON load takes 0.09 s for 20,000. Multi-row `VALUES` with parameters crashed the interpreter.

## D-022 · Backups before migrations, quarantine instead of deletion (phase 03)

**Decision.** Pending migrations on a used store trigger a verified backup (SQLite online backup
API; DuckDB `COPY FROM DATABASE`), keeping the newest 10. A damaged SQLite store is renamed aside and
replaced by the newest backup that passes `integrity_check` and `foreign_key_check`; with no usable
backup it is left untouched and reported. DuckDB, whose rows are derived and also stored as Parquet,
may start empty instead. Detection uses `quick_check` (cheap on every health check); DuckDB
corruption is recognised by its "not a valid DuckDB database file" error so a locked file is never
touched.

## D-023 · Development-only synthetic seed behind two guards (phase 03)

**Decision.** The UI can load a synthetic workspace to exercise the real flow (store → worker → core
→ UI → inspector with provenance) only in development: the core refuses `seed_synthetic_data`
outside `Environment::Development` and passes `LEAD_DEV_TOOLS=1` to the worker only then; the worker
refuses without it. Everything seeded is flagged `is_synthetic` and labelled in the UI.
