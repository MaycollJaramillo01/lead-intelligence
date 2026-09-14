# Lead Intelligence

Local desktop application for Windows 11 that discovers, researches, enriches, classifies and
prioritises US businesses for high-quality B2B prospecting. It may look at many businesses, but its
product is a **small list of leads backed by verifiable evidence**, with a usable contact, a concrete
commercial need, ICP fit and an explanation of why each one is worth contacting.

Everything runs on this machine. No cloud service or LLM API is required.

> **Status: Phase 12 — Lead Explorer and commercial research.** Discovery (phase 04) finds candidate references, the
> Research Center (phase 05) crawls their websites, entity resolution (phase 06) merges records of the
> same business, Latino evidence (phase 07) keeps explicitly evidenced ownership apart from
> Spanish-speaking market affinity, and website intelligence (phase 08) audits every crawled site
> with versioned rules. The phones, emails, forms, booking links and profiles a business publishes
> are now consolidated with where and when each was seen, and every status says exactly what its
> check proved: well formed is not valid, a domain that accepts mail is not a working mailbox, and
> nothing is ever called, emailed or submitted. People appear only when published next to their
> role, doubtful ones wait for review, addresses guessed from a pattern stay apart as hypotheses, a
> local do-not-contact list reaches every screen and export, and contactability is its own
> explainable subscore. Local AI (phase 10) runs small models on this machine through Ollama for
> extraction, classification, synthesis and explanation, one GPU task at a time: every answer
> follows a strict schema, cites only the inputs it was given, is stored raw next to what it
> validated to, and is shown as a model's answer, never as a fact; the app works without it.
> Versioned ICP profiles and scoring models (phase 11) now decide which businesses deserve contact,
> why, with which offer and how sure: gates come before weights, an unknown fact is left out instead
> of counted as zero, every factor shows its value, weight, contribution and evidence, and runs are
> history that can be compared or simulated without touching a lead. Outcomes recorded afterwards
> calibrate a run without leakage. The weights are a declared heuristic, nothing is trained and
> nothing is sent: a suggested message is only a draft. The Lead Explorer (phase 12) reads the leads
> of any run window by window, a million as lightly as ten, with AND/OR filters, versioned saved
> views, bulk actions previewed before they apply and an export preview. Its inspector gathers the
> eleven things an analyst checks; the research workspace records every decision with its reason; a
> lead becomes sales-ready only when eight exact criteria hold; and a sales brief cites the stored
> rows behind each claim, which a local model may reword but never extend. The phase plan lives in
> [`lead_intelligence_prompts/`](lead_intelligence_prompts/).

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
- [docs/resolution.md](docs/resolution.md) — normalisation, entity resolution, review, canonical values
- [docs/latino-evidence.md](docs/latino-evidence.md) — ownership vs market affinity: evidence, rules, review, limits
- [docs/website-intelligence.md](docs/website-intelligence.md) — website audits: every rule and its limits, subscores, history
- [docs/contact-intelligence.md](docs/contact-intelligence.md) — contact channels: what each status means, people, hypotheses, contactability, do-not-contact list
- [docs/local-ai.md](docs/local-ai.md) — local AI: tasks, checks, prompts, VRAM, GPU queue, security, benchmark, RTX 4060 setup
- [docs/scoring.md](docs/scoring.md) — lead scoring: facts, features, gates, formulas, versions, simulation, outcomes, calibration, limits
- [docs/lead-explorer.md](docs/lead-explorer.md) — Lead Explorer and research: analyst flow, exact sales-ready criteria, filters, views, bulk actions, sales briefs, funnel, performance
- [docs/ui-system.md](docs/ui-system.md) — design tokens, navigation map, shortcuts, components
- [docs/decisions.md](docs/decisions.md) — technical decisions and their trade-offs
- [docs/operations.md](docs/operations.md) — configuration, logs, troubleshooting
- [docs/phases/](docs/phases/) — delivery report of each phase

## License

Proprietary software for the exclusive use of its owner. All rights reserved; no use, copy,
modification or distribution is permitted without prior written authorisation. See
[LICENSE](LICENSE). Third-party dependencies keep their own licenses.
