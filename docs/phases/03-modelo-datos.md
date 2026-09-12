# Fase 03 — Modelo de datos, procedencia y persistencia

Fecha: 2026-09-11 · Estado: **completada y verificada** (tests en las tres capas y app real en Windows 11).

## 1. Verificación previa de las fases 01 y 02

Antes de empezar se instalaron las dependencias desde los lockfiles y se ejecutó `pnpm check` sobre
el commit de la fase 02: **exit 0** con Python 60 ✓ / 1 omitida (Ollama detenido), TypeScript 107 ✓ y
Rust 27 ✓, las mismas cifras de sus informes. Durante la fase aparecieron dos defectos heredados, ya
corregidos (sección 13): el `.prettierignore` excluía sin querer `apps/desktop/src/components/data/`
del chequeo de formato, y la tabla densa dejaba que una celda larga se deslizara bajo la columna fija.

## 2. Qué se construyó

El modelo de datos central, completo y con reglas reales en la base de datos:

- **52 tablas** en SQLite (migración `0002_core_model.sql`) que cubren todas las entidades mínimas del
  prompt, **29 enumeraciones** como tablas de búsqueda, **máquinas de estado** con disparadores,
  procedencia de cada valor, historial inmutable, borrado recuperable y auditoría.
- **Snapshots analíticos** en DuckDB (`0002_business_snapshots.sql`) con copia **Parquet
  particionada** y esquema explícito, registrados en SQLite con su lineage.
- **Repositorios** (única capa que ejecuta SQL del modelo), transacciones con savepoints, paginación
  keyset, búsqueda FTS5, backups verificados y recuperación controlada.
- **Contrato de datos** en Pydantic con tipos TypeScript generados, espejo Rust validado y
  `data_contract_version` comprobado en el handshake del worker.
- **Businesses y Datasets conectados a consultas reales**, con inspector de procedencia y un seed
  sintético solo para desarrollo, claramente marcado.

La documentación completa (diagrama lógico, diccionario de datos, procedencia, estados, backup y
contratos) está en [data-model.md](../data-model.md). Decisiones D-015 a D-023 en
[decisions.md](../decisions.md).

## 3. Modelo y almacenamiento

| Dónde                         | Qué                                                                         |
| ----------------------------- | --------------------------------------------------------------------------- |
| SQLite `lead.sqlite3`         | Estado operacional mutable, procedencia, evidencia, historial, auditoría    |
| DuckDB `analytics.duckdb`     | Filas de cada snapshot de dataset (consultas analíticas)                    |
| Parquet `datasets/<ds>/<sn>/` | Copia duradera y portable de cada snapshot, particionada por `region`       |
| Archivos (`artifact_path`)    | Cuerpos de páginas y artefactos desde la fase 05, siempre con ruta relativa |
| `backups/`                    | Copias verificadas de SQLite y DuckDB                                       |

Grupos de entidades: espacio de trabajo (`projects`, `campaigns`, `icp_profiles` + versiones), fuentes
y procedencia (`source_connectors` + configuraciones y límites, `source_runs`, `source_queries`,
`acquired_pages`, `raw_records` inmutables, `extractions`), negocios canónicos y observaciones
(nombres, ubicaciones, áreas de servicio, categorías y servicios, websites, páginas, decision makers,
contact points y validaciones), evidencia y evaluaciones latinas separadas, auditoría de websites
(auditorías, tecnologías, elementos de conversión, hallazgos), IA local (runs, evidencia usada,
outputs), scoring (modelos, versiones, runs, scores, componentes, explicaciones), jobs (tareas,
intentos, checkpoints, eventos con errores, métricas), datasets (snapshots, lineage, exports) y
revisión (tags, notas, decisiones humanas, audit log).

Principios aplicados, cada uno con su mecanismo:

| Principio del prompt                                     | Cómo se cumple                                                                                                                                                     |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1. Fuente, URL/id, momento, método, extractor, confianza | Cada observación → `extraction` (extractor + versión) → `raw_record` (URL o id obligatorio, método, momento) → run → conector; la confianza vive en la observación |
| 2. Lo normalizado no borra lo crudo                      | Columnas `*_raw` junto a `*_normalized`; `raw_records` rechaza `UPDATE` y `DELETE`                                                                                 |
| 3. La IA no sustituye hechos                             | Tablas propias con modelo, digest, prompt, schema, hash de entrada y evidencia usada                                                                               |
| 4. Scores con versión y desglose                         | `business_scores` → `scoring_model_versions`, `component_scores` con peso y contribución, explicaciones                                                            |
| 5. Eliminaciones recuperables o auditadas                | `deleted_at` + motivo obligatorio y entrada de auditoría; un disparador impide borrar filas vivas                                                                  |
| 6. UTC consistente                                       | Formato único `YYYY-MM-DDTHH:MM:SS.sssZ`, verificado por `CHECK` con `strftime` en cada columna de fecha                                                           |
| 7. IDs estables                                          | UUIDv7 monotónico; nombres y dominios nunca son clave                                                                                                              |
| 8. Migraciones hacia adelante con respaldo               | Checksums, una transacción por migración y backup verificado antes de migrar una base con datos                                                                    |

## 4. Estados y transiciones

Las 12 enumeraciones exigidas (más `evidence_status` y las auxiliares) son tablas de búsqueda
referenciadas por clave foránea, así que no hay strings libres. Las transiciones válidas de
`business_status`, `source_run_status`, `job_status`, `evidence_status`, `validation_status`,
`inference_status`, `lead_stage` y `export_status` están en `status_transitions` y las imponen
disparadores `BEFORE UPDATE OF`; el repositorio valida lo mismo antes y audita el cambio.
`latino_status` no tiene máquina: cada evaluación es una fila nueva que sustituye a la anterior.

## 5. Persistencia

- **Repositorios** (`repository.py`): inserción genérica validada por Pydantic, `get`,
  `change_status`, `soft_delete`/`restore` auditados, `list_businesses` (keyset + FTS5 + filtros),
  `get_business_detail` con procedencia por valor, `trace_lineage` y `list_datasets`. La UI nunca
  ejecuta SQL.
- **Transacciones**: `atomic()` usa `BEGIN IMMEDIATE` y, anidado, savepoints.
- **Búsqueda textual**: FTS5 con `remove_diacritics` sobre nombre, nombres observados y lugares,
  sincronizado por disparadores; la entrada del usuario se reduce a palabras entre comillas.
- **Paginación keyset**: cursor opaco con clave y id; un cursor de otra ordenación se rechaza.
- **DuckDB y Parquet** (`datasets.py`): filas vía JSON por líneas con tipos explícitos, copia Parquet
  particionada y registro en SQLite; cualquier fallo deshace lo anterior.
- **Rutas portables**: todo lo guardado es relativo al directorio de datos, con `/`, sin `..`;
  lo comprueban el `CHECK`, el tipo Pydantic y `storage.data_path()`.
- **Backup, integridad y recuperación**: backup online verificado antes de migrar; `quick_check` en
  cada comprobación de salud; una base dañada se pone en cuarentena (nunca se borra) y se restaura el
  backup más reciente que pasa `integrity_check` y `foreign_key_check`; sin backup utilizable no se
  toca nada y se informa. Runbook en [operations.md](../operations.md).

## 6. Contratos compartidos

Fuente de verdad: `contracts.py` (protocolo) y `domain.py` (modelo de datos), en Pydantic. `pnpm
contracts` genera JSON Schema, tipos TypeScript y fixtures; los fixtures de datos salen de las
consultas reales del repositorio sobre una base temporal con datos sintéticos, ids deterministas y
reloj fijo. Tres comprobaciones automáticas cierran el círculo: SQL ↔ Pydantic (columna a columna,
enumeraciones, claves foráneas y transiciones), Pydantic ↔ JSON ↔ Rust (round-trip y rechazo de
payloads manipulados) y JSON ↔ TypeScript (exhaustividad de claves en compilación y en ejecución).

