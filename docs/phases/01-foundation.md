# Fase 01 — Foundation, arquitectura y scaffolding

Fecha: 2026-09-11 · Estado: **completada y verificada en Windows 11** (RTX 4060, driver 610.88).

## 1. Resumen de arquitectura

Tres procesos con responsabilidades separadas (detalle en [architecture.md](../architecture.md)):

- **UI React** (WebView2): presentación y estado efímero con Zustand. Solo habla con Rust mediante
  un adaptador IPC tipado (`apps/desktop/src/lib/ipc.ts`); nunca ejecuta SQL ni `invoke` directo.
- **Núcleo Rust/Tauri**: ventana, superficie IPC (3 comandos + 1 evento), configuración por capas,
  logging estructurado y el supervisor del worker (instancia única, handshake, health check,
  watchdog, reinicio controlado con presupuesto, apagado limpio).
- **Worker Python** (sidecar, uv): servidor JSON Lines por stdin/stdout (protocolo v1), dueño de
  SQLite y DuckDB con migraciones versionadas, sondas reales de Ollama y GPU.

Los contratos entre procesos tienen una única fuente de verdad (Pydantic) de la que se generan
JSON Schema, tipos TypeScript y fixtures que Rust valida con round-trip.

## 2. Árbol principal

```text
apps/desktop/                 React 19 + TS estricto + Vite 8 + Tailwind 4 + shadcn/ui (Radix) + Zustand
  src/lib/ipc.ts              adaptador IPC tipado + IpcError
  src/lib/errors.ts           reporte de errores de UI al log del núcleo
  src/components/             ErrorBoundary, ui/button (shadcn)
  src/features/diagnostics/   pantalla temporal de diagnóstico (modelo, store, vista, tests)
src-tauri/                    núcleo Rust (edition 2024, unsafe prohibido)
  src/config.rs               configuración por capas validada
  src/logging.rs              tracing JSON con rotación diaria
  src/protocol.rs             espejo de los contratos
  src/worker.rs               supervisor del worker
  src/commands.rs             comandos IPC
  tests/                      contratos, IPC (runtime mock), supervisor con worker real
services/research-engine/     worker Python 3.12 (uv, lockfile)
  src/research_engine/        worker, handlers, contratos, storage, ollama, gpu, logs, settings
  src/research_engine/migrations/{sqlite,duckdb}/
  tests/                      proceso real, huérfanos, storage, sondas, contratos
packages/shared-types/        schema JSON, tipos TS generados, fixtures sintéticos
config/                       default/development/production.toml (sin secretos, embebidos)
docs/                         arquitectura, decisiones, operación, informes de fase
scripts/clean.mjs             limpieza de artefactos generados únicamente
```

## 3. Decisiones técnicas

Registradas en [decisions.md](../decisions.md) (D-001 a D-009). Las principales:

| #     | Decisión                                                                                    |
| ----- | ------------------------------------------------------------------------------------------- |
| D-002 | Rust ↔ Python por **JSON Lines sobre stdio** (sin puerto, vida atada al padre por EOF)      |
| D-003 | **Pydantic** como fuente de verdad → JSON Schema + TS generados + fixtures con round-trip   |
| D-004 | El **worker es el único dueño** de SQLite y DuckDB; migraciones SQL con checksum            |
| D-005 | Configuración **embebida** + overlay por entorno + archivo de usuario + `LEAD_*`            |
| D-007 | Supervisión: handshake + health, watchdog 30 s, 3 reinicios / 5 min, apagado → EOF → kill   |
| D-008 | Detección sin efectos: Ollama por API (sin descargas), GPU por nvidia-smi + CUDA driver API |

## 4. Archivos creados

103 archivos nuevos (repositorio sin commits previos), más 16 iconos generados por `tauri init`;
los prompts existentes no se tocaron. Grupos:

- Raíz: `package.json`, `pnpm-workspace.yaml`, `pnpm-lock.yaml`, `.gitignore`, `.gitattributes`,
  `.prettierrc.json`, `.prettierignore`, `README.md`.
- `apps/desktop/`: configuración (Vite, TS, oxlint, shadcn `components.json`), 11 archivos de
  código y 4 de tests.
- `src-tauri/`: `Cargo.toml`, `Cargo.lock`, `build.rs`, `tauri.conf.json`, capacidades,
  manifiesto Windows, 7 módulos y 4 archivos de tests.
- `services/research-engine/`: `pyproject.toml`, `uv.lock`, 11 módulos, 2 migraciones, 8 tests.
- `packages/shared-types/`: schema, 7 fixtures, tipos generados, script de generación.
- `config/` (3), `docs/` (4 + este informe + capturas), `scripts/clean.mjs`.

## 5. Comandos ejecutados (principales)

