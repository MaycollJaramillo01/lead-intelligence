# Performance budgets and measurements

Every number here was measured on the development machine unless it says otherwise: Windows 11 Home
26200, NVIDIA RTX 4060 8 GB, a machine also used for games (background load varies). Estimates are
never listed as measurements.

## 1. Budgets

| Area                                     | Budget                              | Measured                                                                 | How                                                   |
| ---------------------------------------- | ----------------------------------- | ------------------------------------------------------------------------ | ----------------------------------------------------- |
| App launch to worker running (installed) | ≤ 3 s                               | **1.75 s** first start, **1.48 s** restart                               | Install QA script, log line `python worker running`   |
| Packaged worker start to `worker.ready`  | ≤ 3 s                               | **1.05 s** (venv interpreter: 1.14 s)                                    | Sidecar smoke test over JSON Lines                    |
| Idle CPU, app + worker                   | ≤ 2 % of one core                   | **0 %** and **1.35 %** (two runs, 30 s each, 5 s after start)            | `TotalProcessorTime` of both processes                |
| Idle memory, app + worker                | ≤ 250 MiB                           | **33 + 113 MiB**                                                         | Working sets                                          |
| WebView2 (the page)                      | reported, not budgeted              | 6 processes, 356 MiB                                                     | `msedgewebview2.exe` processes of this app            |
| Installer                                | ≤ 80 MiB                            | **56.8 MiB**; installed **208 MiB** (worker folder 188 MiB)              | NSIS output, installed folder                         |
| Interface bundle                         | ≤ 1.5 MB minified                   | **1 300.58 kB** (gzip 360.19 kB), one chunk                              | Vite build output                                     |
| Worker reads after start                 | ≤ 250 ms                            | datasets 11 ms, export catalogue 20 ms, pipelines 72 ms                  | Sidecar smoke test                                    |
| Lead Explorer window, 1 000 038 leads    | ≤ 1 s first window, ≤ 2 s deep sort | 178 ms first window, 575 ms filtered with total, 1 702 ms unindexed sort | Phase 12 profile (`scripts/profile_lead_explorer.py`) |
| Export of 50 038 rows (JSONL)            | bounded memory                      | Python peak **< 48 MiB** (asserted), streamed in batches of 500          | `test_a_large_export_streams_in_bounded_memory`       |
| Store writes                             | ≥ 300 businesses/s                  | asserted on every test run                                               | `test_repository.py` performance test                 |

## 2. Notes on what was measured

- **Diagnostics take about 2 s** when Ollama is installed but not running: a closed loopback port on
  Windows takes that long to refuse. It is the Ollama probe, not the stores (both probes of the stores
  answer in milliseconds). The System screen shows the probe as it runs.
- **The interface bundle** is one chunk. It loads from disk inside the app, and the app shows its
  window before the worker is ready, so no startup cost was measured that splitting would remove; the
  budget stops it growing unnoticed.
- **Paging, not loading:** the explorer, the event log, exports, snapshots and every list read keyset
  or offset windows from the worker; nothing sends a whole dataset or a stored HTML body over IPC.
- **Events** are polled by the interface every 1–2 s only while something runs; a waiting pipeline
  logs at most one event every 5 minutes.

## 3. Not measured in this phase

| Area                              | Why                                                                                                                      |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Soak test of several hours        | Not run: this machine is shared (games); the pipeline and export suites exercise the same paths for minutes only         |
| GPU inference throughput and VRAM | Measured in phase 10 ([local-ai.md](local-ai.md)); Ollama was not running during phase 14                                |
| HTTP fetch and parser throughput  | Measured in phases 05 and 08 ([crawling.md](crawling.md), [website-intelligence.md](website-intelligence.md)); unchanged |
| Browser concurrency               | One browser slot by default (pipelines); not re-measured                                                                 |
| Startup at 125 % / 150 % scaling  | Not measured separately                                                                                                  |
