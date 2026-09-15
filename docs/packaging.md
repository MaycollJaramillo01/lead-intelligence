# Packaging, installation and updates (Windows)

## 1. What a release is

| Artifact                                                                   | Built by              |
| -------------------------------------------------------------------------- | --------------------- |
| `src-tauri/target/release/bundle/nsis/Lead Intelligence_<v>_x64-setup.exe` | `pnpm build`          |
| `lead-intelligence.exe` (desktop core with the interface embedded)         | `tauri build`         |
| `research-engine/` (worker sidecar, one folder)                            | `pnpm build:sidecar`  |
| `dist/sbom/*.cdx.json` (CycloneDX 1.5)                                     | `pnpm run build:sbom` |

The installer holds the app and the sidecar. It never holds Ollama, models, Chromium or user data.

## 2. Build from a clean checkout

Requirements are in the README (Node 22, pnpm 11, Rust MSVC ≥ 1.88 with the Visual Studio C++ build
tools, uv ≥ 0.5). Every step installs from lockfiles:

```bash
pnpm run setup
pnpm check
pnpm build
pnpm run build:sbom
```

`pnpm build` runs the contracts check, builds the sidecar with PyInstaller from the locked
`packaging` dependency group, then `tauri build` with `src-tauri/tauri.bundle.conf.json`, which adds
the sidecar folder to the bundle's resources (kept out of the main configuration so development and
test builds do not need a sidecar).

## 3. The sidecar: PyInstaller or Nuitka

Both were tried on this machine (Windows 11, Python 3.12.13 from uv, Visual Studio 2019 Build Tools)
with the same entry point and packages:

| Criterion               | PyInstaller 6.22.3 (one folder)                                      | Nuitka 4.2.1 (standalone)                                                                |
| ----------------------- | -------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Build                   | **Succeeded in 40 s**                                                | **Failed after 246 s**: Python 3.12 needs MSVC 14.3 (Visual Studio 2022); 14.2 was found |
| Output                  | 188 MiB, 226 files; Playwright driver 102 MiB, DuckDB 36 MiB         | none                                                                                     |
| Start to `worker.ready` | 1.05 s (venv: 1.14 s)                                                | not measured                                                                             |
| Requests after start    | health 0 ms, datasets 11 ms, export catalogue 20 ms, pipelines 72 ms | not measured                                                                             |
| Idle after start        | 0.0 s CPU in 10 s, 113.8 MiB working set                             | not measured                                                                             |
| Toolchain needed        | the locked `packaging` group only                                    | a C compiler (VS 2022 or a downloaded MinGW/Zig) and long C compiles                     |
| Code protection         | bytecode in an archive (not protection)                              | compiled C (harder to read)                                                              |

**Decision (D-088): PyInstaller, one folder.** It builds from the lockfile with no extra toolchain, in
seconds, and starts as fast as the development interpreter. Nuitka's main advantage, compiled code,
protects nothing that matters here (the worker holds no secret), and it could not build with the
compilers this project requires. One folder rather than one file: a one-file build unpacks about
190 MiB to `%TEMP%` on every start, which is slower and scanned again by antivirus each time.

`diagnostics` takes about 2 s in both because it probes Ollama, and a closed loopback port on Windows
takes that long to refuse.

## 4. Chromium and Playwright

The worker ships Playwright's driver (about 100 MB, inside the sidecar) but no browser. For pages
that need JavaScript the crawler tries, in order:

1. Playwright's Chromium, if installed under `%LOCALAPPDATA%\ms-playwright` (or
   `PLAYWRIGHT_BROWSERS_PATH`);
2. Microsoft Edge (installed with Windows 11);
3. Google Chrome.

