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
kill-on-close job object. _Superseded by D-035: the worker does it itself._

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

## D-024 · One adapter per source behind a common contract (phase 04)

**Decision.** Every source implements `DiscoverySource` (metadata, `validate_config`, `estimate`,
`discover`, `checkpoint`) and is stateless: the engine owns cursors, transactions, limits, retries,
rate limits and provenance. A source is registered in `discovery/registry.py`, which is also what
creates and updates its `source_connectors` row.

**Why.** A monolithic scraper cannot be reasoned about, rate-limited or tested per source. With this
split, adding a source is one small module plus one registry line, and the engine's guarantees
(no repeated page, provenance, limits) hold for it automatically.

**Consequence.** Capabilities are data: the campaign builder reads them to tell the operator which
criteria a source will ignore, instead of silently dropping them.

## D-025 · Enablement is a rule, not a list (phase 04)

**Decision.** A source is enabled when it allows automated access and needs no credentials it does
not have. A source that forbids automation stays disabled whatever the database says; a source that
needs a key is `needs_configuration` until the operator configures it, and refuses with
`auth_required`. The shipped `directory.api_template` is exactly that: registered, visible, and
deliberately not working.

**Why.** The prompt forbids CAPTCHA solving, unauthorised authentication, bypassing restrictions and
rotating identities. A rule in code cannot be forgotten the way a checklist can, and a visible
template documents what a keyed connector must declare without inventing an endpoint or a key.

## D-026 · USAspending.gov as the first live source (phase 04)

**Decision.** The public structured source that proves the engine end to end is the USAspending.gov
award-search API: US federal award recipients, public domain, no credentials, real pagination, and
filters for state, city, ZIP, NAICS and date. Its default rate limit is 30 requests a minute with a
daily budget of 2000, deliberately below anything the API asks for.

**Why.** It exercises pagination, checkpoints, `Retry-After`, resume and structured errors against a
real API rather than a mock, and it is legal to query without an account. Its records are a signal
(the business exists, sells B2B/B2G, is registered), never proof of anything else, and the award
payload is kept so the claim can be checked later.

**Trade-off.** It only covers businesses that hold federal awards. Directories with wider coverage
need an API key and terms the operator accepts, which is what the template is for.

## D-027 · One page, one transaction, checkpoint last (phase 04)

**Decision.** A page's `source_query`, `acquired_page`, `raw_records`, `discovery_candidates` and its
`job_checkpoint` are written in a single transaction, with the checkpoint last. The checkpoint holds
the next cursor and the counters (pages, records, candidates, repeats, requests).

**Why.** It makes "do not repeat a page on resume" a property of the database rather than of the
code path: a crash either stored the whole page and its cursor, or neither.

**Consequence.** Progress is read back from the checkpoint instead of being recounted, and a run
interrupted by a restart is `paused` (with a `run_interrupted` event), never silently restarted -
an app that resumed network activity by itself at startup would be a surprise.

## D-028 · Candidates are a separate, immutable table (phase 04)

**Decision.** `discovery_candidates` (migration 0003) is the light projection of one immutable
`raw_record`: a name, a place, maybe a domain or a phone, a `dedup_key` and a `duplicate_of_id`.
Immutable and permanent, like the raw record it projects. It carries no business id and no score.

**Why.** Phase 04 must find references without claiming they are leads. Keeping them out of
`businesses` means entity resolution (phase 06) starts from evidence instead of from guesses already
written into the canonical table.

**Consequence.** A repeat is linked, never merged, and never across campaigns: two campaigns that
find the same business each keep their own candidate with their own lineage.

## D-029 · An imported file travels once and is stored as an artifact (phase 04)

**Decision.** The operator picks a file with the WebView's own file input; its text travels in one
IPC frame (at most 4 000 000 characters), and the worker writes it under `imports/<source run>/`
before the run starts, recording it as an `acquired_page` with its sha256 and size. Only the path is
kept in the run's configuration.

**Why.** No new dependency or capability for a file dialog, the bytes a candidate came from stay on
disk next to it, and a paused import resumes by row offset without the text travelling again.

**Trade-off.** A file larger than the cap is refused with an explanation instead of being truncated.
A path-based connector for bigger imports is a later addition; the ceiling is stated in the UI.

## D-030 · One table is both the frontier and the fetch log (phase 05)

**Context.** A crawl needs a frontier that survives a restart, a record of every URL it decided
about (fetched, skipped by a policy, failed), and metrics.

