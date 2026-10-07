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

## D-058 · A contact status says exactly what its check proved (phase 09)

**Context.** The prompt forbids calling a contact validated when only its syntax passed, and forbids
SMTP probing, calls, messages and form submissions.

**Decision.** Eight statuses with fixed meanings (contact-intelligence.md section 3): `observed`,
`structurally_valid` (well formed only), `domain_valid` (the domain says it accepts mail),
`likely_valid` (independent evidence agrees, or a person confirmed it), `invalid`, `unknown`,
`stale` and `suppressed`, decided most restrictive first by `rules.channel_status`. Catch-all is
always `not_checked`. Numbers in the fictional 555-0100 to 0199 range stay well formed with a signal
and never become likely valid.

**Why.** No check available without contacting someone proves that a person answers; the words say
only what was checked. A domain that accepts mail says nothing about one mailbox.

## D-059 · Phones by libphonenumber; mail domains by DNS only, cached, bounded and opt-in (phase 09)

**Decision.** `phonenumbers` (the libphonenumber port) parses numbers with an explicit `+` or the
country of the business, never guessing one. `dnspython` asks MX, then A/AAAA (implicit MX, RFC
5321), and recognises null MX (RFC 7505), with a 3 s timeout. Answers are cached in
`mail_domain_checks`: MX 7 days; null MX, no mail and NXDOMAIN 1 day; timeout and error 1 hour. A
failed lookup never replaces an answer the domain gave. A run looks domains up only when asked
(`check_mail_domains`) and the worker is not offline (`LEAD_DISCOVERY_OFFLINE=1`), at most 1 000 per
run; reads use the cache only.

**Trade-off.** Two dependencies instead of hand-written patterns that would be wrong on
international numbers and domain edge cases. Lookups tell the system resolver which domains are
being researched, which is why they are opt-in.

## D-060 · Channels are consolidated per canonical business and decided again on every read (phase 09)

**Decision.** Observations are never edited: a channel is every live observation with the same key
in a root business and the records merged into it. Runs and actions write the decided status, its
method and a `contact_validations` row to every observation; reads decide it again from what is
stored, with cached mail answers only, so a suppression, a merge or a review shows at once. An
observation from a crawled page stops being current when a newer crawl of that page no longer shows
it, and the channel becomes `stale`.

**Trade-off.** A contact list decides every channel of its project on each page. Fine for the
thousands of channels a project holds today; the store notes where to materialise them.

## D-061 · People only from published roles; hypotheses are kept apart by the schema (phase 09)

**Decision.** `decision_makers` is rebuilt (nothing had written it) with the relationship the
published role states and a review status. A person is a name published next to a decision role, or
a structured-data `Person`; a name is never used to infer a role, an origin or anything else, and
never feeds Latino evidence. Doubtful people (a founding role, roles that disagree, a shared site, a
name inside the business name) wait for a person's decision. An email pattern is taken only from a
published personal address on the business's own domain that matches a published name; the addresses
it builds for other published people are `hypothesis` rows that triggers keep `unknown`, `invalid` or
`stale` and never primary. They are never confirmed, never counted, left out of lists and exports
unless asked for, and withdrawn when the business publishes the address or the person is rejected.

**Why.** The prompt forbids inventing people and presenting guesses as contacts; enforcing it in the
schema holds for any future writer, not only this engine.

## D-062 · The do-not-contact list is local, permanent and applied three times (phase 09)

**Decision.** `contact_suppressions` holds emails, E.164 phones, registrable domains and businesses,
each with reason, source (`manual`, `import:<file>`), actor and date; an entry is lifted once, with
a reason, and never deleted (triggers). It is applied (1) at once to the observations it covers
(`do_not_contact`, status `suppressed`, history), (2) by the queries: the business list's phone and
every contact read and export check live entries again, and (3) by the desktop core, which refuses a
channel whose status and suppression disagree and a hypothesis that reads as a contact. Lifting an
entry returns what it alone covered to `observed`. Import reads `kind,value[,reason]` with a header,
at most 5 000 rows (1 000 000 characters in the core), and reports every unusable line; export
neutralises cells a spreadsheet would run, and the core checks every cell again.

**Why.** One flag can be missed by a future query; three independent guards make a suppressed
contact hard to leak into a screen or an export.

## D-063 · Contactability is its own subscore; contact checks are a job and a Research Center view (phase 09)

**Decision.** `round(100 × Σ weight × value / Σ weight)` over availability 25, validation 30, role
15, freshness 15 and source 15, counting only published or imported channels that are not invalid,
suppressed or fictional; hypotheses never count, a suppressed business has no score, and every
component says what it read. It is not the lead score (phase 11). `jobs.kind = contact_intelligence`
on `jobs.BackgroundJobs`: one open run per project, an extraction stage (checkpoint per 100 pages)
and a validation stage (checkpoint per 200 businesses), both idempotent. Twelve IPC messages. The
Research Center gains a Contacts view (channels, people to review, do-not-contact list), the
business inspector a contact panel, and runs appear in Jobs.

**Trade-off.** The weights are a documented, versioned judgement (`CONTACTABILITY_VERSION`), not a
fitted model; changing them is a new version, never a silent edit.

## D-064 · A local model proposes, never decides: six task contracts checked before anything is kept (phase 10)

**Context.** The prompt asks for local inference where it adds value (semantic extraction,
controlled classification, synthesis of deterministic findings, commercial explanation, language,
screenshots) without making the model the orchestrator, replacing deterministic analyses or
presenting its output as fact.

**Decision.** Six tasks (`evidence_extraction`, `business_classification`, `finding_synthesis`,
`opportunity_reasoning`, `language_detection`, `visual_review`), each with a strict Pydantic output
(`domain.*Output`, extra fields refused, controlled vocabularies), a version, a schema version, a
prompt template file with its own version, input limits, a timeout, a temperature (0 or 0.2), a
fixed seed and one failure strategy: one repair turn with the errors, then `invalid_output`. An
answer is kept as valid only when it parses, follows the schema, cites only references it was given
(of the kinds the task reads), says nothing else when it answers `insufficient_evidence`, quotes its
fragment literally (extraction) and writes no number absent from the cited evidence (synthesis and
reasoning). Jobs decide what is asked; deterministic engines keep deciding facts. The only path from
an answer to evidence is the phase 07 one: Latino declarations an extraction proposes go to
`latino.store.ingest_model_candidates`, whose rules accept them only when the quote is in the page and
reads as that claim. There is no score.

**Why.** Small models on 8 GB answer wrongly often enough that only checks the code can enforce make
their output usable; the checks are deterministic, cheap and testable without a model.