Each rendered page records the browser and its version. When none launches, the crawl keeps the
plain HTTP fetch, marks the page as not rendered and stores why (the reason is kept, so a crawl does
not pay three failed launches per page). Updating Edge or Chrome is Windows' job; a development
checkout installs Playwright's Chromium with `uv run --directory services/research-engine playwright
install chromium`.

## 5. Ollama and models

Not bundled. The System and AI Models screens detect an Ollama on loopback
(`ollama.base_url`, default `http://127.0.0.1:11434`), its models and the GPU. Without it every
AI task is skipped with a reason and the rest of the app works (phase 10).

## 6. Installer, paths and uninstall

- NSIS, per-user (`installMode: currentUser`): no administrator rights, installed under
  `%LOCALAPPDATA%\Lead Intelligence`; English and Spanish; installing an older version over a newer
  one is refused (`allowDowngrades: false`), because a store migrated by a newer build cannot be
  opened by an older one.
- Paths:

| What    | Where (release)                                                                           |
| ------- | ----------------------------------------------------------------------------------------- |
| App     | `%LOCALAPPDATA%\Lead Intelligence\lead-intelligence.exe`                                  |
| Worker  | `%LOCALAPPDATA%\Lead Intelligence\research-engine\research-engine.exe`                    |
| Data    | `%LOCALAPPDATA%\com.leadintelligence.desktop\data` (stores, datasets, artifacts, backups) |
| Logs    | `%LOCALAPPDATA%\com.leadintelligence.desktop\logs`                                        |
| Config  | `%APPDATA%\com.leadintelligence.desktop\config.toml`                                      |
| WebView | `%LOCALAPPDATA%\com.leadintelligence.desktop\EBWebView`                                   |
| Exports | Wherever the analyst saves them (save dialog)                                             |

- `LEAD_DATA_DIR`, `LEAD_LOG_DIR` or `[paths]` in the config move data and logs. The data folder
  path may have at most 140 characters: Windows stops paths at 260 without long-path support, and
  the worker's deepest file (a snapshot partition) adds 108. The core refuses a longer folder before
  starting the worker, and says why, instead of failing on the first file written.
- Uninstalling removes the program files. Data, logs and config stay unless the analyst ticks the
  uninstaller's **Delete the application data** box, which is off by default.
- Antivirus: the only processes are `lead-intelligence.exe`, `research-engine.exe` (a PyInstaller
  folder, not a self-extracting file) and, while crawling, `node.exe` from Playwright's driver plus
  `msedge.exe`/`chrome.exe`. No service, no scheduled task, no port.

## 7. Signing

Nothing is signed today: no certificate exists, and none is invented. Windows SmartScreen will warn on
first install. To sign a release:

1. Obtain an OV or EV code-signing certificate (or a cloud signing service such as Azure Trusted
   Signing) for the publisher.
2. Before `tauri build` (Tauri does not sign resources), sign the worker:

   ```bash
   signtool sign /fd sha256 /tr <timestamp url> /td sha256 services/research-engine/dist/research-engine/research-engine.exe
   ```

3. Set `bundle.windows.certificateThumbprint`, `digestAlgorithm: "sha256"` and `timestampUrl` (or
   `signCommand` for a cloud service) so Tauri signs the app and the installer.
4. Check with `signtool verify /pa /v` on the three files.

Security checks are never disabled to get an unsigned build through.

## 8. Updates

Not configured, because no update server exists and none is invented. The design, when one exists:

- `tauri-plugin-updater` with a minisign key pair (private key outside the repository),
  `bundle.createUpdaterArtifacts: true`, and a static JSON manifest served over HTTPS;
- the updater verifies the signature before installing and the installer refuses downgrades;
- on the first start of a new version the worker backs up SQLite and DuckDB before running
  migrations (phase 03), quarantines a store that fails its integrity check and restores the newest
  verified backup; a store with migrations unknown to the build is refused with "database contains
  migrations … unknown to this build (newer app version?)"; contracts carry `protocol_version` and
  `data_contract_version`, and snapshots their `schema_version` (older schemas are read, newer ones
  refused).

Rollback: reinstall the previous installer after restoring the backup taken before the migration
(`data/backups/`, [operations.md](operations.md#recover-a-damaged-database)).