**Decision.** `crawl_urls` (migration 0004) holds one row per URL per site run. A row is `queued`
until it is decided, then final: a trigger refuses any change after that and any deletion.
`UNIQUE (source_run_id, url)` plus "one URL, one transaction" (its acquired pages, raw record, row and
discovered URLs together) make "never fetch or store twice on resume" a property of the database.
Bytes stay in `acquired_pages` and parsed content in `raw_records`, as phase 03 designed; the row adds
method, timings, redirects, allowed headers, fallback reason and cost, and hashes.

**Consequences.** Metrics are aggregated from the rows on every read instead of being kept on the
side, so they are identical during a run and after a restart. Skipped URLs are recorded, not dropped,
up to 400 per site; the links beyond that stay in the page's raw record.

## D-031 · The parsed page is the raw record; the bytes are the artifact (phase 05)

**Decision.** A fetched page is stored as an `acquired_page` whose body is a content-addressed file
(`artifacts/web/<sha[:2]>/<sha>.html`) and a `raw_record` whose payload is the deterministic parse of
those bytes: title, metadata, headings, links, visible emails and phones, JSON-LD, main text and the
signals behind any fallback, with the parser's name and version.

**Why.** Later phases need queryable content with provenance (D-018), and re-running the same parser
version over the stored bytes reproduces the record exactly. Identical bodies are stored once on disk;
identical visible text within a site is linked through `duplicate_of_id` instead of stored again.

## D-032 · robots.txt by RFC 9309, not `urllib.robotparser` (phase 05)

**Decision.** A small parser in `crawl/robots.py` applies the longest match (`Allow` wins ties), `*`
and `$`, and the product-token group over `*`. Fetch outcomes follow RFC 9309: a 4xx means no rules;
5xx, 429 or a network failure means nothing is allowed and the site stops. robots.txt is not a
setting.

**Why.** The standard library applies the first matching rule, so `Allow: /` before
`Disallow: /admin` would let the crawler into `/admin`. Being wrong in the permissive direction is
exactly what "respect robots.txt" rules out.

## D-033 · SSRF is checked at connect time, on the address actually used (phase 05)

**Decision.** The httpx transport's connection pool uses `guard.GuardedBackend`: it resolves the host,
refuses the connection if any address is not public, and connects to the checked address. Proxies from
the environment are ignored. The browser checks every request with the same rule.

**Why.** Checking a URL before handing it to a client leaves a window in which DNS can answer
differently (rebinding), and a redirect or a page's own requests can point anywhere. The connect step is
the one place every path goes through.

**Trade-off.** It replaces a private attribute of httpx (`transport._pool._network_backend`). The SSRF
tests connect to a real local server with the guard on, so an httpx upgrade that moves it fails loudly.

## D-034 · Browser only on verifiable criteria, never against a challenge (phase 05)

**Decision.** Chromium renders a page only when `browser = always`, or when the HTTP page is an empty
shell, says JavaScript is required, or refuses plain HTTP clients. The measurements are stored with the
page. A CAPTCHA or bot challenge stops the site and is never rendered. Playwright's own Chromium is
preferred; the Chromium-based Edge or Chrome of the machine are the fallback, so no browser download is
needed on Windows.

**Why.** Rendering costs a hundred times a plain fetch and must be explainable page by page; rendering a
challenge would be working around a block.

## D-035 · Chromium cannot outlive the worker: a job object from Python (phase 05, supersedes the D-007 note)

**Context.** D-007 planned for the desktop core to put the worker tree in a kill-on-close job object
when Chromium arrived. The core forbids `unsafe` code, and the job object needs Win32 calls.

**Decision.** Before its first browser launch, the worker creates a job object with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and assigns itself to it (`ctypes`); the handle is never closed.
The Playwright driver and every Chromium process inherit it, so when the worker process ends - by
shutdown, crash or `TerminateProcess` - Windows kills them. Pause, cancel and shutdown also close
contexts and the browser explicitly.

**Why.** It works the same with the development launcher and the packaged sidecar, needs no dependency
and no `unsafe` in Rust, and is proven by a test that kills a worker outright and checks that its
Chromium processes are gone.

## D-036 · One record per candidate; a business is a root and the records merged into it (phase 06)

**Context.** Merges must keep every member and be reversible. Observations reference a
`business_id`; moving them on a merge would rewrite their history and make an unmerge guess which
observations go back.

**Decision.** Each discovery candidate becomes one record business (`entity_records`, immutable).
Records of the same entity point at the root through `businesses.merged_into_id`, always the root
(flat clusters); status `merged` hides a record from lists and search results show its root. No
observation ever moves: the root's detail reads the observations of all its records. Unmerging
changes two columns and restores the status the record had.