## D-065 · Ollama by default behind a small provider protocol, on loopback only; LLM Fit as an outside opinion (phase 10)

**Decision.** `ollama.Provider` is what the layer needs (probe, show, loaded, chat with a JSON
schema, pull, delete, unload); `OllamaProvider` implements it over the loopback API with
`trust_env=False` and a timeout per call. A llama.cpp server maps onto the same protocol and is not
built yet. The runtime URL must be `127.0.0.1`, `localhost` or `::1` (worker settings and desktop
configuration both refuse anything else). The model never receives tools, its answers are only parsed
as JSON and validated, and nothing it writes is executed or fetched. Models are downloaded or
deleted only through `ai.model_change`, whose confirmation must repeat the exact name, never while the
worker is offline (pulls) and never while the model is in use (deletions). LLM Fit runs only when the
operator asks, read-only (`recommend --json --runtime llamacpp --no-dashboard`), and its report is
shown as an external opinion.

**Trade-off.** One runtime implemented. The protocol keeps a second one to one class.

## D-066 · Every inference is auditable, and a valid result is unique per cache key (phase 10)

**Decision.** Migration 0009 rebuilds the reserved AI tables (nothing wrote them). `ai_inference_runs`
records task and version, business, project and job, provider, model, digest, profile, parameters
(with any documented reduction), template and schema versions, the exact minimal input (`input_json`:
facts and referenced fragments, findings or subscores, never HTML) and its hash, a deterministic cache
key (task, template and schema hashes, runtime, model digest, parameters, input), status, device,
calls, tokens, duration, error and a person's review. `ai_inference_outputs` keeps every raw answer
next to what it validated to or why it was refused, immutable. A partial unique index allows one
valid, not rejected result per cache key: a repeated request or a restart reuses it instead of asking
again. The request columns are fixed by trigger and runs are never deleted. `ai_inference_evidence` is
dropped: most inputs are not evidence items, and the references live in the run. A review
(`ai_output_accepted`, `ai_output_rejected`, `ai_review_withdrawn`) needs a reason, only applies to a
valid answer, and never turns it into a fact elsewhere; a rejected answer stops being reused.

## D-067 · One GPU queue, a circuit breaker and memory-aware placement for an 8 GB GPU (phase 10)

**Decision.** One queue runs one inference at a time, by priority (a person, then project jobs, then
benchmarks), keeping the loaded model's work together, with a capacity of 64 (beyond it a submission
is refused) and cancellation by owner. Before loading a model the runner estimates VRAM from the
model's metadata (weights + f16 KV cache for the context + 512 MiB + 5 %): a model already loaded
runs as is; otherwise other loaded models are unloaded only if the new one does not fit next to them;
a model that does not fit gets its context halved down to 2 048 tokens, then runs partly on CPU if
its profile allows it, or is refused (`insufficient_vram`). An out-of-memory answer halves the
context, then moves to CPU, at most two steps. The breaker opens after three runtime failures in a
row (unreachable, timeout, 5xx), refuses calls for 30 s doubling up to 5 min, then lets one call
through. An AI job whose breaker opens pauses itself (`runtime_unavailable`) instead of looping; at
startup queued inferences are cancelled and running ones failed (`worker_restarted`). While the queue
works and free RAM or VRAM is below the configured limits (1 024 and 512 MiB by default), each crawl
URL waits, at most 120 s, and the wait does not count against the site's time budget.

**Trade-off.** One inference at a time leaves throughput on the table on larger GPUs; on 8 GB it is
what keeps two models and their KV caches from colliding.

## D-068 · Models are recommended by a local benchmark on a labelled synthetic corpus (phase 10)

**Decision.** `ai.benchmark` runs installed models over 15 invented cases with expected answers (5
tasks; visual review has no labelled screenshots): verticals in English and Spanish, a prompt
injection that asks for another vertical, pages too thin to answer, ownership declarations,
languages, audit findings with numbers and reasoning cases. Each model and task gets one immutable
`ai_benchmark_results` row: correct and valid answers, failures, p50/p95 latency, tokens per second
(generation time), peak VRAM used and lowest free RAM, device. A recommendation per task is the most
accurate installed model with the same digest whose answers validated at least 80 % of the time, then
the fastest; it is labelled a recommendation and never assigned. No model is assumed installed: every
profile starts without one.

## D-069 · AI analyses are jobs; the AI Models screen and the inspector panel (phase 10)

**Decision.** `jobs.kind = ai_analysis` on `jobs.BackgroundJobs`: one job per project (or per group of
listed businesses), identical open requests listed instead of duplicated, checkpoint every 10 units,
one aggregated `batch_finished` event every 25 (the UI polls, the worker never pushes), unavailable
tasks and inputs without content counted as skipped. Ten IPC messages (`ai.status`, `ai.configure`,
`ai.start`, `ai.control`, `ai.runs`, `ai.inferences`, `ai.inference`, `ai.review`, `ai.benchmark`,
`ai.model_change`), mirrored in Rust, where the core also refuses an answer citing an input it was
never given and settings or downloads with invalid names or bounds. The AI Models module shows the
runtime, GPU, live resources and queue, installed models with estimates, profiles and tasks with the
reason each is unavailable, benchmarks with recommendations, the LLM Fit opinion, downloads and
deletions with typed confirmation, and the inference history with raw and validated answers and
reviews. The business inspector gains a Local AI panel; AI runs appear in Jobs.

## D-070 · Separate components, gates before weights, unknown left out instead of zero (phase 11)

**Context.** The prompt asks for independent components, hard gates before weights, `unknown` that
is never a failure, no penalty where a signal does not apply, and coverage that lowers certainty
rather than the opportunity.

**Decision.** 24 features in five base components, each known (a value that rests on evidence),
unknown or not applicable. Base components, ICP_MATCH, OPPORTUNITY_SCORE and FINAL_LEAD_SCORE are
weighted means over their known inputs only, renormalized, with contributions that add up to the
value. Gates come first: do not contact, closed, exclusions, the Latino requirement, an observable
problem an offer solves, and must-have criteria. A failure excludes the business without a total; an
undecided gate, or less than `min_known_weight` of the final weight known, gives `insufficient_data`.
Confidence is data confidence × the known share. A feature stops applying through the model's
category rules (booking for roofers) and through the offers: a problem no offer solves is not
weighed. LATINO_CONFIDENCE is its own component, not an input of OPPORTUNITY_SCORE, weighted 0 by
default; campaigns filter with the Latino gate (`verified_latino_owned` or `market_affinity`).

**Why.** Renormalizing keeps an unknown from reading as a failure, while the known share, the tier
and the confidence keep a thin score from passing for a certain one.

