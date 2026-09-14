# Fase 12 — Lead Explorer e investigación comercial

Fecha: 2026-09-14 · Estado: **completada**; `pnpm check` y build de release en verde (sección 7).

## 1. Punto de partida

La fase parte de `main` con la fase 11 cerrada (`edbf83f`). Se construyó en el worktree `fase-4-b49e7b`
y se terminó en `fase-7-finalizacion-f4e335`, adonde se trasladó el trabajo sin commit tal cual.

- **Lead Explorer.** Mostraba los leads del último run terminado por posición, con cuatro filtros
  fijos (tier, problema, puntuación mínima, búsqueda) y páginas keyset sin orden elegible.
- **Inspector.** Resumen con procedencia, evidencia latina, contactos y el panel de lead score de la
  fase 11; ninguna vista completa del negocio.
- **Datos.** Existían scores, scorecards y hechos guardados (11), respuestas de IA (10), canales y
  supresiones (09), auditorías (08), evidencia latina (07) y registros fusionados (06). Nada guardaba
  estado de investigación, vistas, checklists, tareas, pins ni el detalle de cómo se trabajó un lead.

## 2. Qué se construyó

La experiencia del analista sobre lo que los engines guardaron. No calcula hechos nuevos ni contacta
a nadie:

```text
run de scoring terminado ─ lead_index (una fila por lead, con rank)
  ─ Lead Explorer: filtros AND/OR, búsqueda, hasta 4 órdenes, columnas, vistas versionadas
    ─ acciones masivas (preview → aplicar con motivo) · export preview · comparación de 2 a 4
  ─ inspector de 11 secciones (panel lateral y /businesses/<id>)
    ─ sales brief: afirmaciones que citan filas guardadas; un modelo local solo las reescribe
    ─ workspace: checklist, tareas, pins, notas, correcciones auditadas, refresh → jobs
    ─ estado de investigación con motivo; sales_ready solo con los 8 criterios
  ─ funnel manual: intentos y resultados, sin tocar ningún score ─ calibración de la fase 11
```

- **Paquete `leads/`** (Python):
  - `index.py`: proyección de los runs en `lead_index`, ranking al terminar, proyección al leer
    runs anteriores;
  - `explorer.py`: compilador de consultas, ventanas, conteo, export preview y comparación;
  - `views.py`: vistas guardadas, versiones, archivo y migración del esquema 1 al 2;
  - `research.py`: workspace, criterios de sales-ready, checklists, tareas, pins, notas,
    correcciones, refresh, acciones masivas y línea temporal;
  - `brief.py`: sales brief determinista y validación de la redacción del modelo;
  - `common.py`: `LeadError` y utilidades compartidas.
- **Migración `0011_lead_research.sql`:** `lead_index` (con `lead_index_scan`, índice cubriente),
  `lead_research`, `saved_views` y versiones, checklists con versiones y `research_checks`,
  `research_tasks`, `evidence_pins`, columnas nuevas de `lead_outcomes`, seis enumeraciones nuevas,
  `sales_brief` en `ai_task_type`, triggers de inmutabilidad y el que borra del índice un negocio
  eliminado.
- **16 mensajes IPC** (`leads.*`, `views.*`, `research.*`) con espejo en Rust que valida límites del
  árbol de filtros, orden de las ventanas, recuentos del preview, criterios de sales-ready y que cada
  afirmación del brief cite filas. `protocol_version` y `data_contract_version` siguen en 1: todo es
  nuevo o aditivo.
- **Tarea de IA `sales_brief`** (pipeline de la fase 10, perfil de calidad): recibe solo las
  afirmaciones del brief; se rechaza la respuesta que cite IDs inexistentes, repita sección, escriba
  números que ninguna afirmación contiene o use lenguaje acusatorio.
- **UI:** `features/leads/` (explorador, grid virtualizado, filtros, vistas, columnas, acciones
  masivas, export preview, comparación, workspace, brief, línea temporal), `BusinessScreen` y el
  inspector de once secciones; enlaces internos en `lib/links.ts`.
