# Fase 07 — Latino evidence engine

Fecha: 2026-09-12 · Estado: **completada y verificada** (`pnpm check` en verde y build de release).

## 1. Punto de partida

La fase parte del commit de la fase 06 (`363cd99`). El worktree `fase-6-6e13b4` tenía, sin commit,
un primer avance del motor en Python: reglas, almacén, job, migración `0006`, contratos y filtros
de la lista. Se trajo a un worktree nuevo, se revisó línea por línea y se terminó: pruebas, contratos
exportados, espejo Rust y comandos, interfaz, documentación y verificación completa.

Ese avance no arrancaba: `repository.py` usaba `LatinoReviewFilter` sin importarlo (el worker fallaba
al importar), `plan_runs` llamaba a un `repository.AuditAction` inexistente y toda revisión con una
evaluación previa fallaba (`dataclasses.replace` sobre un modelo Pydantic). Se corrigieron junto con
los hallazgos de la sección 8.

## 2. Qué se construyó

Un sistema **ético y auditable** que separa la propiedad latina explícitamente evidenciada de la
afinidad con el mercado hispanohablante, y que dice qué la sostiene y qué no puede concluir:

```text
raw records + páginas rastreadas ─ extracción versionada ─▶ evidence_items (claim, track, fragmento, hash)
          ─ ciclo de vida (stale · revoked · contradicted) ─▶ reglas versionadas por negocio canónico
          ─ assessments (se reemplazan, nunca se editan) ◀── revisión humana con motivo (coexiste)
```

- **Paquete `latino/`** (Python): `rules.py` (taxonomía, patrones y evaluación puras y versionadas),
  `store.py` (fuentes, extracción, ciclo de vida, evaluación, lecturas, revisión y candidatos de un
  modelo) y `engine.py` (job reanudable).
- **Migración `0006_latino_evidence.sql`**: una evaluación vigente por negocio **y método** (la
  revisión convive con el cálculo), `details_json` con advertencias y límites, y por cada evidencia
  citada su fiabilidad, frescura, puntuación, si contó y si es organizacional; las citas son
  inmutables.
- **5 mensajes IPC nuevos** y 4 filtros nuevos en `businesses.list` (aditivos: `protocol_version` y
  `data_contract_version` siguen en 1), espejo Rust con validación, panel en el inspector de negocio,
  filtros y evaluación en Businesses, y runs en Jobs.

Referencia completa en [latino-evidence.md](../latino-evidence.md). Decisiones D-045 a D-051 en
[decisions.md](../decisions.md).

## 3. Requisitos del prompt y cómo se cumplen

