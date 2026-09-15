# Fase 13 — Orquestación de jobs, datasets, exportaciones y operación

Fecha: 2026-09-15 · Estado: **completada**; resultados exactos en la sección 6.

## 1. Punto de partida

La fase parte de `main` con la fase 12 cerrada (`e3ee45b`). Una sesión anterior dejó el trabajo sin
commit en el worktree `fase-7-finalizacion-f4e335`; se copió
tal cual a `fase-5-1a1090` para terminarlo. El checkout principal conserva otro intento más antiguo y
parcial (sin UI ni pruebas de exportación), que no se usó.

- **Jobs.** Cada engine tenía sus jobs reanudables con checkpoints, límites y cancelación (D-027),
  pero nada los encadenaba: cada etapa se lanzaba a mano desde su pantalla.
- **Datasets.** Un snapshot copiaba los negocios a DuckDB y Parquet con lineage de fuentes, sin
  fecha de corte, versiones de pipeline, reporte de calidad, diff ni retención.
- **Exportaciones.** Solo el export preview del Lead Explorer (fase 12), que no escribe nada.
- **Logs y Exports** figuraban en la navegación como «fase 13».

## 2. Qué se construyó

```text
Jobs ▸ Pipelines: un job `pipeline` con 10 etapas fijas (DAG)
  discovery → acquisition → normalization → entity resolution
    → latino evidence · website intelligence · contact intelligence → local AI
    → scoring → export eligibility (leads elegibles + snapshot del proyecto)
  cada etapa: adjunta el job abierto del engine o lo inicia; reintento o dead letter
Datasets ▸ snapshot: corte, calidad, lineage, diff, retención con preview
Lead Explorer / snapshot ▸ Export wizard → archivo local + manifest (Exports: historial y presets)
Logs: eventos persistidos filtrables, limpieza con preview (los errores nunca se borran)
```

- **`operations.py`** (Python): planificación del pipeline con idempotencia por clave, claim atómico
  de etapas (dependencias, una etapa por proyecto, slots por recurso, presión de RAM y disco),
  adjuntar o iniciar el job del engine (`task_attempt_jobs`), espera cooperativa, pausa, reanudación
  y cancelación propagadas, clasificación de errores, backoff, dead letters, vista con intentos, jobs
  hijos con ritmo y ETA estimados, uso de recursos (CPU, RAM, VRAM, disco, navegadores activos),
  log de eventos filtrable y limpieza.
- **`datasets.py`**: snapshots de esquema 2 leídos en una transacción (corte), reporte de calidad
  medido desde el Parquet, lineage, diff, retención que respeta exportaciones y derivados.
- **`exports.py`**: catálogo de columnas con descripción, 5 presets versionados, plan y preview,
  escritores en streaming (CSV con BOM opcional, JSON, JSONL, XLSX, Parquet), neutralización de
  fórmulas y límites de Excel, `.partial` → final con fsync, SHA-256 y manifest, cancelación y
  limpieza, recuperación al arrancar, historial.
- **Migraciones:** SQLite `0012_operations.sql` (DAG fijo, `task_attempt_jobs`,
  `dead_letter_tasks`, columnas de reproducibilidad en `dataset_snapshots`, columnas de historial en
  `exports`, `export_presets` inmutables) y DuckDB `0003_snapshot_scores.sql`.
- **17 mensajes IPC** (`pipelines.*`, `jobs.events`, `jobs.cleanup_events`, `datasets.*`,
  `exports.*`) con espejo validado en Rust, más `choose_export_destination`, que solo existe en el
  core: abre el diálogo nativo (`tauri-plugin-dialog`) y entrega a la página una concesión de un
  solo uso en lugar de una ruta.
- **UI:** `PipelinesPanel` (en Jobs), `SnapshotsPanel` (en Datasets), `ExportWizard`,
  `ExportsScreen`, `LogsScreen`; enlaces `?snapshot=` y `/logs?job=`.