- **Semilla de desarrollo:** la checklist por defecto, una vista guardada y otra guardada con el
  esquema 1, y un lead en investigación (needs research, asignado, con tag, nota, pin, tareas, un
  ítem marcado y un intento de llamada); siguen siendo 38 negocios. `clone_lead_index` genera
  índices sintéticos de escala para pruebas y profiling.

Referencia completa en [lead-explorer.md](../lead-explorer.md). Decisiones D-076 a D-082 en
[decisions.md](../decisions.md).

## 3. Requisitos del prompt y cómo se cumplen

| Requisito                                                                                                                                                                                      | Mecanismo                                                                                                                                                                                                                                                                  |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Tabla virtualizada con consultas paginadas del backend                                                                                                                                         | `leads.explore`: ventanas por offset de hasta 500 filas; el grid lee ventanas de 100 al desplazarse y guarda como mucho 12; área de scroll limitada a 8 000 000 px con mapeo proporcional (D-076, D-082)                                                                   |
| Columnas mínimas (empresa, ubicación, vertical, stage, score y componentes, evidencia latina, digital gap, salud/capacidad, contactabilidad, oportunidad, freshness, revisión/asignación/tags) | 36 campos del catálogo: 29 del snapshot del run y 7 vivos (stage, estado de investigación, asignado, tag, no contactar, stale, edad de los hechos); salud y capacidad etiquetadas como estimación                                                                          |
| Sort multi-columna                                                                                                                                                                             | Hasta 4 claves; Shift+clic añade; faltantes al final; el id del negocio desempata                                                                                                                                                                                          |
| Filtros avanzados y grupos AND/OR                                                                                                                                                              | Árbol de condiciones con `and`/`or` y negación; profundidad 3, 40 condiciones, 20 hijos, 50 valores; una sola sentencia parametrizada                                                                                                                                      |
| Saved views versionadas                                                                                                                                                                        | `saved_views` + versiones inmutables con `schema_version`; guardar sin cambios no crea versión; archivar y restaurar con motivo; el esquema 1 se actualiza al leer (D-077)                                                                                                 |
| Columnas visibles, reordenables y fijables                                                                                                                                                     | `ColumnsDialog`: visibilidad, orden y fijado; guardados en la vista y en las preferencias                                                                                                                                                                                  |
| Bulk actions seguras                                                                                                                                                                           | Preview y luego aplicar con motivo; 1 000 por id, 5 000 por consulta y solo si coincide el recuento previsto; una decisión auditada por lead con batch id; `needs_research` omite no contactar; sales-ready no es masivo (D-078)                                           |
| Selección persistente por ID                                                                                                                                                                   | `features/leads/store.ts`: ids durante la sesión; «todos los que coinciden» guarda la consulta                                                                                                                                                                             |
| Export preview antes de exportar                                                                                                                                                               | `leads.export_preview`: recuentos, excluidos por no contactar, muestra real de hasta 50 filas y avisos; no escribe nada                                                                                                                                                    |
| Búsqueda rápida con debounce/cancelación                                                                                                                                                       | Índice full-text de la fase 03 sobre el negocio y sus registros fusionados; debounce en la UI; la respuesta a una consulta superada se descarta                                                                                                                            |
| Navegación por teclado                                                                                                                                                                         | Grid `role="grid"` con `aria-activedescendant`: ↑ ↓, Re Pág/Av Pág, Inicio/Fin, Espacio selecciona, Enter abre                                                                                                                                                             |
| Inspector de 11 secciones en panel lateral y vista completa                                                                                                                                    | `BusinessInspector` en `InspectorPanel` y `/businesses/<id>`; en el panel, una sección plegada no lee nada hasta abrirse                                                                                                                                                   |
| Research Workspace: checklist por vertical/campaña, refresh, tareas por dato faltante, comparar 3–4, pins, aceptar/rechazar IA, estados con motivo, undo                                       | `research.get`/`research.act`: checklist por campaña, categoría o por defecto; refresh de páginas, contactos o evidencia como jobs; tareas por cada desconocido ponderado; `leads.compare` de 2 a 4; pins; revisión de la fase 10; undo como decisión nueva (D-078, D-079) |
| Sin editar datos de fuente; correcciones como overrides auditados                                                                                                                              | Una corrección es una observación del conector `analyst.corrections` elegida como valor mostrado; las filas de origen no cambian                                                                                                                                           |
| Sales brief estructurado, claims enlazados, rechazo de referencias inexistentes, sin acusaciones                                                                                               | `leads.brief`: diez secciones deterministas; cada afirmación cita de 1 a 8 filas; el modelo solo reescribe y se rechaza con `unknown_evidence_id`, `number_not_in_evidence` o `accusatory_language` (D-080)                                                                |
| Funnel manual sin enviar mensajes, conectado a calibración sin reescribir snapshots                                                                                                            | `lead_outcomes` con canal, resultado, calidad, valor con moneda y notas; un intento conserva la etapa; la calibración sigue etiquetando por etapas posteriores a los hechos del run (D-081)                                                                                |
| No cargar HTML/snapshots al abrir la tabla                                                                                                                                                     | La tabla lee `lead_index`; scorecards y snapshots solo en el inspector o la comparación                                                                                                                                                                                    |
| Prefetch limitado del siguiente registro                                                                                                                                                       | `prefetch.ts`: al abrir un lead se lee una vez el negocio siguiente                                                                                                                                                                                                        |
| Cancelar consultas obsoletas                                                                                                                                                                   | Una llamada al worker no se puede cancelar: la respuesta a una consulta superada se ignora                                                                                                                                                                                 |
| Stale/partial/offline claros                                                                                                                                                                   | Etiquetas en texto para no contactar, stale, partial, conflictos y datos de prueba; estados de carga, conteo, error con reintento y offline                                                                                                                                |
| Preservar selección y filtros al volver                                                                                                                                                        | Selección en el store de sesión; filtros, orden, columnas y vista activa en las preferencias locales                                                                                                                                                                       |
| Deep links a business, job, source, score version y dataset                                                                                                                                    | `/businesses/<id>`, `/lead-explorer?run=`, `/jobs?job=`, `/sources?source=`, `/datasets?dataset=`                                                                                                                                                                          |
| Estados vacíos honestos                                                                                                                                                                        | «Ningún run terminado» y «ningún lead coincide con los filtros»; sin métricas simuladas                                                                                                                                                                                    |
| Mapa de flujo del analista y criterios exactos de `sales_ready`                                                                                                                                | Sección 4                                                                                                                                                                                                                                                                  |
| No implementar outreach automático                                                                                                                                                             | Nada envía mensajes: el funnel se registra a mano                                                                                                                                                                                                                          |