**Trade-off.** Two equal totals can rest on very different evidence; coverage and confidence are
shown next to every total and break ties.

## D-071 · Declarative, versioned definitions over a pure engine; the facts are stored with every score (phase 11)

**Decision.** `IcpDefinition` and `ScoringDefinition` are contracts stored verbatim in immutable,
permanent versions (an unchanged definition makes no version); a profile or model has an active
version and an archive mark, changed only with an audited reason, and a definition with errors is
never activated or run. `model.score(facts, icp, scoring, versions)` reads no store, clock, network
or model, so the same inputs give the same scorecard. `BusinessFacts` (`FACTS_VERSION`) is read once
per business and stored with its scorecard in `business_scores`; a run can score the stored facts of
a finished run again (`facts_from_run_id`), which compares two versions on identical facts. Runs,
scores, components and outcomes are never rewritten or deleted (triggers). Migration 0010 rebuilds
the reserved scoring tables: `score_explanations` and `explanation_kind` are dropped (reasons, gates,
risks and their evidence live in the scorecard), `score_runs.dataset_snapshot_id` is dropped (each
score keeps its facts), `lead_tier` and `lead_outcomes` are added.

**Trade-off.** About 30 KB of facts and scorecard per business per run. History grows with every run;
scoring is cheap enough (20 businesses in 0.06 s) that nothing is cached instead.

## D-072 · One ranking with a documented tie-break, identical in Python and SQL (phase 11)

**Decision.** Tier (priority, review, low, insufficient data, excluded), total descending with a
missing total after, confidence descending, business id. `model.rank_key` and `store.RANK_ORDER`
(`ROW_NUMBER()` over the whole run) express the same order, and a test proves it. A rank is the
place in the whole run: filters and keyset pages never renumber it, and the core refuses a page out
of rank order or outside its filters.

## D-073 · Heuristic weights declared as such; offline calibration with a leakage guard; no training (phase 11)

**Context.** No outcome exists yet, and the prompt forbids training predictive models without volume
and labels.

**Decision.** Every model version is stored with `is_heuristic = true`, and the Scoring Models
screen, the scorecard and the calibration report say so. Outcomes (the ten stages, `lost` with its
reason) move `lead_stage` along its state machine and stay in `lead_outcomes`. Calibration of a run
labels each business with its latest outcome that happened after its facts were read; earlier ones
are counted as leakage and never used. The metrics are:

- precision@K for 10, 25, 50 and 100;
- lift by decile;
- calibration bands;
- conversion by tier;
- coverage.

Warnings appear below 30 labels. Nothing fits a weight.

**Why.** A model fitted on a handful of labels would turn noise into apparent certainty, which the
prompt names as the thing not to do.

## D-074 · A model may word a reason, never score one (phase 11)

**Decision.** The scorecard detail shows a local model's `opportunity_reasoning` problems (phase 10)
only when every reference cited is a website finding the scorecard's features rest on; the rest are
dropped and counted, and the core refuses an AI reason citing anything else. No model output changes
a number, a tier or the evidence. Reasons, inferred impacts (marked as inference), the offer and the
message draft are deterministic templates over the observed problems; the draft is in English, marked
as a draft, and nothing sends it.

## D-075 · Scoring is a job; ICP Builder, Scoring Models, Lead Explorer and the scorecard panel (phase 11)

**Decision.** The engine:

- runs as `jobs.kind = lead_scoring` on `jobs.BackgroundJobs`, with one open run per project;
- works in batches of 200 with a checkpoint after each and skips a business already scored;
- serves 13 IPC messages (`scoring.*`), mirrored in Rust with validation;
- simulates in memory over at most 5 000 businesses read now, or over a run's stored facts, and
  writes nothing.

The interface:

- **ICP Builder:** criteria, Latino requirement, exclusions, offers, preview on real data, versions
  and archive.
- **Scoring Models:**
  - _Models:_ the formula as text, weights, thresholds, category rules and versions (save, save as
    new, compare field by field, activate, archive and restore with a reason).
  - _Runs:_ start, control, distribution and comparison.
  - _Calibration:_ the offline metrics of a run.
- **Lead Explorer:** the ranked leads of the newest finished run, filtered in the store.
- **Business inspector:** a lead score panel with review and outcomes.

Scoring runs appear in Jobs.

## D-076 · A lead index projected from stored scores; windows of one bounded, parameterised query (phase 12)

**Context.** The Lead Explorer must page 100 000 to 1 000 000 leads without loading them, and a
scorecard is about 30 KB of JSON that no filter should read.

**Decision.** `lead_index` holds one row per business per score run with the columns the explorer
filters and sorts on, projected from the stored facts and scorecard:

- written with the scores, and ranked when the run finishes in the order of D-072;
- projected on first read for runs scored before phase 12;
- dropped by trigger when a business is soft-deleted.

A query is a tree of conditions joined by `and` or `or`, optionally negated, plus a quick search over
the full-text index, up to four sort keys and a window. The tree is bounded: depth 3, 40 conditions,
20 children per group, 50 values per list. A window is at most 500 rows, at an offset of at most
5 000 000. The query compiles to one parameterised statement:

- live fields (stage, research status, assignee, tags, do-not-contact, age of the facts) are joined
  or computed only when a filter or a sort needs them;
- a window's rows get their live state in one more query over those rows alone;
- the total is counted only when asked for.

The same compiler resolves the leads of a bulk action, the export preview and comparisons.

**Why offsets.** A virtualized grid addresses any row (End, the scrollbar), and a keyset cursor cannot
jump to row 999 900. A finished run's index never changes, so its windows are stable; only a live field
used as a filter can move a lead between windows.

**Trade-off.** The index repeats scorecard columns for every run: about 930 bytes a lead per run with
its indexes, and 1 086 MiB of store files with one million index rows (D-082,
[lead-explorer.md](lead-explorer.md)).

## D-077 · Saved views are versioned and upgraded when read; the working layout is a local preference (phase 12)

**Decision.** `saved_views` with immutable, permanent `saved_view_versions` that carry their
`schema_version`:

- saving an unchanged definition makes no version;
- archiving and restoring need a reason and are audited;
- a live name is unique.

Schema 1 (flat `and` conditions, one sort key, the visible columns) is upgraded when read and never
rewritten: its conditions become one `and` group, its columns stay visible (rank and name pinned) and
every other field is added hidden. The next save appends a version in schema 2; a schema newer than
the build is refused.

The explorer's working layout (filters, search, sort, columns and the active view) is kept in the
local preferences and restored after a restart. Unknown columns are dropped and new fields appended;
a layout of an older schema falls back to the default view.

