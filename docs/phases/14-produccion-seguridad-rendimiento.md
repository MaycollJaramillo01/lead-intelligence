# Fase 14 — Producción, seguridad, rendimiento y empaquetado Windows

Fecha: 2026-09-15 · Estado: **completada con pendientes documentados** (sección 9); resultados
exactos en la sección 8.

## 1. Punto de partida

La fase parte de la fase 13 terminada en el mismo worktree (`fase-5-1a1090`), sin commit todavía.

- **Instalador:** `tauri build` generaba un NSIS sin el worker: la app instalada no podía arrancarlo.
- **Seguridad:** CSP estricta, SSRF guard, rutas relativas validadas y ningún puerto abierto desde las
  fases 01–09; la ventana tenía el permiso amplio `core:default` y dos instancias podían abrir los
  mismos almacenes.
- **Rendimiento:** medido por fase (explorador de un millón de filas, parser, GPU), sin presupuestos
  de arranque, reposo ni tamaño del paquete.

## 2. Qué se hizo

| Área                   | Cambio                                                                                                                                                                                                                            |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Sidecar                | PyInstaller en modo carpeta desde el grupo bloqueado `packaging` (`packaging/research-engine.spec`, `pnpm build:sidecar`); comparado con Nuitka en esta máquina (D-088)                                                           |
| Bundle e instalador    | `tauri.bundle.conf.json` añade el worker a los recursos solo en `pnpm build`; NSIS por usuario, inglés y español, sin downgrades, metadatos de editor; el core busca `research-engine\research-engine.exe` junto a la app (D-089) |
| Una sola instancia     | `tauri-plugin-single-instance`: un segundo lanzamiento enfoca la ventana abierta (D-090)                                                                                                                                          |
| Permisos de la ventana | `core:default` → solo `core:event:allow-listen` y `core:event:allow-unlisten` (D-090)                                                                                                                                             |
| Rutas de Windows       | Defecto encontrado: con la carpeta de datos a 157 caracteres, la semilla fallaba al escribir un artefacto (ruta de más de 260). Nombres temporales cortos y límite de 140 caracteres comprobado antes de lanzar el worker (D-091) |
| Dependencias           | SBOM CycloneDX (`pnpm run build:sbom`), `pnpm audit`, `pip-audit`, OSV sobre `Cargo.lock`; `time` 0.3.55 y `serde_with` 3.23.0 actualizadas (D-092)                                                                               |
| Pruebas nuevas         | `test_a_stored_path_never_leaves_the_data_directory` (traversal, 6 casos); Rust `a_data_folder_too_deep_for_windows_paths_is_refused_before_launch`                                                                               |
| Documentación          | [security.md](../security.md) (threat model), [packaging.md](../packaging.md) (build, Chromium, Ollama, rutas, desinstalación, firma, updates), [performance.md](../performance.md) (presupuestos), README, arquitectura, runbook |
| Scripts                | `scripts/sbom.py`, `scripts/integrity_check.py` (consultas de integridad de solo lectura, usadas también en la fase 15)                                                                                                           |

## 3. Requisitos del prompt

