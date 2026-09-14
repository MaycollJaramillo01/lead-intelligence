# Operations

## First run (development)

```bash
pnpm run setup
pnpm dev
```

`pnpm run setup` installs the JS workspace from `pnpm-lock.yaml` and the worker from
`services/research-engine/uv.lock` (uv downloads Python 3.12 if needed). `pnpm dev` starts Vite on
port 1420 and the Tauri shell, which spawns the worker from the uv virtualenv.

## Configuration

Precedence, later wins:

1. `config/default.toml` (embedded)
2. `config/development.toml` or `config/production.toml` (embedded, chosen by build type or `LEAD_ENV`)
3. user file: `config/local.toml` in development, `%APPDATA%\com.leadintelligence.desktop\config.toml` in release
4. environment variables

| Variable              | Overrides         | Example                     |
| --------------------- | ----------------- | --------------------------- |
| `LEAD_ENV`            | environment       | `development`/`production`  |
| `LEAD_LOG_LEVEL`      | `logging.level`   | `debug`                     |
| `LEAD_DATA_DIR`       | `paths.data_dir`  | `D:\lead-data`              |
| `LEAD_LOG_DIR`        | `paths.log_dir`   | `D:\lead-logs`              |
| `LEAD_WORKER_PROGRAM` | `worker.program`  | path to a worker executable |
| `LEAD_OLLAMA_URL`     | `ollama.base_url` | `http://127.0.0.1:11434`    |

`LEAD_OLLAMA_URL` must point at this machine (`127.0.0.1`, `localhost` or `::1`): anything else is
rejected as a configuration warning and the default is used. Local AI settings (model per profile,
profile per task, memory limits) live in the store and are edited in **AI Models**
([local-ai.md](local-ai.md)).

Crawl limits are not configuration: each crawl carries its own `CrawlSettings`, bounded by the safe
limits in [crawling.md](crawling.md) section 6.

`LEAD_DISCOVERY_OFFLINE=1` keeps the worker off the network wherever the network is optional:
sources that need it refuse, and contact checks never look up mail domains (addresses stay
`structurally_valid`). Contact validation never talks to a mail server, calls a number or submits a
form in any mode ([contact-intelligence.md](contact-intelligence.md)).

### Browser for the crawler

The crawler renders a page in Chromium only when a criterion asks for it. It uses Playwright's own
Chromium when installed, otherwise Microsoft Edge (present on Windows 11) or Google Chrome. To
install Playwright's build (optional, about 150 MB from Microsoft's CDN):

```bash
uv run --directory services/research-engine playwright install chromium
```

Invalid values never stop the app: the override layers are ignored, built-in defaults are used, and
the Desktop Core row of the diagnostics screen shows the warning. Put secrets nowhere in these
files.

## Logs

- File: `logs/desktop.YYYY-MM-DD.log` (release: `%LOCALAPPDATA%\com.leadintelligence.desktop\logs`).
- One JSON object per line. Worker records have `"target":"worker"`; UI errors `"target":"ui"`.
- Follow one request: search for its `request_id`; the worker's lines carry the same id.
- Rotation: daily; `logging.max_files` files are kept (default 14).

## Tests

| Suite  | Command          | Notes                                                                                                                                                                                                    |
| ------ | ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Python | `pnpm test:py`   | spawns the real worker; tests marked `local_services` use the real Ollama / NVIDIA driver and skip with an explicit reason when absent; crawl tests run against a local fixture site and launch Chromium |
| Rust   | `pnpm test:rust` | unit tests, contract round-trips, supervisor against the real worker and scripted failure modes, IPC through Tauri's mock runtime                                                                        |
| TS     | `pnpm test:ts`   | adapters, store, diagnostics screen (jsdom)                                                                                                                                                              |

Run only the tests that need local services: `uv run --directory services/research-engine pytest -m local_services -rs`.

## Troubleshooting