```text
winget install --id Rustlang.Rustup            # Rust 1.98.1 (no estaba instalado)
pnpm create vite@latest apps/desktop --template react-ts --no-interactive
uv init --package --python 3.12 --name research-engine services/research-engine
uv add pydantic httpx orjson duckdb ; uv add --dev pytest ruff mypy
pnpm add ... ; cargo add ...                   # dependencias con versiones resueltas
pnpm tauri init --ci -A "Lead Intelligence" ...
pnpm contracts                                 # schema + fixtures + tipos TS
pnpm check                                     # verificación completa
pnpm build:app                                 # build de release
pnpm dev                                       # ejecución real + capturas
```

## 6. Resultados exactos

| Verificación                                          | Resultado                                                             |
| ----------------------------------------------------- | --------------------------------------------------------------------- |
| `pnpm check` (contratos, lint, formato, tipos, tests) | **exit 0** en 69 s                                                    |
| Python: ruff, ruff format, mypy `--strict`            | sin errores (19 archivos)                                             |
| Python: pytest                                        | **60 passed, 1 skipped** (Ollama detenido en ese momento)             |
| Python: pytest `-m local_services` con Ollama activo  | **2 passed** (GPU real y Ollama real)                                 |
| TypeScript: oxlint `--deny-warnings`, prettier, tsc   | sin errores                                                           |
| TypeScript: vitest                                    | **16 passed** (4 archivos)                                            |
| Rust: rustfmt, clippy `-D warnings`                   | sin avisos                                                            |
| Rust: cargo test                                      | **27 passed** (16 unitarios, 3 contratos, 1 IPC, 7 supervisor)        |
| `pnpm build:app` (release)                            | exit 0 en 144 s; exe 11,4 MB; JS 268,9 kB (85,5 kB gzip), CSS 15,8 kB |

Criterios de aceptación del prompt:

| Criterio                                                 | Evidencia                                                                                                                                                                                           |
| -------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Frontend compila, typecheck y lint                       | `pnpm check`, `pnpm build:app`                                                                                                                                                                      |
| Rust compila y sus pruebas pasan                         | 27/27                                                                                                                                                                                               |
| Python instala con lockfile y pasa pruebas               | `uv.lock`; 60 + 2 locales                                                                                                                                                                           |
| Sidecar inicia, responde health y se apaga sin huérfanos | tests `real_worker_starts_answers_and_stops_without_leftovers`, `test_worker_exits_when_its_parent_is_killed`; app real: kill forzado → procesos del worker fuera en 0,29 s; cierre normal → exit 0 |
| IPC entrega respuesta real del worker a la UI            | test `ui_commands_reach_the_real_worker_through_ipc` + app real con `ipc_id`/`request_id` correlacionados en el log + [captura](img/01-dev-diagnostics.png)                                         |
| SQLite y DuckDB crean/abren archivos y ejecutan consulta | migración 0001 + `quick_check` / consultas reales; tests de rollback, checksum y versión futura                                                                                                     |
| Ollama disponible y ausente                              | app real: "Degraded — 0.32.9 running, no models" y tras detenerlo "Unavailable — installed but not running" ([captura](img/01-dev-after-crash.png))                                                 |
| GPU reportada o degradación explícita                    | RTX 4060 · 8188 MiB · driver 610.88 · CUDA 13.3; tests de ausencia/timeout/sin CUDA                                                                                                                 |
| La app abre sin depender de internet                     | assets embebidos en release, CSP sin orígenes remotos, bundle sin cargas externas ([captura release](img/01-release.png))                                                                           |

También verificado en la app real: se mató el intérprete del worker, el supervisor lo reinició (generación 2) en unos 1,1 s y la UI se refrescó sola al recibir el evento.

## 7. Riesgos y pendientes

1. **Sidecar sin empaquetar**: la release busca `research-engine.exe` junto al ejecutable y hoy
   muestra "worker executable not found" (comportamiento honesto). Nuitka/PyInstaller es fase 14.
   El instalador NSIS (`pnpm build`) no se ejecutó.
2. **Sin bloqueo de instancia única de la app**: dos instancias chocarían en el lock de DuckDB
   (error visible, sin corrupción). Previsto para fase 14; conviene adelantarlo si molesta.
3. **Endurecimiento pendiente**: capacidades Tauri en `core:default` y CSP desactivada en dev
   (fase 14).
4. **Job Object de Windows** para todo el árbol del worker: necesario cuando llegue Chromium
   (fase 05).
5. **Sin E2E de UI automatizado** (tauri-driver/WebDriver): la IPC se prueba con el runtime mock de
   Tauri y la UI se verificó ejecutando la app real con capturas.
6. **"Sin internet" verificado por construcción** (assets embebidos, CSP, bundle auditado), no
   desconectando la red físicamente.
7. **Entorno de este equipo**:
   - La caché de uv tiene entradas rotas creadas bajo la virtualización MSIX de una aplicación empaquetada
     (`os error 1921`); se usó `uv --no-cache` como alternativa.
   - El `.git` pertenece a otra cuenta de Windows (_dubious ownership_): no se hizo ningún commit.
   - Rust se instaló con winget en `%USERPROFILE%\.cargo`.
   - La ejecución de release creó `%LOCALAPPDATA%\com.leadintelligence.desktop\logs`.