Referencia operativa en [operations.md](../operations.md#operational-runbook-phase-13). Decisiones
D-083 a D-087 en [decisions.md](../decisions.md).

## 3. Requisitos del prompt y cómo se cumplen

| Requisito                                                                | Mecanismo                                                                                                                                                                      |
| ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| DAG explícito con inputs, outputs, versión, dependencias, retry          | `PIPELINE` en `operations.py`; copiado a `pipeline_stage_specs` y `pipeline_stage_dependencies` al encolar, con triggers que impiden cambiarlo (D-083)                         |
| Estados válidos y transiciones atómicas                                  | `repository.change_status` contra `status_transitions` dentro de `atomic`                                                                                                      |
| Checkpoints y reinicio sin perder trabajo                                | Checkpoint por etapa; los jobs del engine quedan en `task_attempt_jobs` antes de esperar; al reanudar se continúan, no se inician otra vez                                     |
| Idempotencia por task key                                                | `pipeline:<clave>` como `idempotency_key` único; `task_key` por etapa; la UI conserva la clave de un inicio fallido                                                            |
| Cancelación cooperativa, pause/resume reales                             | Pausa y cancelación llegan al job del engine en curso; cancelar también cancela los jobs que el pipeline inició                                                                |
| Retry solo recuperable; dead letter                                      | `classify`: 6 códigos recuperables con backoff; lo demás crea `dead_letter_tasks` y una persona reintenta u omite con motivo (D-084)                                           |
| Prioridades, límites por recurso y backpressure                          | Slots por HTTP, navegador, CPU, RAM, disco y GPU; etapas que esperan con `slot_*`, `low_ram`, `low_disk`; una etapa por proyecto (`project_busy`)                              |
| Sin escrituras incompatibles                                             | Una etapa a la vez por proyecto entre todos los pipelines                                                                                                                      |
| Observabilidad                                                           | Progreso por etapa, conteos por estado (incluido omitido), intentos con causa, jobs hijos con ritmo y ETA marcados como estimación, recursos de la máquina, eventos filtrables |
| Sin inundar la UI                                                        | La UI consulta cada 1–2 s solo mientras algo corre; `pipeline_waiting` se registra como mucho cada 5 min por pipeline                                                          |
| Retención configurable de eventos                                        | `jobs.cleanup_events`: por antigüedad y nivel, preview y motivo; los errores se conservan                                                                                      |
| Snapshot reproducible con versión de esquema, corte, versiones y lineage | Esquema 2, `cutoff_at`, `query_json`, `pipeline_versions_json`, `dataset_snapshot_sources` (D-085)                                                                             |
| Parquet tipado, particionado y comprimido; DuckDB sin cargar todo        | Parquet por región con tipos explícitos; calidad y diff consultan el Parquet desde DuckDB                                                                                      |
| Quality report                                                           | Filas, duplicados, cobertura y desconocidos por campo, estados, tiers, bandas de score, sin score, no contactar, filas stale, frescura                                         |
| Diff entre snapshots                                                     | Añadidos, removidos y cambiados con los campos que cambiaron                                                                                                                   |
| Retención segura con preview                                             | Conserva las N más recientes, las exportadas y las que derivan de ellas; preview y motivo                                                                                      |
| CSV (BOM), XLSX, JSON/JSONL, Parquet                                     | Escritores en streaming por lotes de 500 (D-086)                                                                                                                               |
| Cantidad y muestra real; filtros/vista/snapshot exactos                  | `exports.preview` sobre la misma consulta o snapshot que escribe `exports.start`                                                                                               |
| Excluir no contactar y bloquear inclusión accidental                     | Excluidos por defecto; incluirlos exige motivo y marca cada fila                                                                                                               |
| Marcar hipótesis/no validados                                            | Columna `contact_unverified`; las hipótesis por patrón nunca se exportan                                                                                                       |
| Columnas y nombres; `exported_at`, versiones y freshness                 | Columnas elegidas y encabezados propios; columnas añadidas siempre (`exported_at`, `score_run_id`, edad de hechos, `stale`, `partial`, snapshot y corte)                       |
| CSV formula injection y límites de Excel                                 | Prefijo `'` para `= + - @`, tab y CR; caracteres inválidos en XML fuera; 32 767 caracteres por celda; 1 048 575 filas por hoja                                                 |
| Escritura atómica, cancelación y limpieza                                | `.partial` → final con fsync; parar, fallar o reiniciar borra los parciales                                                                                                    |
| Hash, manifest e historial                                               | SHA-256 del archivo, `.manifest.json` con consulta/snapshot, columnas, conteos y versiones; fila `exports` con actor, destino, configuración y preset                          |
| Sin servicios externos; ruta por diálogo seguro                          | El diálogo lo abre el core; la página nunca envía una ruta; el worker valida la ruta y rechaza la carpeta de datos de la app                                                   |
| 5 presets versionados y clonables                                        | `sales_ready_concise`, `full_evidence_audit`, `contact_review`, `website_opportunity_report`, `dataset_analytical`; cada columna con descripción (D-087)                       |
| UI: jobs → stages → tasks → attempts; acciones por estado                | `PipelinesPanel`: etapas, intentos y jobs del engine enlazados; solo las acciones que permite el estado                                                                        |
| Datasets con calidad, lineage y diff; wizard con preview y progreso      | `SnapshotsPanel`, `ExportWizard`                                                                                                                                               |
| Logs vinculados desde cada error; notificación al terminar               | Enlace «Events» a `/logs?job=`; aviso «N filas escritas en archivo», sin abrirlo                                                                                               |
| Sin outreach                                                             | Nada envía emails, llamadas ni mensajes                                                                                                                                        |

## 4. Archivos

**Nuevos (Python):** `operations.py`, `exports.py`, `migrations/sqlite/0012_operations.sql`,
`migrations/duckdb/0003_snapshot_scores.sql`, `tests/test_pipelines.py`, `tests/test_exports.py`,
`tests/test_dataset_snapshots.py`.

**Modificados (Python):** `datasets.py`, `domain.py`, `contracts.py`, `handlers.py`, `worker.py`,
`export_contracts.py`, `leads/explorer.py` (streaming de ids y conjunto de suprimidos),
`crawl/browser.py` y `crawl/engine.py` (páginas en render), `ai/resources.py` (uso de CPU),
`tests/test_worker_process.py` (pipeline real y exportación extremo a extremo).

**Rust:** `commands.rs` (18 comandos, concesiones de destino), `data.rs` (espejo validado),
`worker.rs` (mensajes y timeouts), `lib.rs` (plugin de diálogo), `Cargo.toml`/`Cargo.lock`
(`tauri-plugin-dialog`), `tests/contracts.rs`, `tests/ipc.rs`.

**UI:** `features/jobs/PipelinesPanel.tsx` y su prueba, `features/datasets/SnapshotsPanel.tsx` y su
prueba, `features/exports/{ExportWizard,ExportsScreen}.tsx` y sus pruebas,
`features/logs/LogsScreen.tsx` y su prueba; modificados `JobsScreen`, `DatasetsScreen`,
`ExportPreviewDialog`, `LeadExplorerScreen`, `leads/labels.ts`, `app/ModuleRoute.tsx`,
`app/navigation.ts`, `lib/ipc.ts`, `lib/links.ts`, `lib/locales/{en,es}.ts`, pruebas de Jobs,
Datasets y contratos.

**Contratos:** `packages/shared-types` (esquema, `contracts.ts`, 28 fixtures nuevas y 1 modificada).

**Docs:** `decisions.md` (D-083…D-087), `operations.md` (runbook), `data-model.md`,
`lead-explorer.md`, `ui-system.md` y este reporte.

## 5. Pruebas obligatorias del prompt

| Caso                                | Prueba                                                                                                                                                        |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Reinicio abrupto y resume           | `test_a_restart_resumes_the_engine_job_a_stage_started_instead_of_starting_another`; `test_an_export_a_restart_interrupted_removes_its_partial_file`          |
| Idempotencia y no duplicación       | `test_a_pipeline_is_planned_once_per_key_with_its_dag_fixed`; TS «keeps the key of a start that failed»                                                       |
| Cancelación HTTP/browser/GPU/export | `test_pause_resume_and_cancel_reach_the_engine_job_of_the_stage` (la cancelación de cada engine ya estaba probada en sus fases); `test_a_stop_a_full_disk…`   |
| Retry classification                | `test_errors_are_classified_as_recoverable_or_needing_a_person`, `test_a_recoverable_failure_retries_and_a_refusal_waits_for_a_person`                        |
| Backpressure y límites de recursos  | `test_one_stage_runs_per_project_and_every_resource_has_its_slots`                                                                                            |
| Snapshot, lineage y diff            | `test_a_snapshot_is_one_moment_with_its_lineage_quality_and_versions`, `test_two_snapshots_differ_by_rows_added_removed…`, `test_retention_keeps_the_latest…` |
| CSV formula injection y Unicode     | `test_spreadsheets_make_formulas_inert_and_every_format_keeps_unicode`                                                                                        |
| XLSX con límites y tipos            | `test_cells_stay_within_what_a_spreadsheet_holds`, `test_parquet_columns_have_explicit_types`                                                                 |
| Export grande sin OOM               | `test_a_large_export_streams_in_bounded_memory`: 50 038 filas JSONL con pico Python < 48 MiB                                                                  |
| Suppressions excluidas              | `test_an_export_writes_its_file_and_manifest_atomically_and_leaves_suppressed_leads_out`, `test_including_do_not_contact_leads_needs_a_reason…`               |
| Disco lleno, permisos y `.partial`  | `test_a_stop_a_full_disk_or_a_refused_folder_leaves_no_partial_file`, `test_a_failed_export_is_recorded_with_its_cause`                                       |
| Manifest/hash reproducible          | `test_a_snapshot_export_is_reproducible_byte_for_byte`                                                                                                        |
| Extremo a extremo                   | `test_a_pipeline_drives_the_real_engines_and_an_export_writes_its_file` (worker real por JSON Lines)                                                          |
| Otros                               | Log filtrable y limpieza, lista con estimaciones y recursos, destinos fuera de los almacenes, presets; Rust: fixtures e IPC; UI: 5 pruebas nuevas             |

## 6. Comandos ejecutados y resultados exactos

Ejecutados en `fase-5-1a1090`, en este orden:

| Comando                                                                                                                                            | Resultado                                                                                                                                                                                                                                                                                                                                 |
| -------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Copia del trabajo sin commit (`git diff --binary` + archivos sin seguimiento)                                                                      | 34 modificados y 40 nuevos aplicados sobre `e3ee45b`                                                                                                                                                                                                                                                                                      |
| `ruff check` · `ruff format --check` · `mypy` · `pnpm contracts:check` · `tsc -b` · `oxlint`                                                       | Todo en verde sobre el trabajo copiado                                                                                                                                                                                                                                                                                                    |
| `pytest tests/test_pipelines.py tests/test_exports.py tests/test_dataset_snapshots.py`                                                             | **24 ✓** en 34 s                                                                                                                                                                                                                                                                                                                          |
| `cargo test --no-run` · `cargo test`                                                                                                               | Compila en 2 min 4 s; 51 ✓ y 1 fallo, `the_crawl_flow_runs_end_to_end_through_ipc`, que pasó al repetirlo solo (1,75 s)                                                                                                                                                                                                                   |
| Pruebas de UI nuevas (`PipelinesPanel`, `ExportWizard`, `ExportsScreen`, `SnapshotsPanel`, `LogsScreen`) y ajuste de `JobsScreen`/`DatasetsScreen` | 26 ✓ en esas carpetas; `DatasetsScreen` fallaba porque el panel nuevo llamaba a IPC sin mock                                                                                                                                                                                                                                              |
| `prettier --write` · `oxlint`                                                                                                                      | 9 archivos formateados; oxlint pidió el tipo de un `vi.fn()` en una prueba (corregido)                                                                                                                                                                                                                                                    |
| `pnpm check` (1.ª)                                                                                                                                 | exit 1 en 319 s: `pytest` **750 ✓, 2 fallos, 6 omitidas**. Defectos reales del trabajo copiado: la transición `pending → failed` de `export_status` estaba en la migración y no en `domain.TRANSITIONS`; la regla de nulabilidad de `test_model.py` no contemplaba columnas JSON de tipo lista (`exports.columns_json`). Corregidos ambos |
| `pytest tests/test_model.py`                                                                                                                       | **240 ✓**                                                                                                                                                                                                                                                                                                                                 |
| `pnpm check` (2.ª)                                                                                                                                 | exit 1 en 11 s: `prettier` sobre este reporte. Formateado                                                                                                                                                                                                                                                                                 |
| `pnpm check` (3.ª)                                                                                                                                 | exit 1 en 302 s: contratos, prettier, oxlint, clippy, ruff, `tsc` y mypy en verde; `pytest` **752 ✓, 6 omitidas** (Ollama no corría y fuentes en vivo opt-in); `vitest` **245 ✓, 1 fallo**: `router.test.tsx` esperaba que `/exports` siguiera «no configurado». Prueba actualizada a los módulos que ahora existen                       |
| `pnpm test:rust`                                                                                                                                   | **60 ✓** (22 unitarias, 25 contratos, 5 IPC, 8 worker) en 28 s; el fallo intermitente de IPC no se repitió                                                                                                                                                                                                                                |
| `pnpm test:ts`                                                                                                                                     | **247 ✓** en 42 archivos                                                                                                                                                                                                                                                                                                                  |
| `pytest …::test_a_stored_path_never_leaves_the_data_directory` (añadida en la fase 14)                                                             | 6 ✓                                                                                                                                                                                                                                                                                                                                       |

Cada etapa de `pnpm check` quedó en verde por separado; la corrida completa y limpia se repite al cerrar
la fase 14, que ya modifica `src-tauri`.

## 7. Riesgos y deuda pendiente

| Asunto                                                                                                                     | Cuándo importa                      |
| -------------------------------------------------------------------------------------------------------------------------- | ----------------------------------- |
| **No verificado en la ventana real de la app** (diálogo nativo incluido): UI con fixtures, flujo IPC contra el worker real | Antes de usarlo en serio            |
| Las etapas de un proyecto corren de una en una aunque podrían solaparse                                                    | Proyectos grandes                   |
| Una excepción inesperada que un reintento habría resuelto espera a una persona (dead letter)                               | Fallos transitorios no clasificados |
| Un export de consulta lee los leads que coinciden al escribir; solo el de snapshot es reproducible byte a byte             | Auditorías                          |
| Un snapshot copia todos los negocios del proyecto                                                                          | Proyectos de millones               |
| Presets compartidos por todo el almacén; sin usuarios                                                                      | Equipos                             |
| El uso de CPU solo se lee en Windows; RAM/VRAM dependen del monitor de la fase 10                                          | Otros sistemas                      |

## 8. Lo que esta fase NO hizo

- Enviar archivos, emails, llamadas u outreach: nada sale de la máquina.
- Empaquetado, instalador, endurecimiento y rendimiento de producción (fase 14).