| Requisito                                              | Mecanismo                                                                                                                                                                                                              |
| ------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Cinco estados exactos, etiquetas humanas, códigos      | `LatinoStatus` con los cinco códigos; la UI muestra la etiqueta y el código al lado, sin banderas ni colores de certeza                                                                                                |
| Prohibiciones absolutas                                | El escáner de payloads solo abre columnas de propiedad, certificación, directorio e idioma; nombres, personas, fotos, direcciones, nacionalidad y columnas de etnicidad nunca se leen (D-045)                          |
| `spanish_market_affinity` no es propiedad              | Tres tracks: solo el de propiedad produce estados de propiedad; ninguna combinación de señales de mercado de ninguna fuente pasa de afinidad (probado)                                                                 |
| Taxonomía con fuente y fuerza configurables            | 15 claims con track, tipo, fuerza y edad máxima en un `Ruleset` versionado, con fiabilidad por tipo de fuente                                                                                                          |
| Campos de cada evidencia                               | `evidence_items` + extracción + raw record: claim, valor observado, tipo y URL de fuente, campo y fragmento, captura, versión del extractor, hash del fragmento y del payload, fuerza, estado; frescura por evaluación |
| Fragmentos mínimos                                     | Solo la oración que muestra el claim (≤ 300 caracteres); nunca páginas completas                                                                                                                                       |
| Reglas transparentes y versionadas                     | `RULES_VERSION 1.0.0`; fuerza × fiabilidad × frescura; niveles con reglas nombradas (D-046)                                                                                                                            |
| Factores separados                                     | `evidence_strength`, `source_reliability`, `freshness` y `assessment_confidence` guardados por separado en cada evaluación                                                                                             |
| Explícita fuerte → verified; varias → high o probable  | `verified`: fuerte, fresca, de la ubicación y de fuente oficial o registro; `high`: fuente confiable fuerte o dos fuentes independientes; `probable`: el resto                                                         |
| Solo mercado → afinidad; sin evidencia → unknown       | Sí, con `affinity_is_not_ownership` y `absence_is_not_evidence` en lo que no puede concluir                                                                                                                            |
| Contradicciones visibles que reducen                   | Negación más reciente y al menos igual de fiable marca `contradicted`; una más débil limita a probable y baja la confianza; la advertencia `contradicted` se muestra siempre                                           |
| Revisión humana con decisión, motivo, fecha, historial | Evaluación `manual_review` + `review_decisions` con motivo obligatorio; acotada por la evidencia; retirable; el cálculo automático se conserva y sigue recalculando (D-048)                                            |
| Recalcular conservando versiones                       | Nueva evaluación solo si algo cambió; la anterior queda reemplazada en el historial; unas reglas nuevas recalculan todo y marcan la revisión como desactualizada (D-047)                                               |
| IA solo como candidato enlazado al fragmento           | `ingest_model_candidates`: la cita debe estar en la página y los patrones deben leerla igual; el modelo no decide ni navega (D-049)                                                                                    |
| UI en Business/Research                                | Sección 7                                                                                                                                                                                                              |
| Filtros en Businesses/Lead Explorer                    | Businesses filtra por estado, confianza mínima, tipo de fuente que contó y revisión; Lead Explorer sigue sin leads hasta las fases 11-12                                                                               |
| No implementar opportunity scoring                     | Nada puntúa un lead; la confianza solo describe la evidencia                                                                                                                                                           |

## 4. Evidencia y reglas

- **Fuentes**: los raw records detrás de los negocios del proyecto (columnas estructuradas) y las
  páginas rastreadas de sus sitios (título, meta descripciones, encabezados, texto principal, JSON-LD,
  `lang`, enlaces a versión en español). Una página habla por el negocio cuyo candidato pidió el
  rastreo y por todo negocio cuyo sitio está en ese dominio.
