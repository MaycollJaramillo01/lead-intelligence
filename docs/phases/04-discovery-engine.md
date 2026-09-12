# Fase 04 — Discovery Engine y arquitectura de fuentes

Fecha: 2026-09-12 · Estado: **completada y verificada** (`pnpm check` en verde y una ejecución real
contra la API pública de USAspending.gov).

## 1. Verificación previa de las fases 01–03

Antes de empezar se instalaron las dependencias desde los lockfiles (`pnpm install --frozen-lockfile`
y `uv --no-cache sync --locked`) y se ejecutó `pnpm check` sobre el commit de la fase 03: **exit 0**
con Python 283 ✓ / 1 omitida (Ollama detenido), TypeScript 121 ✓ y Rust 36 ✓. No apareció ningún
defecto heredado.

## 2. Qué se construyó

Un **Discovery Engine extensible**: las campañas se escriben en un DSL versionado y se ejecutan
contra adaptadores de fuente intercambiables. Esta fase **descubre referencias**; no hay crawling
profundo, ni evaluación latina, ni scoring, y la interfaz nunca llama «lead» a un candidato.

- **Contrato común de adaptadores** (`discovery/sources/base.py`): metadata honesta, capacidades,
  términos, credenciales, paginación, checkpoints, rate limits y errores estructurados.
- **Cuatro fuentes**: importación local CSV/TSV/JSON/JSONL con mapeo de columnas y preview, semillas
  manuales, la **API pública de USAspending.gov** (sin credenciales, paginada, filtra por estado,
  ciudad, ZIP, NAICS y fecha) y una **plantilla deshabilitada** para fuentes con API key.
- **Motor de ejecución** (`discovery/engine.py`): un job por ejecución, un `source_run` y un
  `job_task` por fuente, una transacción por página junto a su checkpoint, límites, reintentos solo
  para fallos transitorios, `Retry-After`, pausa/reanudación/cancelación cooperativas y recuperación
  tras reiniciar la aplicación.
- **Modelo**: migración `0003_discovery_candidates.sql` (50 líneas) con `discovery_candidates`,
  inmutable y permanente como el `raw_record` que proyecta.
- **11 mensajes IPC nuevos** (aditivos, `protocol_version` sigue en 1), espejo en Rust con
  validación y **tres pantallas conectadas a datos reales**: Discovery, Campaigns y Sources.

Referencia completa en [discovery.md](../discovery.md). Decisiones D-024 a D-029 en
[decisions.md](../decisions.md).

## 3. La regla central: un adaptador por fuente

```python
class DiscoverySource(Protocol):
    metadata: SourceMetadata
    async def validate_config(self, config) -> list[str]: ...
    async def estimate(self, request, context) -> Estimate: ...
    async def discover(self, request, cursor, context) -> DiscoveryPage: ...
    def checkpoint(self, cursor) -> JsonObject: ...
```

Los adaptadores son **sin estado**: el motor posee el cursor, las transacciones, los límites, los
reintentos y la procedencia. Añadir una fuente son un módulo pequeño y una línea en el registro
(`discovery/registry.py`), que además es quien crea y actualiza su fila en `source_connectors`.

Las **capacidades son datos**: el constructor de campañas lee `SourceMetadata.capabilities` para
decirle al operador qué criterios ignorará cada fuente, en lugar de descartarlos en silencio.

## 4. Legalidad y límites, por diseño

| Regla del prompt                                                           | Mecanismo                                                                                                                                               |
| -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Nada de CAPTCHA, autenticación indebida, bypass ni rotación de identidades | No existe código para ello; `docs/discovery.md` §2 y las pruebas lo fijan                                                                               |
| Una fuente que prohíbe automatización se bloquea                           | `automation_allowed=False` → `SourceHealth.DISABLED`, y sigue deshabilitada aunque la base diga otra cosa                                               |
| Una fuente con API key exige configuración válida                          | `requires_credentials=True` → arranca deshabilitada, falla con `auth_required`, nunca inventa clave ni endpoint                                         |
| Ritmo prudente                                                             | Límite por conector (30/min y 2000/día en USAspending, muy por debajo de lo que la API pide), jitter, presupuesto diario contado desde `source_queries` |