| Requisito                                                  | Estado                                                                                                                                          |
| ---------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| React/Tauri en producción sin dev server                   | ✓ `tauri build`: interfaz embebida en `lead-intelligence.exe`                                                                                   |
| Sidecar con Nuitka o PyInstaller tras comparación práctica | ✓ PyInstaller construyó en 40 s; Nuitka falló en esta máquina (MSVC 14.2, Python 3.12 exige 14.3). Decisión en D-088                            |
| Solo dependencias necesarias                               | ✓ `--no-dev` en el SBOM; mypy, pytest, ruff y pygments excluidos del sidecar                                                                    |
| Playwright/Chromium explícitos                             | ✓ Documentado: driver incluido, sin navegador; orden Chromium de Playwright → Edge → Chrome; versión registrada por página; ausencia con motivo |
| Ollama/modelos fuera del instalador                        | ✓ Detectado en System y AI Models; la app funciona sin IA                                                                                       |
| Bundle con versión semántica, iconos y metadata            | ✓ 0.1.0, iconos, editor, copyright, categoría y descripciones                                                                                   |
| Firma de código documentada sin inventar certificados      | ✓ Proceso en packaging.md; los binarios salen **sin firmar**                                                                                    |
| Desinstalar no borra datasets sin opción clara             | ✓ Verificado: tras desinstalar, `lead.sqlite3` sigue; borrar datos es una casilla desmarcada del desinstalador                                  |
| Rutas de app data, cache, logs, exports, config            | ✓ Tabla en packaging.md                                                                                                                         |
| Update firmado sin endpoints inexistentes                  | ✓ Diseño con `tauri-plugin-updater` y minisign documentado; **no configurado**                                                                  |
| Migraciones antes de abrir datos, backup y rollback        | ✓ Existente desde la fase 03 (backup verificado antes de migrar, cuarentena, restauración); rollback documentado                                |
| Mensajes si la versión es muy nueva o vieja                | ✓ Store con migraciones desconocidas rechazado con mensaje; instalador sin downgrades; contratos con versión                                    |
| Threat model de los 12 temas                               | ✓ security.md, con mitigación y evidencia por tema                                                                                              |
| Allowlist mínima de capabilities                           | ✓ Solo eventos; comandos propios validados en Rust (lista por comando pendiente, security.md §3)                                                |
| Procesos sin shell                                         | ✓ Dos subprocesos Python con ruta absoluta y argumentos fijos; el core sin shell                                                                |
| Validación canónica de rutas                               | ✓ `data_path` probado contra traversal; destinos de export validados; carpeta de datos acotada                                                  |
| Secretos en credential manager                             | ✓ No aplica: la app no guarda secretos (D-090)                                                                                                  |
| Red local en loopback                                      | ✓ Ningún puerto; Ollama solo en loopback                                                                                                        |
| SBOM y escaneo                                             | ✓ Sección 5                                                                                                                                     |
| Sin telemetría externa                                     | ✓ No existe                                                                                                                                     |
| Perfilado con presupuestos                                 | ✓ performance.md; soak test de horas **no ejecutado**                                                                                           |
| Supervisor, shutdown limpio, lock de instancia             | ✓ Supervisor con presupuesto de reinicios (fase 01); cierre verificado; instancia única verificada                                              |
| Disco lleno, permisos, base corrupta, sin GPU/Ollama/red   | ✓ Probado en fases previas y en exports (disco lleno, permisos); Ollama ausente verificado ahora                                                |
| Logs rotativos y support bundle con redacción              | Logs rotativos ✓; **support bundle no implementado**                                                                                            |
| Backups configurables y restore probado                    | ✓ Backups verificados y restauración de la fase 03 probados en `test_storage.py`; retención fija de 10                                          |
| UX de primera ejecución                                    | Parcial: System detecta dependencias y estados reales; carpeta de datos por configuración; **sin asistente, términos ni demo en release**       |

## 4. Seguridad

Detalle en [security.md](../security.md). Escaneo de secretos con `git grep` de patrones de claves
(AWS, GitHub, OpenAI, Slack, Google, claves privadas y asignaciones `api_key|secret|password|token`)
sobre los archivos versionados: **sin coincidencias**.

## 5. SBOM y vulnerabilidades

| Ecosistema                | Componentes en el SBOM | Escaneo                                           | Resultado                                                                                                                                                                                                                                                                         |
| ------------------------- | ---------------------- | ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Interfaz (pnpm, `--prod`) | 97                     | `pnpm audit --prod`                               | Sin vulnerabilidades conocidas                                                                                                                                                                                                                                                    |
| Worker (uv, `--no-dev`)   | 19                     | `pip-audit` sobre los requisitos exportados       | Sin vulnerabilidades conocidas                                                                                                                                                                                                                                                    |
| Core (Cargo)              | 259                    | OSV `querybatch` sobre 450 crates de `Cargo.lock` | 10 avisos: `time` (DoS, corregido → 0.3.55) y `serde_with` (pánico, corregido → 3.23.0); `quick-xml` 0.38 (DoS, arreglo en 0.41 incompatible, solo en build: parsea la configuración propia); `glib` (solo Linux); `proc-macro-error` y cuatro `unic-*` sin mantenimiento (build) |

## 6. Rendimiento