Rust (`src-tauri/src/data.rs`) no confía en nadie: rechaza campos desconocidos, valores de enumeración
desconocidos, ids no canónicos, timestamps fuera de formato, confianzas fuera de [0, 1] y cursores mal
formados; valida cada consulta de la UI y comprueba cada página contra la consulta que la produjo.
`data_contract_version = 1` viaja en el handshake y el núcleo rechaza un worker con otra versión.

## 7. Datos de prueba

`testdata.py` genera datos **sintéticos** evidentes: nombres "Synthetic …", dominios `.example`,
teléfonos del rango ficticio 555-01xx, direcciones en "Example Ave", payloads con `"test_data": true`,
proyecto marcado `is_synthetic` y ninguna persona. La UI etiqueta cada fila y cada detalle como datos
de prueba. El seed existe solo en desarrollo: el núcleo lo rechaza en release y el worker sin
`LEAD_DEV_TOOLS=1`.

## 8. Integración UI

- **Businesses**: consulta real con búsqueda en servidor (debounce 250 ms), paginación con "Cargar
  más", estados vacío, sin coincidencias y error con reintento. El inspector carga el detalle y
  muestra cada valor tal como se adquirió y normalizado, con fuente, extractor, versión, momento y
  confianza; la evidencia usa el componente de la fase 02.
- **Datasets**: módulo ahora disponible, con snapshots, última versión, filas y fecha de captura.
- **Solo desarrollo**: botón "Cargar datos de prueba sintéticos · Dev", que crea el espacio sintético
  una sola vez y abre un negocio en el inspector.

## 9. Archivos

76 rutas respecto a la fase 02, incluido este informe: 30 nuevas, 45 modificadas y 1 eliminada
(`cells.tsx` se movió a `components/data`).

- Worker: `domain.py`, `repository.py`, `datasets.py`, `testdata.py`, dos migraciones nuevas;
  cambios en `contracts.py`, `storage.py`, `handlers.py`, `worker.py`, `settings.py`,
  `export_contracts.py`. Tests nuevos `test_model.py`, `test_repository.py`, `test_datasets.py`;
  ampliados `test_storage.py`, `test_worker_process.py`, `test_settings.py`, `conftest.py`.
- Núcleo Rust: `data.rs` nuevo; cambios en `protocol.rs`, `error.rs`, `worker.rs`, `commands.rs`,
  `lib.rs` y en los tests `contracts.rs`, `ipc.rs`, `worker.rs`, `common/mod.rs`.
- UI: `useKeysetPages.ts`, `PagedFooter.tsx`, `BusinessInspector.tsx`, `DatasetsScreen.tsx` y sus
  tests, `contracts.test.ts`; `cells.tsx` movido a `components/data`; cambios en `ipc.ts`,
  `BusinessesScreen`, `InspectorPanel`, `TableWorkspace`, `columns`, `filters`, `DataTable`,
  navegación, rutas y locales.
- Contratos: schema y tipos regenerados, 6 fixtures nuevos, 3 actualizados.
- Documentación: `data-model.md` nuevo; `architecture.md`, `decisions.md`, `operations.md`,
  `ui-system.md`, `README.md`, `.prettierignore`; 4 capturas.

## 10. Comandos principales

```text
pnpm install --frozen-lockfile ; uv --no-cache sync --directory services/research-engine --locked
pnpm check                                   # verificación previa de fases 01-02 y final
pnpm contracts                               # schema, fixtures y tipos TS
uv run --directory services/research-engine pytest -q -s -k performance
cargo test --manifest-path src-tauri/Cargo.toml
pnpm --filter @lead/desktop build
pnpm dev                                     # app real + capturas
```

## 11. Pruebas y resultados exactos