| Symptom                                                              | Cause / fix                                                                                                                                                                                                    |
| -------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Python Worker: `worker executable not found`                         | Development virtualenv missing: `pnpm run setup`.                                                                                                                                                              |
| Python Worker: `Failed` with a traceback                             | Read the stderr tail on the diagnostics screen and the `worker` records in the log.                                                                                                                            |
| Python Worker: `automatic restarts exhausted`                        | The worker crashed 3 times in 5 minutes; fix the cause, then press **Restart worker**.                                                                                                                         |
| Ollama: `installed but not running`                                  | Start Ollama (tray app or `ollama serve`). Nothing is downloaded automatically.                                                                                                                                |
| AI Models: a task says `No model assigned to its profile`            | By design no model is assumed: pick an installed model for the profile in **Profiles and tasks** and save. Run a benchmark to see which one to choose.                                                         |
| AI Models: `Paused: the runtime kept failing`                        | Three runtime failures in a row opened the breaker; it retries by itself after 30 s (doubling up to 5 min). AI jobs pause instead of looping: resume them in Jobs.                                             |
| AI: an inference fails with `insufficient_vram`                      | The model does not fit in the free VRAM even at 2 048 tokens and its profile does not allow the CPU. Allow the CPU, use a smaller model, or close what is using the GPU.                                       |
| AI: answers end as `invalid_output`                                  | The model's answer broke the schema or cited inputs it was not given, twice. Open it in the history to see why; a benchmark shows which installed model answers validly.                                       |
| AI: crawling slows down while AI runs                                | Free RAM or VRAM is under the limits in **Profiles and tasks**: each URL waits up to 120 s. Lower the limits or let the AI work finish.                                                                        |
| GPU: `nvidia-smi not found`                                          | No NVIDIA driver; local AI will use the CPU.                                                                                                                                                                   |
| DuckDB: `could not set lock on file`                                 | Another app instance (or tool) has the database open. Close it (single-instance lock: phase 14).                                                                                                               |
| SQLite: `degraded · recovered from backup …`                         | The store was damaged and replaced by the named backup; the damaged file is kept as `lead.sqlite3.corrupt-<utc>`. Changes after that backup are lost.                                                          |
| SQLite: `… is damaged … and no usable backup exists`                 | Follow **Recover a damaged database** below.                                                                                                                                                                   |
| SQLite/DuckDB: `… is at schema vN, this build needs vM`              | A migration failed at startup (see the error on System and in the log). The store is unchanged and a backup was taken before the attempt.                                                                      |
| Businesses: `the cursor does not belong to …`                        | The list was paged with a cursor from another sort or a stale build; reload the screen.                                                                                                                        |
| Research Center: sites fail with `Private or local address refused`  | The address resolves to this machine or the local network; the crawler only visits public internet addresses, by design.                                                                                       |
| Research Center: `No browser available`                              | Neither Playwright's Chromium, Edge nor Chrome could start, or free memory is under 2 GiB; pages stay as fetched over HTTP.                                                                                    |
| Research Center: `robots.txt unreadable: nothing allowed`            | The site's robots.txt answered 5xx or 429, or did not answer; RFC 9309 says to fetch nothing. Try again later.                                                                                                 |
| Businesses: `a resolution run is working on this project`            | Review decisions and value choices wait while a resolution run is queued or running on that project. Let it finish, or pause it in Jobs.                                                                       |
| Businesses: `an evaluation is already open for this project`         | A Latino evidence evaluation is queued, running or paused for that project. Resume or cancel it in Jobs before starting another.                                                                               |
| Latino review: `an ownership status needs active ownership evidence` | By design: a person can only choose a status the evidence can support (market affinity needs market signals, verified needs strong ownership evidence).                                                        |
| Latino evidence: everything stays `Unknown`                          | Expected without explicit declarations: names, Spanish and places never count. Crawl the websites (Research Center) and resolve before evaluating.                                                             |
| Website intelligence: `this project has no finished crawl yet`       | Audits read what a crawl stored. Crawl the websites in Research Center ▸ Acquisition first.                                                                                                                    |
| Website intelligence: an audit is `Partial` or `Empty`               | Pages the crawl failed, left queued, or whose stored HTML is missing cannot be analyzed; the rules that need them are unknown, not failed. Crawl the site again, then audit.                                   |
| Website intelligence: auditing again changes nothing                 | By design: a crawl already audited with the same rules writes nothing. Crawl the site again to read fresh pages.                                                                                               |
| Website intelligence: analytics `not detected`                       | Tags loaded by a tag manager or a consent tool leave no trace in the HTML; not detected never means absent.                                                                                                    |
| Contacts: every email stays `Well-formed only`                       | Mail domains are looked up only when **Look up mail domains (DNS)** is ticked and the worker is not offline. A DNS timeout keeps an address well formed; it never invalidates it.                              |
| Contacts: a hypothesis cannot be confirmed or shown                  | By design: an address built from a pattern is never a contact. Crawl a page where the business publishes it, or mark it invalid.                                                                               |
| Contacts: `this value is already on the do-not-contact list`         | A live entry covers the value after normalisation (lower-case address, E.164 number, registrable domain). Lift it first to add it again with another reason.                                                   |
| Contacts: a do-not-contact import reports problems                   | Each problem names its line: the header needs `kind` and `value`, kinds are email, phone, domain or business, and at most 5 000 rows are read.                                                                 |
| Contacts: `there are no businesses to check yet`                     | Discover or import businesses and resolve them first; crawl their websites to read the channels they publish.                                                                                                  |
| Scoring: `a scoring run is already open for this project`            | A score run is queued, running or paused for that project. Resume or cancel it in Jobs before starting another.                                                                                                |
| Scoring: `these versions cannot run` or `cannot be activated`        | The ICP or scoring version has errors (for example `no_offers`, `empty_must_have`, `features_mismatch`), listed in ICP Builder and Scoring Models. Fix them and save a new version.                            |
| Scoring: most leads are `Insufficient data`                          | A gate is undecided or less than 60 % of the final weight is known (site not crawled or audited, contacts not checked, Latino evidence not evaluated). The scorecard's missing data says what would help most. |
| Scoring: a preview says the project has more than 5000 businesses    | A preview over facts read now reads every business at once. Run a score job, then preview over its stored facts.                                                                                               |
| `uv` fails with `os error 1921` when run from a packaged app         | Cache entries created inside an MSIX-virtualised process. Retry with `uv --no-cache …` or clear the affected entries with `uv cache clean <package>`.                                                          |
| `git` reports _dubious ownership_                                    | The `.git` directory belongs to another Windows account; use `git -c safe.directory=<repo> …` or add the exception yourself.                                                                                   |