## 4. Flujo del analista y criterios de `sales_ready`

```text
run terminado → lead_index
  → Lead Explorer: acotar (filtros, búsqueda, orden, vistas)
      ├─ un lead → inspector (11 secciones) → brief → redacción del modelo (aceptar/rechazar), pins
      ├─ varios → acción masiva (preview → aplicar con motivo)
      ├─ 2 a 4 → comparación
      └─ la consulta → export preview
  → workspace: checklist · tareas · refresh (jobs) · correcciones auditadas · notas
  → estado con motivo:
      needs_research → vuelve al workspace
      disqualified   → fuera de la lista; se puede deshacer
      sales_ready    → solo con los 8 criterios → funnel manual → calibración sin fuga
```

`research.act` rechaza `sales_ready` salvo que se cumplan **todos** en ese momento (un undo que vuelve
a sales-ready los comprueba de nuevo):

| #   | Criterio                     | Se cumple cuando                                                                                                                        |
| --- | ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `not_suppressed`             | El negocio no tiene una entrada viva a nivel de negocio en la lista de no contactar                                                     |
| 2   | `active_business`            | Su estado no es `closed` ni `merged`                                                                                                    |
| 3   | `scored_recently`            | El run terminado más reciente lo puntuó con hechos leídos hace 90 días o menos                                                          |
| 4   | `qualifying_tier`            | Ese score tiene tier `priority` o `review`                                                                                              |
| 5   | `verified_opportunity`       | Ese scorecard recomienda una oferta: un problema observado que la oferta resuelve                                                       |
| 6   | `usable_contact`             | Al menos un canal publicado cuenta como alcanzable: estado válido para contactabilidad, no `stale`, no suprimido, no un número ficticio |
| 7   | `contact_conflicts_resolved` | No queda un conflicto `values_disagree` de teléfono o email, salvo que una persona eligiera el valor a mostrar                          |
| 8   | `checklist_complete`         | Todos los ítems obligatorios de la checklist asignada al lead están hechos                                                              |