**Why.** A view is shared, versioned work; what one screen shows at the moment is a preference.

## D-078 · A research status is a decision with a reason; sales-ready is a rule, never a bulk action (phase 12)

**Decision.** `lead_research` holds the current research status and assignee of a canonical business
and is never deleted. Every change is also a `review_decisions` row (`research_status`,
`research_assignee`) and an audit entry.

- `sales_ready` is refused unless the eight criteria of [lead-explorer.md](lead-explorer.md) hold at
  that moment. `needs_research` and `disqualified` need only their reason.
- Undo sets the previous status again as a new decision (`Undo: <reason>`): nothing is erased, and
  returning to sales-ready checks the criteria again.
- **Bulk actions** (add or remove a tag, assign or unassign, needs research, disqualify) are previewed,
  then applied with a reason: one audited decision per lead, sharing a batch id.
  - By id they take at most 1 000 leads.
  - Over a query they take at most 5 000, and apply only while the leads matching are as many as
    previewed.
  - `needs_research` skips leads on the do-not-contact list.
  - Sales-ready is not among them.

**Why.** Sales-ready means a person checked this lead against its evidence, which no bulk action can
do.

## D-079 · Checklists, tasks, pins and corrections are history; a correction is an observation, never an edit (phase 12)

**Decision.**

- **Checklists** are versioned definitions of automatic items, which read the stored facts, and manual
  items, whose ticks are immutable `research_checks` (unticking needs a reason). A lead gets the
  checklist that names a campaign one of its records came from, else one whose categories match the
  lead's, else the default "Sales-ready checklist": seven required items, three optional, created on
  first read.
- **Tasks** (missing data, checklist, refresh, custom) keep one open task per key. Syncing opens one
  for each weighted unknown of the score (up to 10) and each required item left undone. Dismissing
  needs a reason, a task can be reopened, and none is deleted.
- **Pins** mark any row a brief or a timeline cites and are unpinned once, with a reason; a pin whose
  row is gone says so.
- **Corrections** of a name, address, website, phone, email or category add an observation from the
  `analyst.corrections` connector, with the reason in its raw record, and choose it as the value
  shown (phase 06). The source rows are untouched; choosing another value undoes it.
- **Refresh** opens or reuses the task `refresh:<scope>` and starts one engine job:
  - pages: a crawl of up to five known website addresses;
  - contacts: a contact check of the project, without mail DNS lookups;
  - evidence: a Latino evaluation of the project.

  The job ids are recorded on the task. Nothing reaches the business.

## D-080 · A sales brief restates stored rows; a model may only reword its claims (phase 12)

**Decision.** `leads.brief` builds ten sections deterministically:

- each claim (B1, B2…, at most six per section) cites between one and eight stored rows;
- an inference (impact, offer, hook) is marked as one;
- an empty section gives its reason code;
- hooks are neutral questions for each observed problem, never verdicts;
- a lead on the do-not-contact list gets no contact route and is never worded by a model.

The `sales_brief` task (phase 10 pipeline, quality profile) receives only the brief's claims and must
cite claim ids. An answer is rejected (kept with its errors, never shown) when it:

- cites an id it was not given;
- repeats a section;
- writes a number that no cited claim contains;
- uses accusatory language (a fixed word list).

On read, the newest valid, not rejected answer shows only the sentences whose every cited claim is
still in the brief, matched by section and text; the rest are dropped and counted. Accepting or
rejecting it is a phase 10 review with a reason, and nothing is deleted. A `sales_brief` request must
name its businesses, and the analysis of a whole project leaves the task out.

**Why.** A claim the data no longer supports disappears with the data, and wording cannot introduce a
fact.

## D-081 · The funnel records how a lead was worked, never touching its score (phase 12)

**Decision.** `lead_outcomes` gains the channel, the attempt result, the contact quality, an optional
value with its currency, and notes.

- An attempt (`record_attempt`: channel and result required) keeps the stage.
- An outcome moves the stage along the state machine of phase 11, and `lost` needs its reason.
- Rows stay immutable. The core validates the pairing:
  - a review records no work;
  - an attempt names its channel and result;
  - a value comes with a three-letter currency;
  - notes stay within 4 000 characters.

Calibration keeps labelling by the stages recorded after a run's facts were read, so no run's facts
or scores are rewritten. Nothing sends a message.

## D-082 · The explorer reads indexes, never the rows of a run; the grid reads windows (phase 12)

**Context.** Profiling a million leads showed every window sorting the run's rows: 6.6 to 11 s a
window and 11.8 s for an export preview, while the worker's Python memory stayed under 1.5 MiB. The
first ordering term (`rank IS NULL`) kept SQLite off the rank index, and the run header counted tiers
and the distribution again on every window.

**Decision.** The read path:

- **Walked orders.** Without sort keys, a finished run walks `lead_index_by_rank`; its rank is unique,
  so it needs no tie-break. A single key on the name, or on the lead score descending, walks
  `lead_index_by_name` or `lead_index_by_total`.
- **Everything else.** A query with a condition or a search, or with any other order, is resolved in
  `lead_index_scan`: a covering index of every column a condition or a sort reads. The planner is told
  so with `INDEXED BY`, because without statistics it walked another index and read the table row by
  row. Only the window's ids come out of the index; its rows are then read by id.
- **Comparisons.** Names compare by their sort key (case, accents and spacing ignored), tiers sort by
  their order (best first), and only an ascending key on a field that may be missing asks for
  `NULLS LAST`, which no index can walk.
- **Live conditions.** Tags and the do-not-contact list are sets read once per query, never a
  subquery per lead. The export preview counts and warns in one pass.
- **Run header.** A finished run's header is read once per worker process.
- **Connections.** They map the store (256 MiB), cache up to 64 MiB and keep sorts in memory, so a
  sort no index holds does not spill to temporary files.

The grid:

- reads windows of 100 rows as it scrolls, keeps at most 12, and counts once per query;
- drops the answer to a superseded query, since a call to the worker cannot be cancelled;
- caps its scroll area at 8 000 000 pixels and maps the rows beyond it proportionally;
- keeps the selection by id in a session store, and "all matching" for the query that selected it;
- folds the inspector's sections, which read nothing until opened, and reads the next lead ahead
  once.

**Trade-off.**

- The covering index adds 279 bytes a lead per run to the table's 316 (measured in
  [lead-explorer.md](lead-explorer.md)).
- A deep window of an order no index holds still sorts the whole run's entries of that index.
- A filtered query always reads the covering index, even when its filter matches most leads and a walk
  would have stopped sooner.
- A sort may hold about 100 bytes per lead in memory.

## D-083 · A pipeline is a job that drives the engines' own jobs, never a second scheduler (phase 13)