Presupuestos y mediciones en [performance.md](../performance.md). Resumen: la app instalada tiene el
worker listo en 1,75 s, en reposo usa 0–1,35 % de un núcleo y 146 MiB entre core y worker (más
356 MiB de WebView2); instalador de 56,8 MiB.

## 7. QA de instalación (Windows 11, instalador real)

Script de QA con instalación silenciosa por usuario en una carpeta de prueba y datos y logs en una
carpeta aparte (los datos reales no se tocan). Segunda corrida (la primera cortó en una línea del
script y midió mal el reinicio):

| Paso                             | Resultado                                                                                      |
| -------------------------------- | ---------------------------------------------------------------------------------------------- |
| Instalar (`/S`)                  | exit 0 en 6,1 s; app, `research-engine\research-engine.exe` y desinstalador presentes; 208 MiB |
| Primer inicio                    | Worker en marcha a los 1,75 s; almacenes creados                                               |
| Reposo 30 s                      | 0 % de CPU; core 33 MiB, worker 113 MiB; WebView2 6 procesos, 356 MiB                          |
| Segundo lanzamiento              | Sigue habiendo **1** proceso de la app                                                         |
| Cerrar la ventana                | El worker sale en 0,2 s; `desktop core exited` en el log                                       |
| Reiniciar sobre los mismos datos | Worker en marcha a los 1,48 s; el almacén conserva sus 1 568 768 bytes                         |
| Desinstalar (`/S`)               | exit 0; programa eliminado; **datos conservados**                                              |

No probado: actualización de una versión a otra (no hay versión anterior instalable ni feed),
SmartScreen con descarga real, 125/150 % de escalado.

## 8. Comandos ejecutados y resultados exactos

Ejecutados en `fase-5-1a1090`, en este orden:

| Comando                                                                           | Resultado                                                                                                                                                                                                                                                                          |
| --------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `uv add --group packaging "pyinstaller>=6.22"`                                    | `pyproject.toml` y `uv.lock` con PyInstaller 6.22.3 y sus hooks                                                                                                                                                                                                                    |
| `cargo add tauri-plugin-single-instance@2` · `cargo update -p time -p serde_with` | single-instance 2.4.4; `time` 0.3.45 → 0.3.55; `serde_with` → 3.23.0                                                                                                                                                                                                               |
| `pnpm build:sidecar`                                                              | exit 0 en 40 s; `dist/research-engine/` de 188 MiB y 226 archivos; aviso de PyInstaller: `tzdata` no encontrado (el worker no usa `zoneinfo`)                                                                                                                                      |
| Nuitka 4.2.1 `--standalone`                                                       | exit 1 en 246 s: MSVC 14.2 hallado, Python 3.12 exige 14.3                                                                                                                                                                                                                         |
| Smoke test del sidecar (JSON Lines)                                               | `worker.ready` en 1,05 s; health, diagnostics, datasets, catálogo de exports y pipelines sin error; salida limpia (exit 0); venv: 1,14 s                                                                                                                                           |
| `pnpm run build:sbom`                                                             | Primer intento con `pnpm sbom`: el comando nativo de pnpm tapaba el script (renombrado a `build:sbom`). Luego 259 crates, 19 paquetes Python y 97 JS                                                                                                                               |
| `pnpm audit --prod` · `pip-audit` · OSV `querybatch`                              | Sin vulnerabilidades · sin vulnerabilidades · 10 avisos en 450 crates (triage en la sección 5)                                                                                                                                                                                     |
| `git grep` de patrones de secretos                                                | Sin coincidencias                                                                                                                                                                                                                                                                  |
| `cargo clippy --all-targets -D warnings` (1.ª)                                    | exit 101: `collapsible_if` en el cierre de single-instance. Corregido con un `let` encadenado                                                                                                                                                                                      |
| `cargo test` (tras single-instance y permisos)                                    | **60 ✓** en 96 s                                                                                                                                                                                                                                                                   |
| Semilla sintética en la carpeta del scratchpad (157 caracteres)                   | **Fallo real**: `FileNotFoundError` al escribir un artefacto (ruta de más de 260). Corregido (D-091) y verificado con una carpeta corta: 38 negocios en 0,7 s; archivo más profundo a 108 caracteres de la carpeta                                                                 |
| `pytest tests/test_crawl.py tests/test_website.py tests/test_latino.py`           | **38 ✓** tras acortar los nombres temporales                                                                                                                                                                                                                                       |
| `cargo fmt --check` · `cargo clippy` · `cargo test --lib`                         | exit 0 · exit 0 · **23 ✓** (incluida la prueba del límite de carpeta)                                                                                                                                                                                                              |
| `pnpm build`                                                                      | exit 0 en 339 s: Vite 1 300,58 kB, `cargo build --release` en 3 min 2 s, `Lead Intelligence_0.1.0_x64-setup.exe` de 56,8 MiB                                                                                                                                                       |
| Script de QA de instalación (1.ª)                                                 | Cortado en una línea que sumaba `TimeSpan` tras instalar y arrancar; el reinicio también se medía mal (leía el log anterior). Corregidos                                                                                                                                           |
| Script de QA de instalación (2.ª)                                                 | Todo en verde (sección 7)                                                                                                                                                                                                                                                          |
| `scripts/integrity_check.py` sobre la semilla                                     | exit 0 tras excluir la evidencia de registros fusionados (legítima); informativo: 2 dominios compartidos intencionados                                                                                                                                                             |
| `pnpm check` (1.ª)                                                                | exit 1 en 13 s: `prettier` sobre 5 documentos editados                                                                                                                                                                                                                             |
| `pnpm check` (2.ª)                                                                | **exit 0 en 354 s**: contratos, prettier, oxlint, clippy, ruff, `tsc` y mypy en verde; `pytest` **758 ✓, 6 omitidas** (Ollama aún no corría: 5; fuentes en vivo opt-in: 1); `vitest` **247 ✓** en 42 archivos; `cargo test` **61 ✓** (23 unitarias, 25 contratos, 5 IPC, 8 worker) |