`directory.api_template` está registrada, visible y **deliberadamente sin funcionar**: documenta lo
que declara un conector con clave sin fingir que devuelve datos.

## 5. DSL de campañas

`campaigns.criteria_json` guarda un documento `DiscoveryCriteria` y `criteria_schema_version` dice
qué versión sigue (hoy 1): geografía (país/estado/metro/ciudad/ZIP/radio), verticales NAICS,
categorías, servicios, palabras clave, exclusiones, idiomas de mercado, proxies de tamaño, frescura,
estrategia broad/narrow, fuentes con prioridad y tope de registros, y límites.

`check_criteria` separa **errores** (no se puede ejecutar) de **avisos** (se puede, pero conviene
saberlo): fuente desconocida o no lista, fuente duplicada, criterio que una fuente ignorará, sin
geografía, estrategia estrecha sin nada por lo que estrechar, y el recordatorio de que el idioma de
mercado **es una señal, nunca prueba de propiedad**. Una campaña con errores se guarda igual como
borrador. Las estimaciones vienen de las propias fuentes y van siempre marcadas como estimación.

## 6. Una página, una transacción

```text
job (discovery)  ← pausa / reanudación / cancelación actúan aquí
 ├── job_task ── task_attempt (uno por ejecución)
 └── source_run ── source_query ── acquired_page ── raw_record ── discovery_candidate
 └── job_checkpoint (cursor + contadores, uno por página)
 └── job_event (progreso, límites, errores con código y clasificación de reintento)
```

La consulta, la adquisición, los registros crudos, los candidatos y el checkpoint se escriben en una
sola transacción, con el checkpoint al final: «no repetir una página al reanudar» es una propiedad de
la base de datos, no del camino de código. El progreso se lee del checkpoint en lugar de recontarse.

Un `source_run` solo se marca `succeeded` si terminó de verdad (agotado o topado por un límite);
cualquier otra parada lo deja reanudable. Un job que quedó `running` porque el worker murió pasa a
`paused` con un evento `run_interrupted` al arrancar: **nunca se reanuda solo**, porque eso sería
actividad de red sin avisar.

## 7. Detección ligera de repetidos

`dedup_key` es la conjetura barata del conector: id estable de la fuente si existe, si no dominio, si
no nombre normalizado más la señal de lugar más fuerte. Un candidato cuya clave ya se vio **en la
misma campaña** apunta al primero con `duplicate_of_id`. No se fusiona ni se descarta nada, y las
claves no cruzan campañas: dos campañas que encuentran el mismo negocio conservan su candidato y su
propio lineage. La resolución de entidad real es la fase 06.

## 8. UI conectada

| Pantalla      | Qué hace con datos reales                                                                                                                                                                                                                                                                                       |
| ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Sources**   | Catálogo con capacidades, resumen y enlace de términos, límite de ritmo, presupuesto, si necesita clave, salud y último error                                                                                                                                                                                   |
| **Campaigns** | Lista con nº de fuentes, ejecuciones y candidatos; crear, editar y **duplicar** (la copia es una campaña nueva, sin historial); constructor con validación en vivo (debounce 400 ms) y estimaciones bajo demanda                                                                                                |
| **Discovery** | Selección de campaña, selector de archivo con preview de columnas y filas inutilizables, iniciar/pausar/reanudar/cancelar, progreso por fuente (páginas, registros, candidatos, repetidos, peticiones, error), tabla de candidatos e inspector con el registro crudo, su sha256 y toda la cadena de procedencia |

`Discovery` solo consulta mientras una ejecución se mueve; cuando nada se mueve deja de preguntar.
Las tres pantallas dicen en palabras que **un candidato es una referencia, no un lead calificado**.
`navigation.ts` pasa los tres módulos a `available`; los diez que siguen sin existir continúan
declarando su fase.

## 9. Archivos

**Nuevos (Python):** `discovery/` (`engine.py`, `registry.py`, `normalize.py`,
`sources/{base,file_import,seeds,usaspending,api_template}.py`, 2 845 líneas),
`migrations/sqlite/0003_discovery_candidates.sql`, `tests/test_discovery.py`,
`tests/test_discovery_live.py`, `tests/recorded/` (3 respuestas reales grabadas + README).