**Why not a separate entity table.** Phase 03 already modelled `merged` and `merged_into_id`; a
second table would duplicate identity, lists, search and snapshots.

**Consequences.** One business row per candidate. Lead stage, notes and tags live on the root, and
a later merge does not move them yet (phase 12 must follow merges).

## D-037 · Pure, versioned normalisers, applied again when comparing (phase 06)

**Decision.** Normalisers are pure functions with a `NORMALIZER_VERSION` that stamps the extractions
that write observations. Matching normalises the raw values of observations again with the running
build, so a comparison never mixes versions. A new version adds observations through new extractions;
older ones stay as history and a current-extraction filter hides them from the business.

**Trade-off.** A subset of the Public Suffix List and NANP-only phone validation keep the code small
and dependency-free; both are stated where they apply.

## D-038 · Explainable weighted rules with safety rules, not a trained model (phase 06)

**Context.** There is no labelled production data, and every decision must be explained.

**Decision.** Each agreement or conflict adds a named log-odds weight; the score is the logistic of
the total; `auto_merge ≥ 0.97` with two agreeing identities (one of them the name or a stable source
id) and no conflict, `manual_review ≥ 0.25`. Safety rules can only add caution: a shared phone alone,
same brand at another location, an address change, generic names and different states never merge
automatically. Every weight and rule is stored with the pair and the rules version.

**Why.** A sum of named weights is explainable line by line; a model trained on synthetic data would
learn the generator, not businesses. Measured on the labelled and the generated corpora: automatic
precision 1.0, and every true pair of the labelled corpus merged or sent to review.

## D-039 · Blocking with a block-size cap instead of global comparison (phase 06)

**Decision.** Exact keys (domain, E.164 phone, stable source id) and candidate keys (distinctive name
token within a city or ZIP, full name, street, email domain, coordinate cells); a block of more than
250 records is skipped and counted.

**Trade-off.** Two records reachable only through an oversized block are never compared — a
switchboard shared by a franchise, "Main St" — which are exactly the pairs the rules would not merge.
At 30,719 records the engine compared 55,093 pairs instead of 471 million.

## D-040 · Clusters are recomputed from stored pairs, people first (phase 06)