## 9. Riesgos y pendientes

| Asunto                                                                  | Severidad | Próximo paso                                        |
| ----------------------------------------------------------------------- | --------- | --------------------------------------------------- |
| Binarios sin firmar (no hay certificado)                                | alta      | Validación externa: comprar certificado y firmar    |
| Sin feed de actualizaciones                                             | media     | Publicar un feed firmado cuando exista servidor     |
| Sin support bundle con redacción                                        | media     | Comando que empaquete logs y diagnóstico redactados |
| Sin asistente de primera ejecución (carpeta, términos, demo en release) | media     | Asistente sobre la pantalla System                  |
| Soak test de horas no ejecutado                                         | media     | Ejecutarlo en una máquina dedicada                  |
| Comandos propios sin lista por comando en un app manifest               | baja      | `tauri_build::AppManifest` con la lista             |
| `quick-xml` 0.38 con dos avisos de DoS (solo en build)                  | baja      | Actualizar cuando `plist`/Tauri lo hagan            |
| Nuitka no evaluado con VS 2022                                          | baja      | Solo si se necesita código compilado                |
| Bundle JS de 1,3 MB en un chunk                                         | baja      | Dividir si se mide un coste de arranque             |

## 10. Archivos

**Nuevos:** `services/research-engine/packaging/research-engine.spec`,
`src-tauri/tauri.bundle.conf.json`, `scripts/sbom.py`, `scripts/integrity_check.py`,
`docs/security.md`, `docs/packaging.md`, `docs/performance.md`, este reporte.

**Modificados:** `package.json` (`build`, `build:sidecar`, `build:sbom`),
`services/research-engine/pyproject.toml` y `uv.lock` (grupo `packaging`), `src-tauri/Cargo.toml` y
`Cargo.lock` (single-instance, `time`, `serde_with`), `src-tauri/tauri.conf.json` (metadata y NSIS),
`src-tauri/capabilities/default.json`, `src-tauri/src/lib.rs` (instancia única, sidecar en carpeta,
límite de la carpeta de datos y su prueba), `crawl/engine.py` (nombres temporales cortos),
`tests/test_storage.py`, `README.md`, `docs/architecture.md`, `docs/operations.md`,
`docs/decisions.md` (D-088…D-092), `docs/scoring.md`.
