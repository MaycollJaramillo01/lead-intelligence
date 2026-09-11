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

Invalid values never stop the app: the override layers are ignored, built-in defaults are used, and
the Desktop Core row of the diagnostics screen shows the warning. Put secrets nowhere in these
files.

## Logs

- File: `logs/desktop.YYYY-MM-DD.log` (release: `%LOCALAPPDATA%\com.leadintelligence.desktop\logs`).
- One JSON object per line. Worker records have `"target":"worker"`; UI errors `"target":"ui"`.
- Follow one request: search for its `request_id`; the worker's lines carry the same id.
- Rotation: daily; `logging.max_files` files are kept (default 14).

## Tests

| Suite  | Command          | Notes                                                                                                                                  |
| ------ | ---------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Python | `pnpm test:py`   | spawns the real worker; tests marked `local_services` use the real Ollama / NVIDIA driver and skip with an explicit reason when absent |
| Rust   | `pnpm test:rust` | unit tests, contract round-trips, supervisor against the real worker and scripted failure modes, IPC through Tauri's mock runtime      |
| TS     | `pnpm test:ts`   | adapters, store, diagnostics screen (jsdom)                                                                                            |

Run only the tests that need local services: `uv run --directory services/research-engine pytest -m local_services -rs`.

## Troubleshooting

| Symptom                                                      | Cause / fix                                                                                                                                           |
| ------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| Python Worker: `worker executable not found`                 | Development virtualenv missing: `pnpm run setup`.                                                                                                     |
| Python Worker: `Failed` with a traceback                     | Read the stderr tail on the diagnostics screen and the `worker` records in the log.                                                                   |
| Python Worker: `automatic restarts exhausted`                | The worker crashed 3 times in 5 minutes; fix the cause, then press **Restart worker**.                                                                |
| Ollama: `installed but not running`                          | Start Ollama (tray app or `ollama serve`). Nothing is downloaded automatically.                                                                       |
| GPU: `nvidia-smi not found`                                  | No NVIDIA driver; local AI will use the CPU.                                                                                                          |
| DuckDB: `could not set lock on file`                         | Another app instance (or tool) has the database open. Close it (single-instance lock: phase 14).                                                      |
| `uv` fails with `os error 1921` when run from a packaged app | Cache entries created inside an MSIX-virtualised process. Retry with `uv --no-cache …` or clear the affected entries with `uv cache clean <package>`. |
| `git` reports _dubious ownership_                            | The `.git` directory belongs to another Windows account; use `git -c safe.directory=<repo> …` or add the exception yourself.                          |

## Clean

`pnpm clean` removes `apps/desktop/dist`, `src-tauri/target`, tool caches and `__pycache__`
directories. It never deletes `data/`, `logs/`, `config/local.toml`, `node_modules` or `.venv`.
