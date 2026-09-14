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
                                   │ repositories, datasets  │── Parquet data/datasets/…
                                   └───────────┬────────────┘   backups  data/backups/
                                               ├── HTTP → Ollama (loopback only, optional)
                                               ├── nvidia-smi + nvcuda.dll (optional)
                                               ├── HTTPS → public websites and APIs (guarded)
                                               └── Playwright driver → Chromium (on demand,
                                                   in the worker's kill-on-close job object)
```

| Layer         | Owns                                                                 | Never does                                       |
| ------------- | -------------------------------------------------------------------- | ------------------------------------------------ |
| React UI      | Presentation, ephemeral UI state (Zustand), calls typed IPC adapters | Run SQL, spawn processes, call `invoke` ad hoc   |
| Rust core     | Window, IPC surface, configuration, logging, worker lifecycle        | Open the database files, interpret research data |
| Python worker | Databases and migrations, probes, (later) every research engine      | Talk to the UI directly, print to stdout         |

## Contracts

`services/research-engine/src/research_engine/contracts.py` (Pydantic v2) is the single source of
truth for every cross-process message: the worker envelope and payloads, and the Rust → UI
diagnostics report. `domain.py` does the same for the data model (records, enumerations, queries and
pages; see [data-model.md](data-model.md)). `pnpm contracts` exports:

- `packages/shared-types/schema/contracts.schema.json` — JSON Schema (2020-12)
- `packages/shared-types/src/contracts.ts` — TypeScript types (json-schema-to-typescript)
- `packages/shared-types/fixtures/*.json` — synthetic golden samples

Rust mirrors the models in `src-tauri/src/protocol.rs` and the data contract in
`src-tauri/src/data.rs` with `deny_unknown_fields` and validated ids, timestamps, confidences and
cursors; `src-tauri/tests/contracts.rs` round-trips every fixture and fails on any drift. `pnpm
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

- Requests (core → worker): `health`, `diagnostics`, `shutdown`, `businesses.list`,
  `businesses.get`, `datasets.list`, the discovery set (`sources.list`, `sources.preview_import`,
  `campaigns.list`, `campaigns.save`, `campaigns.validate`, `discovery.start`, `discovery.control`,
  `discovery.runs`, `discovery.candidates`, `discovery.candidate`), the crawl set (`crawl.start`,
  `crawl.control`, `crawl.runs`, `crawl.urls`, `crawl.url`), the resolution set
  (`resolution.start`, `resolution.control`, `resolution.runs`, `matches.list`, `matches.get`,
  `matches.act`, `businesses.choose_value`), the Latino evidence set (`latino.start`,
  `latino.control`, `latino.runs`, `latino.get`, `latino.review`), the website intelligence set
  (`website.start`, `website.control`, `website.runs`, `website.audits`, `website.audit`), the
  contact intelligence set (`contacts.start`, `contacts.control`, `contacts.runs`, `contacts.list`,
  `contacts.get`, `contacts.act`, `decision_makers.list`, `decision_makers.review`,
  `suppressions.list`, `suppressions.change`, `suppressions.import`, `suppressions.export`), the
  local AI set (`ai.status`, `ai.configure`, `ai.start`, `ai.control`, `ai.runs`, `ai.inferences`,
  `ai.inference`, `ai.review`, `ai.benchmark`, `ai.model_change`), the lead scoring set
  (`scoring.catalog`, `scoring.save_icp`, `scoring.save_model`, `scoring.change`, `scoring.simulate`,
  `scoring.start`, `scoring.control`, `scoring.runs`, `scoring.leads`, `scoring.scorecard`,
  `scoring.compare`, `scoring.calibration`, `scoring.act`) and, in development builds only,
  `dev.seed_synthetic`. The answer echoes `request_id` with type `<request>.result` and carries
  either `payload` or `error`, never both.
- Events (worker → core): `worker.ready` (handshake, carries a health payload) and
  `worker.protocol_error` (malformed input; correlated to the request id when recoverable).
- Errors: `{code, message, retryable}` with codes `invalid_message`, `unsupported_protocol`,
  `unknown_type`, `invalid_payload`, `not_found`, `not_allowed`, `store_unavailable`, `timeout`,
  `shutting_down`, `internal`.
- Limits: one UTF-8 frame per line, at most 8 MiB. Oversized or invalid frames are rejected on
  both sides without losing stream synchronisation.
- Compatibility: `protocol_version` must match exactly. Additive changes (new message types, new
  nullable fields) keep the version; anything else bumps it. The data model has its own
  `data_contract_version`, reported in the health handshake; the core refuses a worker that speaks
  another one.

## Worker lifecycle

1. `start()` (single instance per supervisor) spawns the worker with `CREATE_NO_WINDOW`, piped
   stdio and an explicit environment (`LEAD_DATA_DIR`, `LEAD_LOG_LEVEL`, `LEAD_OLLAMA_URL`, …).
2. The worker migrates both stores (backing them up first when migrations are pending on a used
   store, recovering a damaged one from backup), then emits `worker.ready`. The core then sends an
   explicit `health` request and checks its `data_contract_version`; only after both it reports
   `running`. Startup is bounded by
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

## Data flow: businesses and datasets

```text
BusinessesScreen ─ useKeysetPages ─ desktop.listBusinesses({search, sort, limit, cursor})  (apps/desktop)
  → invoke("list_businesses", {query})                                              (Tauri IPC)
  → commands::list_businesses: query.validate() (limit, search length, typed cursor/id)
  → supervisor.request_with("businesses.list", query, 15 s)                          (src-tauri)
  → worker: Pydantic validates the payload → repository.list_businesses
    (keyset page + FTS5 search over SQLite, one short-lived connection on a thread)  (research-engine)
  ← BusinessPage deserialised by serde, then page.validate(query): size, total, filters
InspectorPanel ─ desktop.getBusiness({business_id}) → get_business → businesses.get
  ← BusinessDetail with a Provenance block per value; detail.validate(id) checks ownership
DatasetsScreen ─ desktop.listDatasets → list_datasets → datasets.list → dataset + latest snapshot
```

## Data flow: a discovery run

```text
DiscoveryScreen ─ desktop.startDiscovery({campaign_id, inputs})                    (apps/desktop)
  → invoke("start_discovery", {request}) → commands::start_discovery (validates the payload size)
  → supervisor.request_with("discovery.start", request, 70 s)                       (src-tauri)
  → worker: criteria validated against the source catalogue, job + source_run + job_task created,
    the imported file written under imports/<run>/, then the run continues in the background
  ← DiscoveryRun (the run as it stands), checked against its own source totals
  ⟳ desktop.listDiscoveryRuns / listCandidates while the run is moving (the UI polls, the worker
    never pushes), then desktop.getCandidate for the raw record and its provenance
```

The engine is described in [discovery.md](discovery.md): adapters, criteria, limits, retries,
checkpoints and how a paused or interrupted run resumes.

## Data flow: a crawl

```text
ResearchCenterScreen ─ desktop.startCrawl({campaign_id?, urls, settings})           (apps/desktop)
  → invoke("start_crawl") → commands::start_crawl: settings bounds, addresses       (src-tauri)
  → worker: sites from the campaign's candidates and the addresses; job + one source_run, job_task,
    robots.txt row and home row per site; the crawl continues in the background.
    Per site: robots.txt → sitemaps → pages by priority. Each URL is one transaction:
    guarded HTTP fetch ─(criterion?)→ Chromium render → acquired_page + raw_record + crawl_url
  ⟳ desktop.listCrawlRuns / listCrawlUrls while a crawl moves (metrics aggregated from crawl_urls,
    in-flight URLs from the worker's memory), desktop.getCrawlUrl for one URL and its provenance
JobsScreen ─ listDiscoveryRuns + listCrawlRuns; cancel goes to the engine that owns the job
```

The crawler is described in [crawling.md](crawling.md): policies, SSRF guard, fallback criteria,
limits, scheduling, metrics and reason codes.

## Data flow: entity resolution

```text
BusinessesScreen ▸ Possible duplicates ─ desktop.startResolution({project_id: null})    (apps/desktop)
  → invoke("start_resolution") → commands::start_resolution                          (src-tauri)
  → worker: one job per project; on a thread: ingest candidates in batches (records, observations,
    checkpoint) → load observations → blocking → compare → store pairs → recluster from the stored
    pairs (people first) → canonical values                                          (research-engine)
  ⟳ desktop.listResolutionRuns while a run moves; desktop.listMatches for the queue
MatchReview ─ desktop.getMatch → both records with their evidence, fields, reasons, history
  ─ desktop.actOnMatch({match_id, action, reason}) → recluster the project → MatchDetail
BusinessInspector ─ desktop.chooseCanonicalValue({business_id, field, observation_id}) → BusinessDetail
```

The engine is described in [resolution.md](resolution.md): normalisers, blocking, weights,
thresholds, safety rules, clusters, canonical values and the reversible flow.

## Data flow: Latino evidence

```text
BusinessesScreen ▸ LatinoFilters ─ desktop.startLatino({project_id: null})             (apps/desktop)
  → invoke("start_latino") → commands::start_latino                                  (src-tauri)
  → worker: one job per project; on a thread: extract claims from the raw records and crawled pages
    behind its businesses (versioned extractions, checkpoint) → move stale, revoked and contradicted
    evidence → evaluate each canonical business with the rules, superseding what changed (research-engine)
  ⟳ desktop.listLatinoRuns while a run moves, then the list is read again with its Latino filters
LatinoPanel ─ desktop.getLatino({business_id}) → the review or calculation, evidence, history
  ─ desktop.reviewLatino({business_id, status, reason}) → bounded by the evidence → LatinoDetail
```

The rules are described in [latino-evidence.md](latino-evidence.md): tracks, evidence matrix,
factors, tiers, lifecycle, review and what a result cannot conclude.

## Data flow: website intelligence

```text
ResearchCenterScreen ▸ Website intelligence ─ desktop.startWebsiteAudit({project_id, sites})  (apps/desktop)
  → invoke("start_website_audit") → commands::start_website_audit: sites within one project  (src-tauri)
  → worker: one job per project; on a thread, per crawled site (its latest finished crawl): crawl rows
    and stored HTML → page facts (versioned extractor) → snapshot → versioned rules → audit, findings,
    technologies, conversion elements and the difference with the previous audit, one transaction
    and one checkpoint per site; a crawl already audited with these versions writes nothing  (research-engine)
  ⟳ desktop.listWebsiteRuns / listWebsiteAudits while a run moves
WebsiteAuditPanel ─ desktop.getWebsiteAudit({audit_id}) → findings with evidence, coverage, subscores,
  pages, technologies, comparison and history; validate() checks every subscore against its findings
JobsScreen ─ listWebsiteRuns; cancel goes to control_website_audit
```

Every rule, its thresholds and its limits are in [website-intelligence.md](website-intelligence.md).

## Data flow: contact intelligence

```text
ResearchCenterScreen ▸ Contacts ─ desktop.startContacts({project_id, check_mail_domains})   (apps/desktop)
  → invoke("start_contacts") → commands::start_contacts                                      (src-tauri)
  → worker: one job per project; on a thread: channels and people of every crawled page that speaks
    for a business (contacts.site extractions, checkpoint per 100 pages) → per 200 canonical
    businesses: mail domains (cache; DNS only when allowed and online), people to review, hypotheses,
    statuses with history, checkpoint                                                    (research-engine)
  ⟳ desktop.listContactRuns while a run moves; desktop.listContacts with its filters
ContactPanel (Contacts view, BusinessInspector) ─ desktop.getContacts({business_id}) → channels decided
  now, hypotheses apart, people, conflicts, contactability; validate() refuses a hypothesis as a contact
  ─ desktop.actOnContact({business_id, key, action, reason, check_mail_domains}) → ContactDetail
People to review ─ listDecisionMakers({review_status: "ambiguous"}) ─ reviewDecisionMaker
Do not contact ─ listSuppressions ─ changeSuppression ─ importSuppressions ─ exportSuppressions
JobsScreen ─ listContactRuns; cancel goes to control_contacts
```

What each status means, the rules, contactability and the do-not-contact list are in
[contact-intelligence.md](contact-intelligence.md).

## Data flow: local AI

```text
AiModelsScreen ─ desktop.getAiStatus (polled every 5 s while open) → get_ai_status → ai.status
  → worker: runtime probe, model details (cached by digest), loaded models, nvidia-smi, memory,
    settings, benchmark results, recommendations; LLM Fit only when asked      (research-engine)
  ← AiStatus, validated by the core (one entry per task, availability explained)
AiPanel (BusinessInspector) ─ desktop.startAi({business_ids, task_types}) → start_ai → ai.start
  → worker: one ai_analysis job; on a thread, per business × task: input from the store (page
    fragments, findings, subscores) → runner on the event loop: cache? → run row → GPU queue →
    breaker → VRAM placement → Ollama /api/chat with a JSON schema → checks → one repair at most →
    answers and run stored → Latino declarations to the Latino rules; checkpoint, aggregated event
  ⟳ desktop.listAiRuns / listAiInferences while a run moves
InferenceView ─ desktop.getAiInference → the run, raw and validated answers, cited inputs, reviews;
  validate() refuses a citation of an input the model was never given
  ─ desktop.reviewAiInference({decision, reason}) → AiInferenceDetail
Crawler ─ before each URL, while the AI queue works under memory pressure: wait (≤ 120 s)
```

Tasks, checks, prompts, VRAM, the queue, security, the benchmark and the recommended
configuration for an RTX 4060 are in [local-ai.md](local-ai.md).

## Data flow: lead scoring

```text
IcpBuilderScreen ─ desktop.getScoringCatalog({project_id}) → get_scoring_catalog → scoring.catalog
  → worker: profiles and models with every version and the problems of the newest one, the feature
    catalog and templates; the default heuristic model is created on first read    (research-engine)
  ─ desktop.saveIcpProfile · desktop.changeScoring({target, id, action, version_id, reason})
  ─ desktop.simulateScoring({project_id | facts_from_run_id, icp, scoring, top}) → scoring.simulate
  → worker: facts read now (≤ 5 000 businesses) or stored by a run → model.score in memory → tiers,
    distribution, gates with failing examples, top leads; nothing written
ScoringModelsScreen ─ desktop.saveScoringModel ─ desktop.startScoring({project_id,
  icp_profile_version_id, scoring_model_version_id, facts_from_run_id}) → start_scoring
  → worker: one lead_scoring job; on a thread, per 200 canonical businesses: facts from the store
    or from the stored run → model.score → business_scores and component_scores, checkpoint
  ⟳ desktop.listScoreRuns while a run moves ─ compareScoreRuns ─ getCalibration
LeadExplorerScreen ─ desktop.listLeadScores({score_run_id, tier, min_score, problem, search, cursor})
  → ranked rows of the newest finished run (ROW_NUMBER over the whole run); validate() checks the
  rank order and the filters
ScorecardPanel (BusinessInspector) ─ desktop.getLeadScorecard({business_id, score_run_id}) → the
  scorecard, earlier scores, outcomes, reviews, AI wording only when it cites the scorecard's own
  findings; validate() refuses contributions that do not add up and claims without evidence
  ─ desktop.actOnLead({action, stage, lost_reason, reason}) → a review, or an outcome that moves
  lead_stage along its state machine
JobsScreen ─ listScoreRuns; cancel goes to control_scoring
```

Features, gates, formulas, versions, calibration and limits are in [scoring.md](scoring.md).

Worker errors keep their meaning across the boundary: `not_found` → `CommandError.not_found`,
`not_allowed` → `not_allowed`, `invalid_payload` → `invalid_input`; anything else is `worker_error`
with the worker's message.

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
| Backups     | `<data>/backups`           | same, under the data directory                       |
| Datasets    | `<data>/datasets`          | same, under the data directory                       |
| Logs        | `<repo>/logs`              | `%LOCALAPPDATA%\com.leadintelligence.desktop\logs`   |
| User config | `<repo>/config/local.toml` | `%APPDATA%\com.leadintelligence.desktop\config.toml` |
| Worker      | uv virtualenv python       | `research-engine.exe` next to the app (phase 14)     |

Nothing is hardcoded: paths come from Tauri's path resolver or `CARGO_MANIFEST_DIR` (debug builds
only) and can be overridden in configuration.
