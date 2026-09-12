# Lead Intelligence

Local desktop application for Windows 11 that discovers, researches, enriches, classifies and
prioritises US businesses for high-quality B2B prospecting. It may look at many businesses, but its
product is a **small list of leads backed by verifiable evidence**, with a usable contact, a concrete
commercial need, ICP fit and an explanation of why each one is worth contacting.

Everything runs on this machine. No cloud service or LLM API is required.

> **Status: Phase 05 — web acquisition.** Discovery (phase 04) finds candidate references from
> interchangeable sources. The Research Center crawls their websites, or addresses you type, in two
> levels: plain HTTP first, Chromium only when a page verifiably needs JavaScript. The crawler reads
> robots.txt first, only visits public addresses, keeps a gentle pace per host, stays within page,
> byte, time and request budgets, and never works around a CAPTCHA, a login or a block. Every page
> keeps its bytes, how it was fetched and the raw findings parsed from it; crawls pause, resume and
> survive a restart without fetching anything twice. Nothing is scored yet: qualification arrives in
> later phases. The phase plan lives in [`lead_intelligence_prompts/`](lead_intelligence_prompts/).

## Requirements

| Tool                                           | Version  | Notes                                                           |
| ---------------------------------------------- | -------- | --------------------------------------------------------------- |
| Windows 11 x64                                 | —        | WebView2 ships with Windows 11                                  |
| Node.js                                        | ≥ 22     |                                                                 |
| pnpm                                           | 11       | `npm i -g pnpm`                                                 |
| Rust (rustup, `stable-x86_64-pc-windows-msvc`) | ≥ 1.88   | needs Visual Studio Build Tools (C++ workload + Windows SDK)    |
| uv                                             | ≥ 0.5    | installs Python 3.12 and the worker's locked virtualenv         |
| Ollama, NVIDIA driver                          | optional | detected at runtime; the app works without them                 |
| Chromium (Playwright's, Edge or Chrome)        | optional | only for pages that need JavaScript; Edge ships with Windows 11 |

## Commands

Run from the repository root. `pnpm run setup` must be spelled with `run` (`pnpm setup` is a
built-in pnpm command).

| Command                                           | What it does                                                                   |
| ------------------------------------------------- | ------------------------------------------------------------------------------ |
| `pnpm run setup`                                  | Install JS dependencies (frozen lockfile) and the Python worker (uv, locked)   |
| `pnpm dev`                                        | Run the desktop app with hot reload (Vite dev server + Tauri)                  |
| `pnpm check`                                      | Contracts drift check, lint + format check, typecheck and every test suite     |
| `pnpm test` · `test:ts` · `test:rust` · `test:py` | Test suites (Rust and Python tests start the real worker)                      |
| `pnpm lint` · `pnpm format`                       | Lint / auto-format TypeScript, Rust and Python                                 |
| `pnpm typecheck`                                  | `tsc` for the TS packages and `mypy --strict` for the worker                   |
| `pnpm contracts`                                  | Regenerate JSON Schema, fixtures and TS types from the Pydantic contracts      |
| `pnpm build:app`                                  | Release build of the desktop binary, no installer                              |
| `pnpm build`                                      | Release build plus NSIS installer (the Python sidecar is packaged in phase 14) |
| `pnpm clean`                                      | Delete build outputs and tool caches only — never `data/`, `logs/` or deps     |

## Layout

```text
apps/desktop/                React 19 + TypeScript (strict) + Vite + Tailwind 4 + shadcn/ui + Zustand
src-tauri/                   Rust desktop core: window, typed IPC, config, logging, worker supervisor
services/research-engine/    Python 3.12 worker (uv): protocol server, data model, repositories, probes
packages/shared-types/       Contract JSON Schema, generated TS types and cross-language fixtures
config/                      Non-secret defaults per environment (embedded into the binary)
data/  logs/                 Local stores and rotating logs in development (git-ignored)
scripts/                     Maintenance scripts (clean)
docs/                        Architecture, decisions and operations
```

## Documentation

- [docs/architecture.md](docs/architecture.md) — layers, processes, contracts, data flow
- [docs/data-model.md](docs/data-model.md) — model, provenance, data dictionary, backup and recovery
- [docs/discovery.md](docs/discovery.md) — discovery engine: sources, criteria, runs, candidates
- [docs/crawling.md](docs/crawling.md) — web acquisition: policies, SSRF, fallback, limits, metrics
- [docs/ui-system.md](docs/ui-system.md) — design tokens, navigation map, shortcuts, components
- [docs/decisions.md](docs/decisions.md) — technical decisions and their trade-offs
- [docs/operations.md](docs/operations.md) — configuration, logs, troubleshooting
- [docs/phases/](docs/phases/) — delivery report of each phase

## License

Proprietary software for the exclusive use of its owner. All rights reserved; no use, copy,
modification or distribution is permitted without prior written authorisation. See
[LICENSE](LICENSE). Third-party dependencies keep their own licenses.