**Decision.** A run and a decision share one path: stored pairs → union-find with people's merges
first and automatic merges by score, separations (a person's "keep separate" and hard conflicts)
refusing any chain across them → the differences with `businesses` written as audited merges and
unmerges with `business_merges` history. Undo reverts the latest decision on a pair. A later run
refreshes the evidence of a pair a person decided but never its status.

**Trade-off.** A decision recomputes the whole project: a second at tens of thousands of records.
Recomputing only the affected components is the upgrade path if projects grow to millions.

## D-041 · Canonical values by source trust, then confidence or freshness; conflicts counted (phase 06)

**Decision.** For each field of a cluster the most trusted source wins, then confidence (freshness
first for a location), then the oldest id. The choice, its method (`policy` or `manual`), reason and
the number of different values in the cluster are recorded in `canonical_values`, superseded rather
than overwritten. Lists show how many fields disagree; the inspector shows every value and lets a
person choose another, which later runs keep.

**Why.** "Last write wins" would let the newest import overwrite a registry; hiding the alternatives
after a merge would hide the conflict the prompt says must stay visible.

## D-042 · Resolution is a job: batched ingest, recomputed comparison, idempotent apply (phase 06)

**Decision.** `jobs.kind = resolution`, one open run per project. Ingest runs in batches of 200
candidates with a checkpoint cursor; comparison is recomputed in memory on every run and checks for
pause and cancel every 2,000 items; applying pairs, clusters and values writes only differences.
Decisions are refused while a run is working on the same project; a run left running by a dead
worker is paused at startup.

**Why.** Only ingest writes new rows that must not repeat; the rest is deterministic and cheaper to
recompute than to checkpoint.

## D-043 · The review queue is a view of Businesses, not a new module (phase 06)

**Decision.** Businesses toggles between the canonical list and Possible duplicates. The navigation
registry keeps the 16 modules the product defined; the queue belongs to the businesses it resolves.

## D-044 · A business is an establishment; no organization entity yet (phase 06)

**Context.** The prompt asks for `organization` and `location` entities if the model needs to tell a
brand, a company and a branch apart.

**Decision.** A business is one establishment. Branches and franchise locations stay separate
businesses (`same_brand_different_location`), and what relates them is kept as a stored distinct
pair with its rule and evidence. Places are `business_locations` observations with their time, so a
move is history of the same business. No `organizations` table.

**Why.** Nothing before phase 11 works at brand level, and a brand grouping inferred from a shared
domain or phone is exactly the kind of heuristic that must not become a fact.

**Consequences.** When a phase needs company-level rollups, `organizations` can be built from the
stored `same_brand_different_location` pairs without touching records or merges.

## D-045 · Ownership and market affinity are separate tracks; names and places are never read (phase 07)

**Context.** Spanish on a website, a Hispanic clientele or a Spanish surname are routinely used as
proxies for Latino ownership. They are not evidence of who owns a business, and using them would
turn a stereotype into data.

**Decision.** Every claim belongs to one track: ownership, market or contradiction. Only ownership
evidence can produce an ownership status; market signals produce at most `spanish_market_affinity`,
whatever their number or source. The extractors never read names, people, photos, addresses,
nationality or ethnicity columns: the payload scanner opens only ownership, certification, directory
and language columns, and pages are read for explicit phrases.

**Consequences.** Most businesses stay `unknown`, which is the honest answer. A test proves that no
combination of market signals from any kind of source reaches ownership, and that names, surnames,
photos and places never raise a status above `unknown`.

## D-046 · Transparent, versioned rules instead of a model (phase 07)

**Decision.** A score is strength × source reliability × freshness; independent sources combine as
`1 − Π(1 − score)`; tiers are named rules (verified needs strong, fresh evidence about the location
from an official or registry source; high confidence a trusted strong source or two independent
ones; probable anything else). Every value lives in a versioned `Ruleset`, and each assessment stores
the factors of every piece of evidence it cites.

**Why.** There is no labelled ground truth, and every result must explain itself line by line. A
self-declaration on the business's own website is explicit but not independently confirmed: it
reaches high confidence and carries `self_declared`, never `verified`.

## D-047 · Evidence from versioned extractions of acquired data; assessments are superseded, never edited (phase 07)

**Decision.** Evidence is written through extractions (`latino.payload`, `latino.site`,
`latino.model`) over raw records, at most one item per claim, extraction and business, with the
sentence that shows it and its hash. Only its status moves (`stale`, `revoked`, `contradicted`),
each move audited with a reason. A run writes a new assessment only when status, confidence, rules
version, cited evidence or warnings differ, and supersedes the previous one; migration 0006 allows one
current assessment per business **and method**, stores warnings and limitations in `details_json`,
the per-evidence factors in `latino_assessment_evidence`, and makes the citations immutable.

**Consequences.** Recalculating with new rules or new evidence keeps the full history, and a second
run over the same data writes nothing.

## D-048 · A person's review coexists with the calculation and is bounded by the evidence (phase 07)

**Decision.** A review is a `manual_review` assessment with a mandatory reason in `review_decisions`
and the id of the calculation it reviewed. The business shows the review; the rules keep
recalculating underneath, and a recalculation flags the review as outdated. A person cannot give a
status the evidence cannot reach at all (ownership without ownership evidence, `verified` without
strong ownership evidence, affinity without market signals). A review can be withdrawn, with its
reason.

**Why.** People catch what patterns miss, but a review must not become a back door for the
proxies D-045 excludes, nor erase what the rules concluded.

## D-049 · A model points, the rules decide (phase 07)

**Decision.** Model output about Latino ownership is only a candidate: a claim plus the literal quote
from an acquired page. It becomes evidence only if the quote is in the page and the extraction
patterns read that quote as the same claim; the evidence links to the inference run. The model never
adjudicates ownership and never browses.

**Why.** The prompt allows a model to help find declarations, not to decide them. Verifying against
the fragment keeps hallucinated or paraphrased declarations out of the store.

## D-050 · A website several businesses list is organizational evidence (phase 07)

**Decision.** A crawled page speaks for the business whose candidate asked for the crawl and for
every canonical business whose website is on the same registrable domain. When all of a business's
ownership evidence is shared that way, its status is capped at probable with
`location_ownership_unknown`.

**Why.** A franchise or brand declaring Hispanic ownership says who owns the brand, not each location
(D-044 keeps locations separate).

## D-051 · Evaluation is a job; engines without source runs share one background runner (phase 07)

**Decision.** `jobs.kind = latino_evidence`, one open run per project, stages extract → lifecycle →
evaluate with batch checkpoints. The thread, control, recovery and shutdown code that resolution had
on its own moved to `jobs.BackgroundJobs`, together with checkpoint, progress and finish helpers;
resolution and Latino evidence now only say how to plan, run and show their jobs.

**Trade-off.** One more generic class in `jobs.py`, in exchange for not keeping two copies of the
same concurrency code. Filters on Businesses use the kind of source that counted rather than a
connector key, so they need no source catalogue and read the same across projects.

## D-052 · An audit reads one stored crawl and parses its HTML again with its own extractor (phase 08)

**Context.** The crawler's parsed snapshot keeps what acquisition needs (title, meta tags, headings
up to h3, links, JSON-LD, main text), not forms, scripts, images, frames or every heading.