**Modificados (Python):** `domain.py` (candidato, DSL y 20 modelos de lectura), `repository.py`
(campañas, ejecuciones, candidatos, checkpoints, presupuesto diario), `contracts.py` (11 tipos de
mensaje), `handlers.py`, `worker.py`, `settings.py` (`LEAD_DISCOVERY_OFFLINE`), `testdata.py`
(`seed_discovery`), `export_contracts.py` (12 fixtures nuevas).

**Rust:** `data.rs` (espejo completo con `deny_unknown_fields` y validaciones de página, ejecución,
detalle y preview), `commands.rs` (10 comandos), `worker.rs`, `lib.rs`, `tests/contracts.rs`,
`tests/ipc.rs` (flujo completo end-to-end contra el worker real).

**UI:** `features/{sources,campaigns,discovery}/` (pantallas, constructor, inspector, panel de
ejecución, helpers de criterios y draft), `components/ui/textarea.tsx`, `lib/useLoaded.ts`,
`lib/ipc.ts`, `lib/locales/{en,es}.ts` (+187 claves cada uno), `app/{navigation,ModuleRoute}.tsx`.

**Docs:** `discovery.md` (nuevo), `decisions.md` (D-024…D-029), `architecture.md`, `data-model.md`,
`README.md`.

## 10. Comandos ejecutados y resultados exactos

| Comando                                                            | Resultado                                                      |
| ------------------------------------------------------------------ | -------------------------------------------------------------- |
| `pnpm install --frozen-lockfile`                                   | exit 0                                                         |
| `uv --no-cache sync --directory services/research-engine --locked` | exit 0                                                         |
| `pnpm check` (base, fase 03)                                       | **exit 0** — Python 283 ✓ / 1 omitida, TS 121 ✓, Rust 36 ✓     |
| `pnpm contracts`                                                   | esquema + 25 fixtures + `contracts.ts` regenerados             |
| `pnpm check` (final)                                               | **exit 0**                                                     |
| `pnpm test:py`                                                     | **318 ✓, 2 omitidas** (Ollama detenido; prueba en vivo opt-in) |
| `pnpm test:ts`                                                     | **138 ✓** en 20 archivos                                       |
| `pnpm test:rust`                                                   | **39 ✓** (21 unitarias + 7 contratos + 3 IPC + 8 worker)       |
| `pnpm lint` / `pnpm typecheck`                                     | ruff, oxlint, prettier, clippy, tsc y mypy sin avisos          |
| `LEAD_LIVE_SOURCE_TESTS=1 pytest tests/test_discovery_live.py`     | **1 ✓** contra la API real                                     |
| Ejecución real end-to-end (script desechable, almacén temporal)    | ver abajo                                                      |

### Ejecución real contra USAspending.gov

Campaña «Hialeah construction», NAICS 23, Florida/Hialeah, desde 2024-01-01, tope de 15 registros:

```text
store sqlite: ok (sqlite 3.50.4 · schema v3 · query ok)
source usaspending.recipients: ready enabled=True
source directory.api_template: disabled enabled=False
warning: language_is_a_signal - Market language is an affinity signal, never evidence of ownership
run finished: status=succeeded
  usaspending.recipients: succeeded pages=1 records=15 candidates=7 repeats=8 requests=1 cursor=2
  event info/limit_reached {'limit': 'max_records_for_source'}
unique candidates: 7   (WARRIOR SERVICE COMPANY LLC, ALFASPIRE INC., GENISIS GLOBAL LLC, …)
lineage: connector=usaspending.recipients method=api_request http=200
  request=https://api.usaspending.gov/api/v2/search/spending_by_award/?page=1&limit=15
campaign: runs=1 candidates=7
```

15 registros reales, 7 negocios distintos, 8 repetidos enlazados, el límite por fuente alcanzado y
reportado, y el cursor `2` guardado para reanudar.

## 11. Pruebas de aceptación del prompt