**Context.** Phase 13 asks for a durable DAG from discovery to export eligibility with retries,
checkpoints, cancellation, resource limits and backpressure. Every engine already runs resumable jobs
with its own checkpoints, limits and cancellation (D-027, `jobs.BackgroundJobs`).

**Decision.** A pipeline is a `jobs` row of kind `pipeline` whose `job_tasks` are its ten stages.
When it is queued, each stage's version, inputs, outputs, retry policy and resources are written to
`pipeline_stage_specs` and `pipeline_stage_dependencies` and never change (triggers). A stage claims
atomically when its dependencies succeeded, no other pipeline of the project runs a stage, a slot is
free for every resource it uses and there is no RAM or disk pressure. An attempt attaches the engine
job already open for the project or starts one, and records it in `task_attempt_jobs` before waiting,
so a restart resumes that job instead of starting another. Pause, resume and cancel are passed on to
the engine job. Idempotency: `pipeline:<key>` is the job's unique idempotency key.

**Trade-off.** Stages of one project run one at a time even where they could overlap; the engines'
own per-project work is the unit, so a stage is as fine-grained as its engine.

## D-084 · Retry recoverable causes; a refusal becomes a dead letter a person resolves (phase 13)

**Decision.** `timeout`, `rate_limited`, `network`, `store_busy`, `store_unavailable` and
`worker_interrupted` retry with exponential backoff (at most 60 s) up to the stage's attempts. A
refusal by an engine (`configuration`, a `ValueError` or `LookupError`), an engine job cancelled
outside the pipeline and anything unexpected write a `dead_letter_tasks` row and fail the pipeline.
A person retries (one more attempt) or skips (the stage counts as done with nothing produced), always
with a reason; the dead letter keeps its failure.

**Trade-off.** An unexpected exception that a retry would have cleared waits for a person.

## D-085 · A snapshot is one moment with its quality report; retention never breaks lineage (phase 13)

**Decision.** A snapshot reads every row, the version and the lineage inside one SQLite read
transaction (its cut-off), writes DuckDB rows and Parquet partitioned by region with explicit types,
and registers `query_json`, `cutoff_at`, `pipeline_versions_json` and a quality report measured from
the Parquet copy (rows, duplicates, coverage and unknowns per field, statuses, tiers, score bands,
unscored, suppressed, stale rows, freshness). Schema 2 adds the score run, tier, total and
do-not-contact flag per row. A diff reads both Parquet copies and reports added, removed and changed
rows with the fields that changed. Retention keeps the newest N versions, every version an export was
written from and every version a kept one derives from; it is previewed, then applied with a reason.

**Trade-off.** Snapshots copy every business of the project: their size grows with the project, not
with a query.

## D-086 · Exports are local files written atomically with a manifest; the page never names a path (phase 13)

**Decision.**

- The desktop core opens the native save dialog and gives the page a one-time grant; `start_export`
  exchanges it for the path. The worker still checks the path: absolute, local, an existing folder,
  the format's extension, and not inside the app's data folder (except `exports/`).
- Rows stream in batches of 500 into `<file>.partial` (CSV with optional BOM, JSON, JSONL, XLSX
  written as a streamed zip, Parquet through DuckDB with explicit types), flushed, hashed with SHA-256,
  then renamed; the manifest is written the same way. A cancel, a failure or a restart removes the
  partial files.
- Do-not-contact leads are excluded unless the draft includes them with a reason (flagged per row);
  pattern hypotheses are never exported. Every row carries `exported_at` and its versions and
  freshness columns.
- CSV/XLSX cells starting with `=`, `+`, `-`, `@`, tab or carriage return get a leading `'`; control
  characters are dropped; cells are cut at 32 767 characters and XLSX refuses more than 1 048 575 rows.
- The history (`exports`) records who, what, where, the configuration, the preset and the hash; nothing
  is sent anywhere.

**Trade-off.** A query export reads the leads matching at write time; only a snapshot export is
reproducible byte for byte (the manifest says which).

## D-087 · Five built-in export presets, versioned and cloned, never rewritten (phase 13)

**Decision.** `sales_ready_concise` (CSV), `full_evidence_audit`, `contact_review` and
`website_opportunity_report` (XLSX) over explorer queries, and `dataset_analytical` (Parquet) over
snapshots. Saving a preset writes its next version and marks the previous one superseded; a built-in
preset is cloned under a new key. Every column in the catalogue carries its description, shown in the
wizard, so a preset never hides what a field means.

**Trade-off.** Presets are shared by everyone using the store; there is no per-user preset.

## D-088 · The worker ships as a PyInstaller one-folder build next to the app (phase 14)