**Decision.** A website audit reads the latest finished crawl of each site in a project and parses
the HTML the crawler stored again with `website/extract.py`, versioned on its own
(`EXTRACTOR_VERSION`). It never goes back to the network, and the crawler is unchanged.

**Consequences.** Audits can be repeated offline and do not depend on how a site answers today. A
page whose stored HTML is gone is a gap in the coverage, never a failure. What the crawler does not
keep (security headers, hosting) is not judged.

## D-053 · Deterministic rules; unknown and not applicable never lower a score (phase 08)

**Decision.** 58 rules in a versioned `Ruleset`, each with a category and a severity that is its
weight (critical 10, high 6, medium 3, low 1, info 0). A subscore is the weighted share of credit
(pass 1, warning 0.5, fail 0) over the rules that could be evaluated, with its coverage and
confidence; `unknown` and `not_applicable` are left out, and a category with nothing evaluated has
no score. There is no overall score. What is not detected is reported as not detected: measurement
rules never fail, and advertising and call tracking are informational.

**Why.** The prompt forbids treating unknown as failure and concluding absence from a missing pixel.
A single number would hide how much could not be evaluated, and the commercial score belongs to
phase 11. No model takes part in any finding.

## D-054 · The snapshot is stored with its audit; audits are history (phase 08)

**Decision.** Migration 0007 rebuilds the four phase-03 website tables, which nothing had written,
around a project, a crawled site and the crawl it read. `snapshot_json` keeps every fact the rules
read; findings, technologies and conversion elements carry evidence references (URL, crawl URL,
field, fragment) instead of `evidence_items`, which belong to a business. Rows are immutable and
permanent. At most one audit exists per site, crawl, extractor version and ruleset version; newer
rules recalculate from the stored snapshot into a new audit, and every audit stores its difference
with the previous audit of its site.

**Trade-off.** Snapshots take space (a few kilobytes per page) in exchange for audits that can be
reproduced and compared long after the artifacts are gone. Audits belong to crawled sites, not to
businesses: the phases that need a business's audit can link them as Latino evidence links pages
(candidate and registrable domain).

## D-055 · Lab measurements from the crawler only; never Core Web Vitals (phase 08)

**Decision.** Response and render times, HTML size, scripts and images come from the crawl, with the
tool, device profile and time of each measurement. Every audit notes that there is no field data and
no Core Web Vitals, and no rule estimates them. Lighthouse and PageSpeed Insights are not
integrated.

**Why.** Lighthouse needs Node and a browser profile the Python sidecar does not ship (packaging is
phase 14); PageSpeed Insights sends every address to a third party and needs a key; inventing Core
Web Vitals is forbidden. A later integration would add its own lab and field sections next to these
figures.

## D-056 · Technologies by documented signatures, with the confidence of their trace (phase 08)

**Decision.** About 70 signatures match what a page itself carries: script and iframe addresses,
generator tags, inline script, markup and links, each trace with its own confidence (0.95 down to
0.7). Hosting is not guessed.

**Why.** Deterministic, testable and documented, with no external fingerprint database to license,
package or keep updated. Tags loaded inside a tag manager or consent tool stay invisible, which is
why not detected never means absent.

## D-057 · Website audits are a job; the interface is a view of the Research Center (phase 08)

**Decision.** `jobs.kind = website_audit` on `jobs.BackgroundJobs`: one open run per project, one
transaction and checkpoint per site, and an optional list of sites to audit again within one
project. Five IPC messages; the desktop core refuses a detail whose subscores do not count exactly
its findings. The Research Center switches between Acquisition and Website intelligence, and runs
appear in Jobs. Rule titles, conditions and remediations are English text of the ruleset;
categories, results, severities, gaps and notes are translated.

**Trade-off.** Recommendations stay in English until the ruleset gains translations, in exchange
for rule texts that are versioned with the rules that produce them.