## 5. UI conectada

| Pantalla                                 | Qué hace con datos reales                                                                                                                                                                             |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Lead Explorer**                        | Cualquier run: grid virtualizado, filtros AND/OR, búsqueda, multi-orden, columnas, vistas guardadas, selección por id, acciones masivas, export preview, comparación, run por enlace                  |
| **Business** (`/businesses/<id>`, nueva) | Las once secciones completas con una lista para saltar entre ellas                                                                                                                                    |
| **Inspector de negocio**                 | Las mismas once secciones plegables: resumen y fuentes, scorecard, why this lead, evidencia latina, website, contactos y supresiones, línea temporal, evidencia, conflictos, oferta, notas y revisión |
| **Scorecard (inspector)**                | Intentos y resultados con canal, resultado, calidad del contacto, valor y notas                                                                                                                       |
| **Jobs · Sources · Datasets**            | Marcan el job, la fuente o el dataset que nombra el enlace, o avisan si no está listado                                                                                                               |
| **AI Models**                            | La tarea `sales_brief` aparece entre las tareas y queda fuera del benchmark (se redacta lead por lead)                                                                                                |

## 6. Archivos

**Nuevos (Python):** `leads/` (`__init__.py`, `common.py`, `index.py`, `explorer.py`, `views.py`,
`research.py`, `brief.py`), `migrations/sqlite/0011_lead_research.sql`,
`ai/prompts/sales_brief.txt`, `scripts/profile_lead_explorer.py`, `tests/test_leads.py`.

**Modificados (Python):**

- `domain.py`: campos y operadores del explorador, consultas, vistas, workspace, checklists, tareas,
  pins, brief, funnel y contratos IPC;
- `contracts.py`: 16 mensajes;
- `handlers.py` y `worker.py`: rutas y `LeadError` → `invalid_payload`;
- `scoring/store.py`: proyección al índice, intentos y campos nuevos de resultados;
- `ai/engine.py`, `ai/inputs.py`, `ai/tasks.py`: tarea `sales_brief` y palabras acusatorias;
- `storage.py`: pragmas de lectura (mmap, caché, ordenaciones en memoria);
- `testdata.py`: `seed_leads` y `clone_lead_index`;
- `export_contracts.py`: 28 fixtures nuevas;
- pruebas: `test_ai_tasks.py`, `test_scoring.py`, `test_worker_process.py`.

**Rust:** `data.rs` (espejo con validación), `commands.rs` (16 comandos), `worker.rs`, `lib.rs`,
`tests/contracts.rs`, `tests/ipc.rs`.

**UI:**

- `features/leads/`: `LeadExplorerScreen`, `LeadGrid`, `useLeadWindows`, `FilterBuilder`,
  `ViewsDialog`, `ColumnsDialog`, `BulkDialog`, `ExportPreviewDialog`, `CompareDialog`,
  `ResearchPanel`, `BriefPanel`, `TimelinePanel`, `EvidenceChip`, `query.ts`, `store.ts`,
  `research.ts`, `labels.ts` y 5 pruebas;
- `features/businesses/`: `BusinessScreen` y su prueba, `prefetch.ts`, `BusinessInspector.tsx`,
  `InspectorPanel.tsx`, `columns.tsx`; se eliminó el `LeadExplorerScreen` de la fase 11;
- `features/scoring/` (`ScorecardPanel`, `labels.ts`), `features/jobs`, `features/sources`,
  `features/datasets`, `features/ai`;