- **Declaración o mención**: una frase de propiedad en primera persona o en texto prominente es
  `ownership_statement` (fuerte); en texto corrido sin primera persona, `ownership_mention`
  (moderada). Una frase sobre **otros** negocios ("apoyamos negocios de propiedad latina", "find
  Hispanic-owned restaurants") no es un claim.
- **Fiabilidad**: API oficial y registro público 0.95, API de plataforma 0.85, directorio y sitio del
  negocio 0.80, dataset público 0.75, importación y sintético 0.60, semilla manual 0.50, búsqueda web
  0.40.
- **Independencia**: un conector para registros, un dominio registrable para páginas.
- **Autodeclaración**: explícita pero no confirmada por terceros; llega a `high_confidence` con
  `self_declared`, nunca a `verified`.
- **Organizacional**: si toda la evidencia de propiedad la comparten varios negocios canónicos (una
  marca), el estado se limita a probable con `location_ownership_unknown` (D-050).

## 5. Ciclo de vida, recálculo y revisión

| Evento                                                         | Efecto                                                                                                                       |
| -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Evidencia más vieja que su edad máxima o certificación vencida | `stale`: no cuenta, advertencia `stale_evidence`; vuelve a `active` si las reglas cambian                                    |
| Un snapshot más reciente de la misma página ya no lo dice      | `revoked` (final): no cuenta, advertencia `revoked_evidence`                                                                 |
| Negación más reciente y al menos igual de fiable               | `contradicted`: no cuenta, advertencia `contradicted`; vuelve a `active` si la negación deja de valer                        |
| "Under new ownership" más reciente                             | la evidencia anterior deja de contar (`ownership_changed`)                                                                   |
| Cambia la evidencia o las reglas                               | nueva evaluación, la anterior queda en el historial; sin cambios, nada se escribe                                            |
| Una persona revisa                                             | evaluación `manual_review` que el negocio muestra; el cálculo sigue debajo; si recalcula, la revisión queda "desactualizada" |
| Una persona retira su revisión                                 | se vuelve a mostrar el cálculo; queda la decisión con su motivo                                                              |

Cada cambio de estado de una evidencia queda en `audit_log` con su motivo.

## 6. IA local

El runtime llega en la fase 10. El almacén ya acepta candidatos de un modelo sobre una página
rastreada (`ownership_statement`, `ownership_mention`, `certification_statement`,
`ownership_denied`) con la **cita literal**: se guardan como evidencia `latino.model` enlazada a su
inferencia solo si la cita está en la página y los patrones la leen como el mismo claim; si no, se
rechazan con `claim_not_supported`, `quote_too_short`, `quote_not_found` o `not_a_declaration`.

## 7. UI conectada

| Pantalla                 | Qué hace con datos reales                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Businesses**           | Columna de evidencia latina (etiqueta, confianza e insignia "Revisado"); filtros en el almacén por estado, confianza mínima, tipo de fuente que contó y revisión (revisado, sin revisar, desactualizado); última evaluación y botón para ejecutarla; la lista se relee al terminar.                                                                                                                                                                                                     |
| **Inspector de negocio** | Propiedad y mercado hispanohablante en líneas separadas; estado con su código, confianza y si decidió una persona o las reglas (con el cálculo debajo de una revisión); advertencias; lo que no puede concluir; evidencia agrupada por track y ordenada por fuerza y frescura, cada una con estado, si contó, fuerza, frescura, fiabilidad, fuente enlazada, campo, extractor, fecha y fragmento; historial de evaluaciones; decisiones; formulario de revisión con motivo obligatorio. |
| **Jobs**                 | Evaluaciones junto a descubrimientos, rastreos y resoluciones, con pausa, reanudación y cancelación a través del motor dueño del job.                                                                                                                                                                                                                                                                                                                                                   |

La evidencia latina ya no se repite en la lista genérica de evidencias del inspector.

## 8. Correcciones al avance previo

| Hallazgo                                                                                                                                                                                                                          | Corrección                                                                                   |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `LatinoReviewFilter` sin importar en `repository.py`: el worker no arrancaba                                                                                                                                                      | Importado; la subconsulta de revisión reescrita sin paréntesis construidos por concatenación |
| `repository.AuditAction` no existe: ninguna evaluación podía planificarse                                                                                                                                                         | `AuditAction` del dominio                                                                    |
| `dataclasses.replace` sobre un modelo Pydantic: fallaba toda revisión de un negocio evaluado                                                                                                                                      | `model_copy`                                                                                 |
| Una negación que dejaba sin evidencia de propiedad, o una negación sola, no producía la advertencia `contradicted`                                                                                                                | La advertencia se muestra siempre que algo habla en contra de la propiedad                   |
| Filtro por clave de conector (exige catálogo y cambia entre proyectos)                                                                                                                                                            | Filtro por **tipo** de fuente que contó (`latino_source_kind`)                               |
| Checkpoints, progreso y cierre de job duplicados entre `jobs.py` y `resolution/store.py`                                                                                                                                          | Una sola copia en `jobs.py`; la resolución la reutiliza (D-051)                              |
| Guiones y apóstrofos Unicode literales en las expresiones regulares                                                                                                                                                               | Escapes `\N{…}` con nombre                                                                   |
| Tipos del cursor y de los lotes del job, líneas largas y comentarios `fmt: skip` inválidos                                                                                                                                        | Corregidos (ruff y mypy `--strict` sin avisos)                                               |
| Llamadas existentes a `BusinessListQuery` sin los campos nuevos                                                                                                                                                                   | Actualizadas (pruebas, fixtures y payloads del worker)                                       |
| El índice `evidence_items_by_status` de la migración, sin uso en ninguna consulta, llevaba al planificador (sin `ANALYZE`) a recorrer toda la evidencia activa por cada fila de la lista: 1 007 ms por página con 20 000 negocios | Eliminado: la misma página tarda 7.9 ms; la prueba de rendimiento del repositorio lo detectó |

## 9. Archivos

**Nuevos (Python):** `latino/` (`rules.py`, `store.py`, `engine.py`),
`migrations/sqlite/0006_latino_evidence.sql`, `tests/test_latino_rules.py`, `tests/test_latino.py`.

**Modificados (Python):** `domain.py` (columnas de `LatinoAssessment` y `LatinoAssessmentEvidence`,
tracks, frescura, filtros y contratos de runs, detalle y revisión; filas y consulta de la lista),
`repository.py` (estado, confianza y revisión en la lista; filtros), `jobs.py` (`BackgroundJobs`,
checkpoints, progreso y cierre compartidos), `resolution/engine.py` y `resolution/store.py` (usan
`jobs`), `contracts.py`, `handlers.py`, `worker.py` (servicio, recuperación y apagado),
`testdata.py` (`synthetic_source`, `insert_site_pages`, `seed_latino`, semilla de desarrollo),
`export_contracts.py` (7 fixtures), `tests/test_resolution.py`, `tests/test_datasets.py`,
`tests/test_worker_process.py`.

**Rust:** `data.rs` (espejo con validación: estados, tracks, evaluaciones, evidencia, detalle,
revisión y filtros de la lista), `commands.rs` (5 comandos), `worker.rs`, `lib.rs`,
`tests/contracts.rs`, `tests/ipc.rs`.

**UI:** `features/latino/` (`LatinoPanel.tsx`, `LatinoFilters.tsx`, `filters.ts`, `labels.ts` y
pruebas), `features/businesses/` (`BusinessesScreen.tsx`, `BusinessInspector.tsx`, `columns.tsx`,
`TableWorkspace.tsx` y pruebas), `features/jobs/`, `features/resolution/` (`labels.ts` y prueba),
`lib/ipc.ts`, `lib/locales/{en,es}.ts`, `lib/contracts.test.ts`, `app/router.test.tsx`.

**Contratos:** `packages/shared-types` (esquema, `contracts.ts`, 7 fixtures nuevas y 2 actualizadas).

**Docs:** `latino-evidence.md` (nuevo), `decisions.md` (D-045…D-051), `architecture.md`,
`data-model.md`, `operations.md`, `README.md`.

## 10. Comandos ejecutados y resultados exactos

| Comando                                                          | Resultado                                                                                                                          |
| ---------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `pnpm install --frozen-lockfile` · `uv --no-cache sync --locked` | exit 0                                                                                                                             |
| `pnpm contracts`                                                 | exit 0: esquema, `contracts.ts`, 7 fixtures nuevas y 2 actualizadas                                                                |
| `cargo fmt` · `cargo clippy --all-targets -- -D warnings`        | exit 0                                                                                                                             |
| `ruff format` · `ruff check` · `mypy`                            | sin avisos (64 archivos)                                                                                                           |
| Primer `pnpm check`                                              | exit 1: 8 pruebas de `test_repository.py` construían la consulta de la lista sin los campos latinos (corregido)                    |
| Segundo `pnpm check`                                             | exit 1: `prettier --check` sobre este informe sin formatear (corregido)                                                            |
| Tercer `pnpm check`                                              | exit 1: `test_performance_on_a_synthetic_dataset`, páginas de ~1 s por el índice `evidence_items_by_status` (eliminado, sección 8) |
| `pnpm check` (final)                                             | **exit 0** en 402 s                                                                                                                |
| `pnpm test:py`                                                   | **537 ✓, 2 omitidas** en 244.5 s (prueba en vivo opt-in de fuentes; Ollama detenido)                                               |
| `pnpm test:ts`                                                   | **157 ✓** en 24 archivos                                                                                                           |
| `pnpm test:rust`                                                 | **46 ✓** (21 unitarias + 13 contratos + 4 IPC + 8 worker)                                                                          |
| `pnpm lint` / `pnpm typecheck`                                   | ruff, oxlint, prettier, clippy, `tsc` y mypy `--strict` sin avisos                                                                 |
| `pnpm build:app`                                                 | **exit 0** en 383 s: Vite build y `cargo build --release` (5 m 56 s) → `lead-intelligence.exe`                                     |

## 11. Pruebas obligatorias del prompt

| Caso                                            | Prueba                                                                                                                                                                                                                                                                                                                                                       |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Autodeclaración explícita                       | `test_an_explicit_self_declaration_is_ownership_evidence` (inglés, español, encabezado, guion no separable, "owned and operated by"), `test_a_self_declaration_alone_is_high_confidence_but_never_verified`; en el almacén, `declared` → `high_confidence` con fragmento y URL                                                                               |
| Directorio/certificación confiable              | `test_certifications_platform_attributes_and_directories_are_read_from_their_columns`, `test_a_trusted_certification_verifies_and_a_directory_is_probable`; en el almacén, `certified` → `verified` y `listed` → `probable`                                                                                                                                  |
| Web en español sin declaración de propiedad     | `test_a_spanish_website_without_a_declaration_is_market_affinity`, `test_mostly_spanish_text_is_a_spanish_page_even_without_a_lang_attribute`, `test_no_mix_of_market_signals_from_any_source_ever_reaches_ownership`; en el almacén, `spanish` → afinidad                                                                                                   |
| Nombre/apellido hispano sin evidencia           | `test_names_surnames_photos_and_places_never_raise_the_status_above_unknown` (8 apellidos × 3 nombres, foto, vecindario, nacionalidad y etnicidad); en el almacén, ninguna evidencia sale de nombre, dirección, ciudad, teléfono o sitio                                                                                                                     |
| Señales contradictorias                         | `test_a_newer_reliable_denial_contradicts_a_declaration_and_is_shown`, `test_a_weaker_denial_lowers_the_status_instead_of_being_ignored`, `test_new_ownership_makes_older_declarations_stop_counting`; en el almacén, `disputed` → `unknown` con la evidencia `contradicted`                                                                                 |
| Evidencia expirada o removida                   | `test_expired_old_or_removed_evidence_stops_counting_and_says_so`, `test_an_expired_certification_is_stale_and_stops_counting`, `test_a_claim_the_website_no_longer_makes_is_revoked_and_history_is_kept`                                                                                                                                                    |
| Múltiples ubicaciones, evidencia organizacional | `test_evidence_an_organization_shares_across_locations_caps_ownership`; en el almacén, las dos sedes de `brand` → `probable` con `organizational_evidence`                                                                                                                                                                                                   |
| Extracción IA falsa o no soportada              | `test_a_model_may_point_at_a_declaration_but_never_decides_one`, `test_a_model_candidate_becomes_evidence_only_when_the_page_confirms_it`                                                                                                                                                                                                                    |
| Revisión humana y recálculo versionado          | `test_a_person_reviews_with_a_reason_and_the_rules_keep_their_own_history`, `test_a_second_run_changes_nothing`, `test_assessments_and_what_they_cite_are_history`; en Rust, `data_commands_serve_real_store_queries_through_ipc` filtra, rechaza un motivo en blanco y un estado sin evidencia, revisa y reevalúa por IPC; en la UI, `LatinoPanel.test.tsx` |
| Apellido/nombre/foto jamás sobre `unknown`      | `test_names_surnames_photos_and_places_never_raise_the_status_above_unknown`                                                                                                                                                                                                                                                                                 |
| Español como máximo `spanish_market_affinity`   | `test_no_mix_of_market_signals_from_any_source_ever_reaches_ownership` (127 combinaciones × 10 tipos de fuente)                                                                                                                                                                                                                                              |

## 12. Rendimiento

Medido en esta máquina con 5 000 candidatos sintéticos de tres fuentes: 100 con una certificación en un
registro público, 400 en un directorio de propiedad latina y 643 con el español entre sus idiomas.
Todos llevan además un nombre de dueño hispano y un vecindario en columnas que las reglas no leen.

| Paso                                          | Resultado                                                                                                                                      |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Inserción y resolución                        | 11.9 s                                                                                                                                         |
| Evaluación completa                           | **2.1 s** (2 407 negocios/s): 5 000 fuentes leídas, 1 143 evidencias, 100 `verified`, 400 `probable`, 643 afinidad de mercado, 3 857 `unknown` |
| Segunda evaluación                            | 1.0 s: 0 evidencias nuevas y 0 evaluaciones reescritas                                                                                         |
| Primera página con los cuatro filtros latinos | 46.9 ms (400 coincidencias; umbral de la lista 250 ms)                                                                                         |
| Detalle latino de un negocio                  | 2.5 ms                                                                                                                                         |

Ni el nombre del dueño ni el vecindario de los 5 000 registros produjeron evidencia alguna. La lista sin
filtros con 20 000 negocios sirve una página en 7.9 ms (sin `ANALYZE`, como la prueba del
repositorio). Todo se midió con un juego abierto ocupando buena parte de la CPU: las cifras son
conservadoras.

## 13. Riesgos y deuda pendiente

| Asunto                                                                                                                                                                                            | Cuándo importa                                         |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| **No verificado en la ventana real de la app.** La UI está probada con fixtures generadas por las consultas reales y el flujo IPC contra el worker real, pero no se pulsó en la ventana de Tauri. | Antes de cerrar la fase, si se quiere evidencia visual |
| Ninguna fuente real aporta todavía atributos de propiedad o certificaciones: con datos reales el máximo alcanzable hoy es `high_confidence` (sitio propio).                                       | Cuando haya un conector de registro o directorio       |
| Los patrones leen inglés y español; una declaración en imagen, PDF o video no se lee.                                                                                                             | Sitios que declaran en imágenes o documentos           |
| La frescura se mide desde la adquisición, no desde la fecha de publicación.                                                                                                                       | Fuentes antiguas importadas tarde                      |
| La evidencia organizacional se infiere solo por dominio compartido.                                                                                                                               | Marcas sin dominio común                               |
| Un título de página que es un nombre comercial con una frase de propiedad se lee como declaración.                                                                                                | Nombres del tipo "Latino Owned Plumbing"               |
| La confianza combina factores documentados; no es una probabilidad calibrada.                                                                                                                     | Fase 11 (no debe usarse como score)                    |
| La frescura cambia con el tiempo: una reevaluación meses después escribe evaluaciones nuevas aunque no haya evidencia nueva.                                                                      | Historial de negocios con evidencia envejeciendo       |
| Los filtros latinos de la lista usan subconsultas correlacionadas: 47 ms con 5 000 negocios.                                                                                                      | Proyectos de cientos de miles de negocios              |
| La evaluación carga en memoria la evidencia de un proyecto completo.                                                                                                                              | Proyectos de millones de evidencias                    |
| El candidato de un modelo aún no tiene quien lo produzca.                                                                                                                                         | Fase 10                                                |
| La semilla de desarrollo añade 8 registros sintéticos con evidencia latina (36 negocios visibles).                                                                                                | Solo desarrollo                                        |

## 14. Lo que esta fase NO hizo

Sin auditoría de sitio (08), sin validación de contactos más allá de la estructural (09), sin
runtime de IA local (10) y sin ICP ni scoring (11). Ninguna pantalla puntúa, califica ni llama «lead»
a un negocio, y ninguna usa la evidencia latina como puntuación.