| Verificación                                          | Resultado                                                       |
| ----------------------------------------------------- | --------------------------------------------------------------- |
| `pnpm check` (contratos, lint, formato, tipos, tests) | **exit 0**                                                      |
| Python: ruff, ruff format, mypy `--strict`            | sin errores (26 archivos)                                       |
| Python: pytest                                        | **280 passed, 1 skipped** (Ollama detenido)                     |
| TypeScript: oxlint, prettier, `tsc`                   | sin avisos                                                      |
| TypeScript: vitest                                    | **121 pruebas, 17 archivos**                                    |
| Rust: rustfmt, clippy `-D warnings`                   | sin avisos                                                      |
| Rust: cargo test                                      | **36 pruebas** (21 unitarias, 5 contratos, 2 IPC, 8 supervisor) |
| Build de producción del frontend                      | OK · JS 734,5 kB (223,4 kB gzip), CSS 54,0 kB (9,8 kB gzip)     |

Pruebas obligatorias del prompt:

| Obligatoria                            | Dónde                                                                                                                                                                                      |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Migración desde base vacía             | `test_migrating_an_empty_database_creates_every_table`, `test_fresh_stores_are_created_migrated_and_queried`                                                                               |
| Migraciones repetibles / idempotencia  | `test_checks_are_idempotent`, `test_upgrading_an_existing_store_backs_it_up_first`                                                                                                         |
| Constraints y foreign keys             | `test_constraints_reject_invalid_rows`, `test_confidence_json_and_paths_are_checked`, `test_every_enum_column_references_its_lookup_table`, inmutabilidad, transiciones y borrado          |
| Rollback transaccional                 | `test_a_failed_operation_rolls_back_every_statement` (incluye savepoints), rollback de migraciones SQLite y DuckDB                                                                         |
| Round-trip Pydantic ↔ JSON ↔ Rust ↔ TS | `test_data_contract_fixtures_round_trip`, `data_contract_fixtures_round_trip_and_pass_validation`, `tampered_data_payloads_are_rejected`, `contracts.test.ts`                              |
| Inserción y consulta paginada          | `test_keyset_pages_visit_every_row_exactly_once`, `test_search_matches_names_and_places_without_accents`, cursores manipulados                                                             |
| Escritura/lectura DuckDB y Parquet     | `test_snapshot_rows_are_identical_in_duckdb_and_parquet`, `test_duckdb_table_matches_the_explicit_schema`                                                                                  |
| Lineage de un valor normalizado        | `test_lineage_reaches_the_raw_record_of_a_normalized_value`                                                                                                                                |
| Backup + restore en archivo temporal   | `test_backup_and_restore_round_trip`, recuperación de base dañada con y sin backup, DuckDB dañado                                                                                          |
| Concurrencia y bloqueo de SQLite       | `test_sqlite_locking_blocks_writers_but_not_readers`, `test_concurrent_writers_all_commit_through_the_busy_timeout`                                                                        |
| Rendimiento con dataset sintético      | `test_performance_on_a_synthetic_dataset`: 20.000 negocios (180.000 filas) en 24,8 s; primera página 19,9 ms, página 51 17,7 ms, búsqueda 29,4 ms, orden por fecha 15,4 ms, detalle 2,1 ms |

Además: proceso real del worker por stdio (`test_data_requests_are_served_from_the_store`,
`test_development_seed_creates_openable_synthetic_data`), IPC de Tauri contra el worker real
(`data_commands_serve_real_store_queries_through_ipc`, con rechazo del seed en release) y worker con
otra versión de contrato rechazado (`worker_with_another_data_contract_is_refused`).

## 12. Verificación en la app real

Con `pnpm dev`, ventana maximizada a 1920×1009 y la interfaz en español:

| Comprobación         | Resultado                                                                                                                                  |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Businesses sin datos | Estado vacío honesto, "0 filas", búsqueda y acción Dev visibles ([captura](img/03-businesses-empty.png))                                   |
| Seed sintético       | 24 negocios marcados "Datos de prueba" en 118 ms, inspector abierto con procedencia por valor ([captura](img/03-businesses-synthetic.png)) |
| Búsqueda "mia"       | 4 negocios de Miami, "Mostrando 4 de 4" (prefijo sobre lugares)                                                                            |
| Datasets             | "Synthetic businesses", 1 snapshot, versión 1, 24 filas ([captura](img/03-datasets.png))                                                   |
| System               | SQLite y DuckDB en schema v2, todo OK ([captura](img/03-system.png))                                                                       |
| Cierre               | Worker con exit 0 y sin procesos huérfanos; ningún WARN ni ERROR en el log de la sesión                                                    |

## 13. Defectos encontrados y corregidos durante la fase

1. **`.prettierignore` sin anclar** (heredado de la fase 01): `data/` y `logs/` también excluían
   `apps/desktop/src/components/data/`, así que `DataTable.tsx` y `filters.test.ts` nunca se revisaban
   y estaban mal formateados. Patrones anclados y archivos formateados.
2. **Tabla con layout automático** (heredado de la fase 02): con la insignia en español, la columna
   fija "Negocio" crecía y tapaba "Ubicación". Ahora `table-layout: fixed`; los valores largos se
   truncan y la columna se ensanchó a 300 px.
3. `CHECK (x = strftime(...))` aceptaba fechas imposibles porque `NULL = x` pasa el check: se usa
   `IS`.
4. DuckDB `executemany` tardaba 2,3 s por 500 filas y `VALUES` múltiples con parámetros cerraba el
   intérprete: carga por JSON por líneas (0,09 s para 20.000 filas).
5. Leer `TIMESTAMPTZ` desde Python exige `pytz`: DuckDB guarda `TIMESTAMP` UTC.
6. El orden de contactos usaba una columna inexistente (`observed_at`): detectado al generar fixtures.
7. Las conexiones abiertas con el context manager de `sqlite3` no se cierran y bloqueaban el archivo
   en Windows durante la restauración: los tests cierran explícitamente.
8. Una regla de lint de React (setState síncrono en efecto) obligó a reescribir la carga paginada
   para actualizar el estado solo al llegar la respuesta.

## 14. Riesgos y pendientes

1. **Rendimiento de ingesta**: ~800 negocios/s con todas las restricciones y el índice FTS activos
   (dos actualizaciones del índice por negocio). Suficiente para descubrimiento por lotes; una
   importación masiva debería reconstruir el índice en una pasada (D-020).
2. **Recuperación sin interfaz**: la restauración automática funciona, pero sin backup la base dañada
   se informa y se resuelve con el runbook; no hay botón de backup/restauración en la UI (fases 13-14).
3. **Esquema de fases futuras provisional**: las tablas de auditoría web, IA, scoring y jobs se
   modelaron con lo que piden los prompts 07-13; esas fases pueden añadir columnas o enumeraciones
   mediante migraciones aditivas (lookup tables y `status_transitions` hacen barato ampliarlas).
4. **Referencias polimórficas** (`entity_tags`, `notes`, `review_decisions`, `audit_log`) no pueden ser
   claves foráneas; se apoyan en `entity_type` restringido e ids UUID globales.
5. **Auditoría desde el repositorio**: los disparadores no conocen el actor, así que la auditoría la
   escribe la capa de repositorios; otra vía de escritura no quedaría auditada (D-004 garantiza que
   no existe).
6. **Versión de snapshot sin bloqueo**: dos snapshots simultáneos del mismo dataset chocan en la
   restricción única en vez de esperar; el orquestador de la fase 13 los serializará.
7. **Detección de corrupción en DuckDB** por el mensaje de error del motor; si cambia en una versión
   futura, el caso se reportaría como error sin recuperar (nunca se toca un archivo bloqueado).
8. **Datos sintéticos en el `data/` de desarrollo**: quedan hasta borrar esa carpeta; nunca llegan a
   una build de release.
9. **Tests de UI**: en jsdom `user.type` no llegaba al buscador del portal de la barra superior; el
   test usa `fireEvent.change` y el tecleo real se verificó en la app.