- `app/router.tsx`, `app/navigation.ts`, `app/ModuleRoute.tsx`, `lib/links.ts` y su prueba,
  `lib/ipc.ts`, `lib/locales/{en,es}.ts`, `stores/preferences.ts`.

**Contratos:** `packages/shared-types` (esquema, `contracts.ts`, 28 fixtures nuevas y 4 modificadas).

**Docs:** `lead-explorer.md` (nuevo), `decisions.md` (D-076…D-082), `architecture.md`,
`data-model.md`, `operations.md`, `scoring.md`, `ui-system.md`, `README.md` y este reporte.

## 7. Comandos ejecutados y resultados exactos

Ejecutados en `fase-7-finalizacion-f4e335` al terminar la fase, en este orden:

| Comando                                                                                      | Resultado                                                                                                                                                                                                                                                                                                                 |
| -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pnpm install --frozen-lockfile` · `uv --no-cache sync --locked`                             | Dependencias del worktree al día (entran `dnspython` y `phonenumbers` de la fase 09)                                                                                                                                                                                                                                      |
| `pnpm contracts:check` · `ruff check` · `ruff format --check` · `mypy`                       | exit 0 · sin avisos · 109 archivos formateados · sin errores en 107 archivos                                                                                                                                                                                                                                              |
| `pytest tests/test_leads.py`                                                                 | **16 ✓** en 20,6 s                                                                                                                                                                                                                                                                                                        |
| `python scripts/profile_lead_explorer.py 1000000`                                            | exit 0; resultados en la sección 9                                                                                                                                                                                                                                                                                        |
| `prettier --check .`                                                                         | Primer intento: `data-model.md`, `ui-system.md` y `lead-explorer.md` sin formato (tablas nuevas). `prettier --write` y luego en verde                                                                                                                                                                                     |
| `pnpm check` (1.ª)                                                                           | exit 101 en 42 s: `clippy::useless_conversion` en `tests/ipc.rs` (`u64::from` sobre un `u64`). Corregido                                                                                                                                                                                                                  |
| `pnpm check` (2.ª)                                                                           | exit 1 en 10 s: `prettier` sobre este reporte tras una edición. Formateado                                                                                                                                                                                                                                                |
| `pnpm check` (3.ª)                                                                           | exit 1 en 251 s: todo en verde hasta `pytest`, **728 ✓, 1 fallo, 1 omitida**. El fallo, `test_a_real_model_answers_within_the_contract[injection_roofing]` (fase 10), se debe al entorno: el puerto 11434 lo atendía un Ollama 0.9.6 dentro de WSL con solo `llama2`, que obedece la instrucción inyectada (3 de 3 veces) |
| `pytest tests/test_ai_live.py` contra el Ollama 0.32.9 de Windows (11435)                    | **4 ✓** en 12,7 s con `qwen3:4b`; el puerto se redirigió con un plugin de pytest fuera del repositorio, sin tocar código                                                                                                                                                                                                  |
| `pnpm test:ts` · `pnpm test:rust`                                                            | **227 ✓** en 37 archivos (45 s) · exit 101: artefactos corruptos en `target/debug` (`found staticlib tokio instead of rlib`, errores internos del compilador)                                                                                                                                                             |
| `cargo clean --profile dev` · `pnpm test:rust`                                               | 6,0 GiB borrados · **57 ✓** (22 unitarias + 23 contratos + 4 IPC + 8 worker) en 149 s                                                                                                                                                                                                                                     |
| `pnpm build:app`                                                                             | **exit 0** en 135 s: Vite build (1,2 s; aviso de chunk de 1 235 kB) y `cargo build --release` (1 min 53 s) → `lead-intelligence.exe` (20 MB)                                                                                                                                                                              |
| `cargo clippy --all-targets -D warnings` (perfil dev limpio)                                 | exit 0 en 67 s                                                                                                                                                                                                                                                                                                            |
| `pnpm check` (4.ª y 5.ª, pytest contra el Ollama de Windows)                                 | 4.ª: 1 fallo por el plugin, que también cambiaba la URL de `test_settings.py` (acotado a `test_ai_live.py`). 5.ª: pytest **729 ✓, 1 omitida** y vitest **227 ✓**, pero `cargo test` otra vez con artefactos corruptos (E0460 «found possibly newer version of crate»)                                                     |
| Diagnóstico                                                                                  | **Disco C: con 0,23 GB libres**: los builds de Rust escribían artefactos truncados. `cargo clean` del `target` de este worktree (7,9 GiB)                                                                                                                                                                                 |
| `pnpm check` (6.ª: `CARGO_INCREMENTAL=0`, sin debuginfo, pytest contra el Ollama de Windows) | **exit 0 en 434 s**: contratos, prettier, oxlint, clippy, ruff, `tsc` y mypy en verde; `pytest` **729 ✓, 1 omitida** (fuentes en vivo opt-in), `vitest` **227 ✓** en 37 archivos, `cargo test` **57 ✓**; mínimo de 4,62 GB libres durante la corrida                                                                      |

## 8. Pruebas obligatorias del prompt

| Caso                                           | Prueba                                                                                                                                                                                                                                                                   |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Tabla con 100k–1M registros sin cargarlos      | `test_a_hundred_thousand_leads_are_paged_from_the_index_without_loading_them` (pico < 16 MiB, < 2 s), `test_windows_are_found_in_indexes_and_never_sort_the_rows_of_a_run`; TS «reads a million leads window by window…», `window.test.ts`; profiling de 1 M (sección 9) |
| Filtros AND/OR                                 | `test_filters_sorts_and_windows_match_a_reference_evaluation` (contra una evaluación de referencia, con grupos negados y valores faltantes); TS «combines all-of and any-of groups…», `query.test.ts`                                                                    |
| Saved views y migración de esquema             | `test_saved_views_are_versioned_archived_with_a_reason_and_upgraded_when_read`; TS «applies a saved view, says it was upgraded…» y «falls back to the default view when the stored layout has an older schema»                                                           |
| Selección al paginar y ordenar                 | TS «keeps a selection by id across windows and a new sort», «selects every matching lead by what matches…»; Python: la selección de una ventana sobrevive a otro orden                                                                                                   |
| Datos stale, partial y conflicto               | TS «marks do-not-contact, stale, partial, conflicting and test leads in words»; `scored_recently` falla con hechos de más de 90 días en la prueba de sales-ready                                                                                                         |
| Supresión visible y respetada                  | `test_the_do_not_contact_list_is_shown_filtered_and_respected_everywhere`                                                                                                                                                                                                |
| Brief con evidence IDs válidos y alucinaciones | `test_every_claim_of_a_brief_resolves_to_a_stored_row_and_says_nothing_offensive`, `test_a_model_may_only_reword_the_claims_of_a_brief`; Rust `lead_explorer_payloads_that_claim_too_much_are_rejected`; TS `BriefPanel.test.tsx`                                        |
| Stage transitions y audit trail                | `test_a_lead_is_sales_ready_only_when_every_criterion_holds_and_the_change_can_be_undone`, `test_attempts_and_outcomes_are_recorded_in_order_and_never_touch_a_score`, `test_bulk_actions_are_previewed_then_applied_lead_by_lead…`; TS `ResearchPanel.test.tsx`         |
| Keyboard navigation y accesibilidad            | TS «reads a million leads window by window and moves through them from the keyboard», `BusinessScreen.test.tsx`                                                                                                                                                          |
| Recuperación tras reiniciar                    | `test_research_work_survives_a_worker_crash` (worker real matado y reabierto); TS «…brings the layout back after a restart»                                                                                                                                              |
| Otros                                          | Índice y proyección al leer, correcciones auditadas, pins/tareas/notas/checks y línea temporal, checklists por categoría, refresh con jobs, rutas IPC; Rust `lead_explorer_fixtures_round_trip_and_pass_validation`; UI router, Jobs, Sources, Datasets                  |

## 9. Rendimiento

`uv run python scripts/profile_lead_explorer.py 1000000`: la semilla sintética más un millón de filas
clonadas en su run más reciente (1 000 038 leads), una llamada por caso, pico de memoria Python con
`tracemalloc`. Detalle en [lead-explorer.md](../lead-explorer.md#11-performance).

| Caso                                            | Tiempo     | Pico Python |
| ----------------------------------------------- | ---------- | ----------- |
| Primera ventana por rank con total              | 178 ms     | 0,70 MiB    |
| Ventana en el offset 999 900                    | 117 ms     | 0,70 MiB    |
| Filtros AND/OR + 2 órdenes, con total (333 333) | 575 ms     | 0,70 MiB    |
| Lo mismo en el offset 100 000                   | 405 ms     | 0,70 MiB    |
| Orden sin índice en el offset 500 000           | 1 702 ms   | 0,70 MiB    |
| Nombre, tag o lista de no contactar, con total  | 921–932 ms | ≤ 0,70 MiB  |
| Export preview (recuentos + muestra de 50)      | 655 ms     | 0,36 MiB    |
| Acción masiva sobre todo el run (rechazada)     | 152 ms     | 0,00 MiB    |

- Antes de D-082 (medido por la sesión que construyó la fase) cada ventana tardaba de 6,6 a 11 s y el
  export preview 11,8 s, porque se ordenaban las filas del run.
- Tamaño: 932 bytes por lead por run (tabla 316, índice cubriente 279, otros cuatro índices 337);
  1 086 MiB de almacén con un millón de filas.
- Escribir el índice: 3,1 s para 100 000 filas, 5 min para un millón (los insert en cinco índices
  con ids aleatorios salen de la caché).

## 10. Riesgos y deuda pendiente

| Asunto                                                                                                                                                                                                                                    | Cuándo importa                                |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- |
| **No verificado en la ventana real de la app.** La UI está probada con fixtures del servicio real y el flujo IPC contra el worker real, pero no se pulsó en Tauri.                                                                        | Antes de usarla en serio                      |
| El índice es el snapshot del run: nombre, lugar, scores y canal quedan como se puntuaron.                                                                                                                                                 | Hechos nuevos: puntuar de nuevo               |
| El índice de cada run ocupa unos 930 bytes por lead (1 086 MiB de almacén con un millón) y escribir un millón de filas tardó 5 min en el profiling.                                                                                       | Muchos runs grandes                           |
| Una ventana profunda con un orden que ningún índice recorre ordena todas las entradas del run; un filtro poco selectivo siempre lee el índice cubriente.                                                                                  | Runs de millones con órdenes poco comunes     |
| Refresh de contactos o evidencia recorre todo el proyecto (esos engines trabajan por proyecto).                                                                                                                                           | Proyectos grandes                             |
| Brief, hooks y redacción del modelo solo en inglés.                                                                                                                                                                                       | Outreach en español                           |
| La selección dura la sesión; el layout, solo en esta máquina. Sin bloqueo multiusuario: un analista por almacén.                                                                                                                          | Equipos                                       |
| La lista de palabras acusatorias es fija y en inglés.                                                                                                                                                                                     | Redacción en otros idiomas                    |
| Export preview no escribe: las exportaciones llegan en la fase 13; ningún negocio registra aún un snapshot de dataset, así que el enlace `?dataset=` no se usa todavía.                                                                   | Fase 13                                       |
| **Entorno:** el disco C: tenía 0,23 GB libres (hay unos 33 GB de `target` de Rust en otros worktrees) y el puerto 11434 lo atiende un Ollama de WSL con solo `llama2`, así que la app hablaría con ese runtime y no con los modelos qwen. | Antes de usar la IA local o compilar de nuevo |
| El bundle de Vite pesa 1 235 kB en un solo chunk.                                                                                                                                                                                         | Fase 14 (rendimiento)                         |

## 11. Lo que esta fase NO hizo

Queda fuera:

- el outreach automático: nada envía mensajes;
- recalcular hechos o scores: el explorador solo lee lo que guardaron los engines;
- las exportaciones reales (fase 13);
- el empaquetado y endurecimiento de producción (fase 14).