**Context.** The release app needs the Python worker without a Python installation. PyInstaller and
Nuitka were both tried on this machine with the same entry point (numbers in
[packaging.md](packaging.md#3-the-sidecar-pyinstaller-or-nuitka)).

**Decision.** PyInstaller 6.22.3, one folder, from the locked `packaging` dependency group
(`services/research-engine/packaging/research-engine.spec`): the worker's SQL migrations and prompts
as data, development tools excluded, a console program the core starts without a window. The Tauri
bundle copies it to `<install dir>/research-engine/` through `src-tauri/tauri.bundle.conf.json`, which
only `pnpm build` merges, so development and test builds need no sidecar. The core looks for
`research-engine/research-engine.exe` beside the app, then a bare `research-engine.exe`.

**Trade-off.** Nuitka could not build here (Python 3.12 needs MSVC 14.3; Visual Studio 2019 has 14.2)
and would only add compiled code, which protects no secret in this worker. The folder weighs 188 MiB,
102 of them Playwright's driver.

## D-089 · A per-user NSIS installer that refuses downgrades and keeps the analyst's data (phase 14)

**Decision.** NSIS in `currentUser` mode (no administrator rights), English and Spanish,
`allowDowngrades: false` because a store migrated by a newer build cannot be opened by an older one.
Uninstalling removes the program and keeps data, logs and configuration unless the uninstaller's
"delete the application data" option is ticked. Nothing is signed and no update endpoint is
configured: neither a certificate nor a server exists, so both are documented processes
([packaging.md](packaging.md#7-signing)), never faked.

**Trade-off.** Unsigned installers trigger SmartScreen; every update is a manual reinstall until a
signed update feed exists.

## D-090 · One instance, and a window that may only listen (phase 14)

**Decision.** `tauri-plugin-single-instance` is the first plugin: a second launch focuses the running
window instead of opening the stores again (two instances fought over DuckDB's lock). The `default`
capability grants only `core:event:allow-listen` and `core:event:allow-unlisten`: the interface uses
nothing else from Tauri; the app's own commands validate payloads in Rust and the save dialog is
opened by the core. The credential store D-005 foresaw for secrets is not needed: the app stores
no secret (sources needing credentials stay disabled).

**Trade-off.** The app's commands are not listed one by one in an app manifest; CSP and never rendering
fetched HTML keep a script in the page unlikely.

## D-091 · The data folder is bounded so no path passes Windows' 260 characters (phase 14)

**Context.** Seeding a store in a folder 157 characters deep failed: an artifact's temporary name
(64-character hash, extension, a 36-character id) passed 260 characters, and Python on Windows without
long-path support could not open it.

**Decision.** Temporary artifact names are short (16 hash characters and 8 random ones). The core
refuses a data folder longer than 140 characters before starting the worker, with the reason: the
deepest file the worker writes, a snapshot partition, adds 108 characters.

**Trade-off.** A folder deeper than 140 characters cannot hold the data even on machines with long
paths enabled.

## D-092 · Releases carry an SBOM and a dependency scan; flagged crates are updated or triaged (phase 14)

**Decision.** `pnpm run build:sbom` writes CycloneDX 1.5 files of what ships (`cargo tree` over normal
and build dependencies for Windows x64, `uv export --no-dev`, `pnpm sbom --prod`). Before a release:
`pnpm audit --prod`, `pip-audit` over the exported requirements and an OSV query over `Cargo.lock`.
Flagged crates with a compatible fix are updated (`time` 0.3.55, `serde_with` 3.23.0); the rest are
triaged in the phase report (Linux-only, build-time only or unmaintained without a fix).

**Trade-off.** `quick-xml` 0.38 (two denial-of-service advisories, fixed in 0.41) stays until `plist`
and Tauri move: it only parses the build's own configuration, never input.

## D-093 · Phones and emails come from Overture's open places; keyed directories stay out (after phase 15)

**Context.** Discovery shipped with one remote source, USAspending, whose recipients carry no phone,
email or website: contact intelligence (phase 09) had nothing to read and the crawler nothing to
visit. The operator needs a phone above all, and emails.

**Decision.** `overture.places` reads the open places of the Overture Maps Foundation (about 74
million): no account, no key, and licences that allow keeping the data (CDLA Permissive 2.0, Apache
2.0 or CC0 by contributor). Measured on 2026-09-15 over central Houston: 87 % with a phone, 48 % with
a website, 23 % with an email. One DuckDB query per run over the public Parquet (anonymous S3) keeps
places with a phone or an email and writes them to the data directory; pages are windows of that
copy. Places are located with Nominatim, one lookup a second. Details in
[discovery.md](discovery.md#overture-maps-places-phones-websites-and-emails).

**Considered and not built.**

| Source                     | Why not                                                                                                   |
| -------------------------- | --------------------------------------------------------------------------------------------------------- |
| Google Places, Yelp Fusion | Paid keys, and their terms forbid storing phones and addresses beyond short caches, which a lead store is |
| SBA Small Business Search  | Owners' emails and phones, but no public API: its site's `/_api/v2/search` answered HTTP 500 from outside |
| Foursquare OS Places       | Same fields and an open licence, but now behind a portal account and token; Foursquare feeds Overture     |
| OpenStreetMap (Overpass)   | Free and keyless; its US phone coverage was not measured. The next candidate if Overture falls short      |

**Trade-off.** Overture's `confidence` is kept, not filtered: about 40 % of central Houston's places
score near 0.2 and most of them have a phone. A radius is searched as a square. DuckDB downloads
`httpfs` on the first run on each machine.

## D-094 · The app starts Ollama with itself and stops the server it started (after phase 15)

**Context.** Local AI needs `ollama serve` running. The operator had to start it by hand, and a
server left running kept models in memory after the app closed.

**Decision.** At startup the core probes the configured loopback address. If nothing answers and
`ollama.exe` is found (`%LOCALAPPDATA%\Programs\Ollama`, then `PATH`), it starts `ollama serve`
hidden, with `OLLAMA_HOST` set to that address. On exit it stops that process and its model runners
(`taskkill /T`). A runtime that was already answering is used and never stopped.

**Trade-off.** If the app crashes, the server it started keeps running: the crate forbids `unsafe`,
so no Job Object ties it to the core. Ollama's own sign-in shortcut still starts it with Windows
unless the operator removes it.

## D-095 · «Buscar clientes» compone los comandos existentes (phase 16)

**Contexto.** Crear una lista exigía conocer y coordinar campañas, perfiles, activación y procesos.
Ese vocabulario describía la implementación en vez del objetivo comercial.

**Decisión.** La nueva portada compone, desde el frontend y en orden, `list_campaigns` /
`save_campaign`, `get_scoring_catalog` / `save_icp_profile` / `change_scoring` y `start_pipeline`.
No se añade ningún mensaje al worker. Los nombres estables «Buscar clientes · …» y «Mi oferta» hacen
que una búsqueda repetida cree una versión sobre los mismos objetos; la clave idempotente protege el
arranque. La barra simple solo muestra Buscar clientes y Ajustes; una preferencia persistida revela
intacta la navegación anterior y la paleta nunca la filtra.

La cantidad solicitada se multiplica por doce antes del descubrimiento: la evidencia observable, la
compatibilidad entre problema y oferta, la contactabilidad y las supresiones reducen el universo.
Los mensajes de progreso solo agregan contadores de trabajos reales. No se intenta una transacción
distribuida: si falla un paso, los borradores versionados quedan reutilizables y volver a pulsar
continúa sin proliferación de objetos.

**Consecuencia.** La secuencia no es atómica, pero no necesita ampliar el protocolo Rust/Python y
conserva su validación y auditoría. La pantalla explica un fallo en el último paso y permite repetir;
una operación ya iniciada sigue protegida por la clave idempotente del orquestador.

## D-096 · «Latinos primero» ordena en vez de excluir y la lista sale en la propia pantalla (phase 16)

**Contexto.** La primera búsqueda real (remodelación, Miami, FL, 25 clientes, «Solo negocios
latinos») terminó con «Hay 0 clientes listos». Tuvo tres causas. El perfil exigía evidencia latina
(`latino: market_affinity`) y 13 de los 14 negocios de Florida no la publicaban, así que
`gate.latino` los excluyó. Sin radio, Overture solo miró los límites municipales de Miami: 12
remodeladores con teléfono o correo. Y el recuento de la etapa de elegibilidad cubre todo el
proyecto, con restos de USAspending y de una campaña de Arkansas.

**Decisión.** En el modo simple el perfil nunca exige evidencia latina (`latino: none`). Pedirla
ordena la lista por `latino_confidence`, y la elección se guarda en el nombre de la búsqueda
(«… · latinos primero»). Sin radio se buscan 40 km (64 remodeladores alrededor de Miami). Al
terminar, la pantalla consulta `explore_leads` sobre la puntuación del proceso: prioridad o revisar,
sin supresión, con un canal utilizable, del estado y los tipos de negocio de la campaña. El teléfono
o correo de cada fila sale de `get_business`. Con 0 clientes dice cuántos negocios revisó. La etapa
de IA muestra «Saltado» cuando ningún perfil tiene modelo. Sigue sin añadirse ningún mensaje al
worker.

**Consecuencia.** Estado y categoría sustituyen a un filtro por campaña que el explorador no tiene:
dos búsquedas del mismo tipo en el mismo estado comparten lista, y un lugar que Overture encontró
solo por una categoría padre queda fuera. Un campo de campaña en el explorador es la mejora. La
lista pide hasta 100 fichas en paralelo.

## D-097 · Una exportación lleva el teléfono y el correo, y la lista se descarga desde la pantalla (phase 16)

**Contexto.** La búsqueda ya termina con clientes en pantalla, pero llevárselos no funcionaba. El
explorador solo guarda el **tipo** de canal (`main_channel`: teléfono o correo), nunca su valor, y
ninguna columna exportable ni añadida traía el número: un archivo exportado no se podía llamar. La
única forma de descargar era el explorador técnico, oculto de la barra simple, con un asistente de
dos diálogos y claves de columna en inglés.

**Decisión.** `phone` y `email` son columnas exportables de una consulta (`CONTACT_COLUMNS`), leídas
de `contact_points` por lotes junto a las filas: la publicada más validada de cada tipo, la primaria
antes que el resto, nunca una hipótesis ni una marcada «No contactar», la misma regla del canal del
índice (`leads.index.STATUS_RANK`). El preset «Sales-ready concise» las trae tras el nombre. En
«Buscar clientes» un botón **Descargar la lista** escribe todos los clientes de la búsqueda —no solo
los 100 que se ven— con las cabeceras en el idioma de la pantalla, por el mismo diálogo de guardado,
hash y manifiesto que cualquier exportación.

**Consecuencia.** El recuento previo sigue leyendo solo campos del explorador (un valor de contacto
no lo es). El archivo es CSV con BOM. `top_problem` y `offer` viajan como código; la pantalla los
traduce, el archivo no.

## D-098 · La ciudad se elige de una lista y la experiencia simple deja de ser una sola pantalla (phase 16)

**Contexto.** La ciudad era texto libre: se escribía y solo al terminar se sabía si Nominatim la
reconocía, así que una errata daba 0 clientes sin explicación. Y toda la experiencia simple
(formulario, progreso, lista de clientes e historial) vivía dentro de «Buscar clientes», con una
barra lateral de dos entradas, en contra de lo que ya describía `docs/ux/buscar-clientes.md`.

**Decisión.** Las ciudades salen del Gazetteer de lugares 2024 del US Census Bureau (dominio
público): 31.830 lugares de los 50 estados y DC, sin su descriptor legal («city», «CDP»...),
generados en `apps/desktop/src/features/campaigns/usCities.ts` y empaquetados una línea por estado.
El desplegable se llena con el estado elegido y se vacía al cambiarlo. La barra simple pasa a tener
cuatro entradas: **Buscar clientes** (formulario y progreso), **Tu lista de clientes**
(`/mis-clientes`, con la descarga), **Mis búsquedas** (`/mis-busquedas`) y **Ajustes**; las dos
nuevas leen los mismos datos con `useSearchRuns` y `useClientList`.

**Consecuencia.** El archivo de ciudades ocupa 315 KB en el bundle y solo cubre EE. UU.; un lugar
que el Censo no lista ya no puede escribirse a mano. La lista de una búsqueda anterior se abre con
`#/mis-clientes?job=<id>`. Los tests de interfaz seguían escritos contra la versión en inglés
anterior al rediseño: fijar el idioma inglés en `src/test/setup.ts` recupera 31 de ellos, y los ~100
que siguen fallando esperan nombres que el rediseño cambió (corregidos en e3184da: 257 de 257).

## D-099 · La lista de una búsqueda es la de su campaña, se trabaja en la pantalla y se descarga en palabras (phase 16)

**Contexto.** «Tu lista de clientes» filtraba por el estado y los tipos de negocio de la campaña
porque el explorador no sabía qué campaña encontró cada negocio (D-096): dos búsquedas del mismo
tipo en el mismo estado compartían lista. Mostraba un solo contacto, sin prioridad ni filtros, no
dejaba anotar nada, y el archivo escribía `weak_local_seo` en «Por qué llamarle» y no decía qué
ofrecer (D-097). Además, una fusión no mueve contactos: el archivo leía solo los del negocio raíz,
mientras su ficha también muestra los de los registros fusionados en él.

**Decisión.** El explorador tiene dos campos que solo filtran. `campaign` reúne las campañas cuyos
candidatos se convirtieron en un registro del negocio, fusionados incluidos (`entity_records`).
`reachable_by` reúne los tipos de contacto que un archivo escribiría: publicado, fuera de «No
contactar», ni inválido ni caducado, del negocio o de un registro fusionado en él. No son columnas,
ni claves de orden, ni columnas exportables; la vista por defecto y el preset de auditoría los dejan
fuera, así que ese preset no cambia de versión.

La lista de una búsqueda es `campaign has <id>`, prioridad o revisar, sin supresión y con un canal
utilizable. «Con teléfono», «Con correo» y «Solo Llamar primero» añaden condiciones a esa misma
consulta, así que la descarga lleva lo que se ve. `ExportColumn.labels` son las palabras que el
archivo escribe en lugar de los códigos de una columna, en el idioma de la pantalla: prioridad, por
qué llamarle y qué ofrecerle salen en palabras. Es el único campo con valor por defecto de un
contrato, para que los presets y borradores guardados antes sigan leyéndose.
`exports.best_contacts` lee también los contactos de los registros fusionados, y entre iguales
elige el más antiguo; la pantalla elige igual (`contactsOf`).

Cada cliente ofrece «Llamé» (o «Le escribí»), «Le interesa» y «No le interesa», que recorren el
embudo existente (`act_on_lead` con `record_outcome`) por las etapas que permite: un cliente
interesado fue contactado antes. «No contactar» añade una entrada de negocio a la lista de la fase
09 y se puede deshacer. «Ver guion» abre la ficha en su sección de oferta y «Ver todo», la ficha
entera; «Volver» regresa a la lista. No se añade ningún mensaje al worker.

**Consecuencia.** En el explorador avanzado, el filtro por campaña se escribe como texto (su id).
Los motivos para llamar son frases de la pantalla, fieles a lo que el motor observó; el guion y
las explicaciones de la ficha siguen en inglés, como el resto de textos del motor.

## D-100 · «Mis búsquedas» se lee de lo guardado y «Repetir» rellena el formulario (phase 16)

**Contexto.** «Mis búsquedas» solo mostraba el nombre interno de la campaña, el estado y «Abrir».
El prompt 16 pide fecha, qué, dónde, cuántos clientes y estado, con «Abrir», «Continuar» y
«Repetir».

**Decisión.** Nada nuevo se guarda. El tipo de negocio, el lugar y «latinos primero» se leen del
nombre que `searchName` dio a la campaña; el radio y cuántos clientes se pidieron, de sus
criterios; lo que se vendía, de las ofertas `simple_*` de la versión de «Mi oferta» con la que se
puntuó la búsqueda (`score_run` de la página del explorador); y los clientes, de la misma consulta
de la lista (D-099) con una fila. Cada búsqueda se lee una vez por estado, aunque la lista se
refresque mientras otra corre. «Continuar» reanuda el mismo proceso (`control_pipeline`).
«Repetir» abre «Buscar clientes» con el formulario relleno y no arranca nada: la persona revisa y
pulsa «Buscar clientes», que versiona la misma campaña y el mismo perfil (D-095).

**Consecuencia.** Una búsqueda que aún no terminó no dice qué vendía (no tiene puntuación) y
«Repetir» no se ofrece mientras está en curso o en pausa. Las búsquedas creadas antes de la lista
de ciudades pueden traer una ciudad que ya no está en la lista: se vuelve a elegir.

## D-101 · Un paso fallido se explica sin códigos y el modo simple solo salta lo que no hace la lista (phase 16)

**Contexto.** Cuando un paso agotaba sus intentos o el motor lo rechazaba, la búsqueda quedaba
«Fallida» con una carta muerta abierta (D-084) y el modo simple no decía nada: solo la pantalla
avanzada de Trabajos permitía reintentar o saltar, con códigos como `network`.

**Decisión.** «Buscar clientes» y «Mis búsquedas» muestran la carta abierta en palabras: en qué paso
se detuvo, por qué (un texto por código conocido, uno genérico para el resto) y, si el motor la
marcó como pasajera, que reintentar suele arreglarlo. «Reintentar» y «Saltar este paso» llaman al
`resolve_dead_letter` existente con un motivo fijo; el mensaje del motor queda plegado en «Detalles
técnicos». En el modo simple solo se pueden saltar los pasos sin los que igualmente sale una lista
—visitar las webs, evidencia latina, problemas web, contactos e IA local— y cada uno dice qué se
pierde. Buscar y unir negocios, puntuar y preparar la lista no se ofrecen para saltar: sin ellos
no hay lista (la puntuación del proceso sale del último paso).

**Consecuencia.** Un paso que no se puede saltar y sigue fallando deja la búsqueda detenida hasta
que se arregle la causa; la pantalla avanzada sigue permitiendo saltarlo. Verificado en la app real
sobre una copia de la búsqueda de Miami detenida en «buscar problemas en sus webs»: saltarlo
reanudó la búsqueda y terminó las diez etapas.

## D-102 · «Otro» es una oferta más de «Mi oferta», con el nombre y los problemas de la persona (phase 16)

**Contexto.** «¿Qué vendes?» solo ofrecía cinco tarjetas. El prompt 16 pide «Otro»: un nombre
libre y los problemas que resuelve, marcados con frases y no con códigos.

**Decisión.** «Otro» añade a «Mi oferta» una oferta con clave fija `simple_other`, el nombre que
escribió la persona (recortado, 80 caracteres como máximo) y los `ProblemType` de las casillas
que marcó. Cada casilla describe una situación del negocio, no un código. El motor la trata como
cualquier otra oferta: la puntuación la recomienda cuando la evidencia muestra uno de esos
problemas. El nombre no cabe en la clave (un código), así que la lista, el archivo, «Mis
búsquedas» y «Repetir» lo leen de la versión de «Mi oferta» con la que se puntuó la búsqueda. Si
ese catálogo no se puede leer, la lista sale igual y solo queda sin nombre esa oferta. No cambia
ningún contrato ni mensaje del worker.

**Consecuencia.** Solo cabe un servicio propio por búsqueda. Su nombre se guarda en la versión del
perfil, así que una búsqueda posterior con otro nombre crea otra versión y la anterior conserva el
suyo.

## D-103 · Una búsqueda lee todas las webs de su campaña, no las cien primeras

**Contexto.** La etapa «visitar sus páginas web» arrancaba el rastreo con los valores por defecto:
100 sitios y 2.000 peticiones. Una búsqueda de 100 clientes encuentra hasta 1.200 negocios, unos 900
con web, así que nueve de cada diez webs no se leían. Sin página leída no hay evidencia latina, ni
auditoría, ni motivo para llamar: «Latinos primero» ordenaba el 6 % de la lista. Medido el
2026-10-07 sobre las 13 búsquedas reales (8.146 negocios): la app había dado 63 negocios con
evidencia de mercado hispanohablante y ninguno con dueño latino; leyendo la portada de sus 5.631
webs propias salieron 109 con alguna señal y, verificados, 83 clientes, 4 con dueño latino
declarado.

**Decisión.** La etapa de adquisición de cualquier proceso usa `campaign_crawl_settings()`: el
mismo presupuesto por sitio (12 páginas, 30 peticiones por minuto, 2 a la vez) con `max_sites` y
`max_requests` en sus topes, que suben de 500 a 2.000 sitios y de 20.000 a 40.000 peticiones (20
por sitio; medido: 8,7). Un rastreo que alguien lanza a mano conserva los valores por defecto. No
se cambia la concurrencia por sitio: con una petición a la vez (el doble de webs en paralelo) 120
webs reales tardaron 2,4 minutos frente a 2,7, con las mismas páginas (553 y 558); no compensa
alterar un trato por sitio ya probado.

**Consecuencia.** Una búsqueda grande tarda más: unas 44 webs por minuto, cerca de 21 minutos de
lectura para 900 webs donde antes eran de 3 a 5, dentro de los 25–60 minutos que la pantalla ya
anuncia para 100 clientes. También ocupa más disco, en torno a 1 MB por web (casi 1 GB por
búsqueda grande). Cada sitio recibe exactamente las mismas peticiones que antes. Una campaña con
más de 2.000 webs se sigue recortando y lo dice el evento `sites_capped`.