| Criterio                                      | Prueba                                                                                                                                                                                        |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Contrato común de adaptadores                 | `test_every_registered_source_satisfies_the_contract`                                                                                                                                         |
| CSV/JSON con columnas incompletas/malformadas | `test_import_preview_maps_columns_and_names_the_rows_it_cannot_use`, `test_a_malformed_file_is_refused_with_a_reason`, `test_a_ragged_row_keeps_its_extra_values_instead_of_shifting_columns` |
| Paginación y checkpoints                      | `test_pagination_follows_the_cursor_to_the_end` (respuestas reales grabadas)                                                                                                                  |
| Reintentos con backoff y `Retry-After`        | `test_a_rate_limited_page_is_retried_and_then_succeeds` (peticiones `[1,2,2,2,3]`)                                                                                                            |
| Cancelación cooperativa                       | `test_cancelling_stops_the_run_and_leaves_what_it_already_found`                                                                                                                              |
| Recuperación al reiniciar la aplicación       | `test_a_run_interrupted_by_a_restart_is_paused_and_resumable`                                                                                                                                 |
| No duplicar páginas al reanudar               | `test_a_paused_run_resumes_from_its_checkpoint_without_repeating_a_page`                                                                                                                      |
| Resultados crudos inmutables con lineage      | `test_raw_records_are_immutable_and_every_candidate_points_at_one`                                                                                                                            |
| Límites globales y por fuente                 | tres pruebas de límites (páginas, presupuesto de peticiones, registros)                                                                                                                       |
| Errores diferenciados                         | `blocked`, `invalid_query`, `source_unavailable`, `rate_limited`, `auth_required` cubiertos                                                                                                   |
| Fuente con clave que se bloquea               | `test_a_source_that_needs_a_key_refuses_instead_of_inventing_one`                                                                                                                             |
| Sin mezclar campañas                          | `test_candidates_of_one_campaign_never_mix_with_another`                                                                                                                                      |
| UI conectada sin mocks engañosos              | `the_discovery_flow_runs_end_to_end_through_ipc` (Rust, worker real) + 16 pruebas de las tres pantallas                                                                                       |

## 12. Riesgos y deuda pendiente

| Asunto                                                                                                                                                                                                                        | Cuándo importa                                                                     |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| **No verificado en la ventana real de la app en esta sesión**: se pidió permiso de control y se denegó. La app quedó levantada con `pnpm dev` para que se revise a mano. Todo lo demás sí está probado contra el worker real. | Antes de cerrar la fase, si se quiere la misma evidencia visual que en 02 y 03     |
| **XLSX no implementado** a propósito (es un zip de XML, no texto); la UI lo dice y pide exportar a CSV.                                                                                                                       | Cuando un operador insista en XLSX                                                 |
| **Archivo importado limitado a 4 000 000 caracteres** porque viaja en un frame IPC (D-029).                                                                                                                                   | Importaciones grandes → conector por ruta                                          |
| **Una página a la vez, fuentes en serie** (`ponytail:` en `engine.py`).                                                                                                                                                       | Si una ejecución real se ve limitada por espera, no por el rate limit de la fuente |
| **Búsqueda de candidatos con `LIKE`** sobre el nombre normalizado (`ponytail:` en `repository.py`).                                                                                                                           | Cuando las listas de candidatos crezcan                                            |
| **USAspending solo cubre negocios con contratos federales**: es una señal, no un directorio general.                                                                                                                          | La cobertura amplia necesita directorios con API key                               |
| **La prueba en vivo es opt-in** (`LEAD_LIVE_SOURCE_TESTS=1`); si la API cambia de forma, lo detecta esa prueba, no la suite por defecto.                                                                                      | Ejecutarla antes de cada release                                                   |
| `manual.seeds` aparece como `needs_configuration` en el catálogo hasta que una campaña le da semillas (es correcto: sin semillas no puede ejecutar), lo que puede leerse como un defecto a primera vista.                     | Documentado en `discovery.md` §5                                                   |

## 13. Lo que esta fase NO hizo

Sin crawling profundo ni fetch de webs de negocio (fase 05), sin normalización ni resolución de
entidad (06), sin evaluación latina (07), sin auditoría de sitio (08), sin inteligencia de contacto
(09), sin IA local (10) y sin ICP ni scoring (11). Los candidatos no tienen score, veredicto ni
etapa de lead, y ninguna pantalla insinúa que lo tengan.