## Data, backups and recovery

Details in [data-model.md](data-model.md). In short:

- `data/lead.sqlite3` (operational), `data/analytics.duckdb` (analytics), `data/datasets/` (Parquet
  snapshots), `data/artifacts/web/` (crawled bodies, rendered pages and screenshots, stored once
  per sha256) and `data/backups/` (verified backups, newest 10 per engine) all live under the data
  directory; every path stored in the database is relative to it, so the folder can be moved whole.
- A backup is taken automatically before migrations run on a store that already has data.
- Take a manual backup while the app runs (development shell, from the repository root):

  ```bash
  uv run --directory services/research-engine python -c "from pathlib import Path; from research_engine import storage; d = Path('../../data'); c = storage.open_sqlite(d / storage.SQLITE_FILE); print(storage.backup_sqlite(c, d / storage.BACKUP_DIR, 'manual')); c.close()"
  ```

### Recover a damaged database

1. Close the app (the worker must not hold the files).
2. Move `lead.sqlite3`, `lead.sqlite3-wal` and `lead.sqlite3-shm` to a safe folder. Do not delete
   them.
3. Copy the newest file from `data/backups/` to `data/lead.sqlite3`, or leave the folder empty to
   start with a fresh store.
4. Start the app and open **System**: SQLite should report `ok` and the expected schema version.

Integrity of any copy can be checked with
`uv run --directory services/research-engine python -c "from pathlib import Path; from research_engine.storage import sqlite_problems; print(sqlite_problems(Path('<file>')) or 'ok')"`.

## Synthetic test data (development only)

`pnpm dev` passes `LEAD_DEV_TOOLS=1` to the worker, and the Businesses screen shows **Load synthetic
test data · Dev**. It creates, once, a project flagged as synthetic with 38 fictitious businesses
(duplicates to resolve, Latino evidence cases, crawled sites to audit and contacts to check) and a
dataset snapshot, then opens one in the inspector. Release builds refuse the action in the core and
the worker. To remove it in development, stop the app and delete the development `data/` folder.

## Clean

`pnpm clean` removes `apps/desktop/dist`, `src-tauri/target`, tool caches and `__pycache__`
directories. It never deletes `data/`, `logs/`, `config/local.toml`, `node_modules` or `.venv`.
