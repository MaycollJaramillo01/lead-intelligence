# Fase 10 — IA local, Ollama y RTX 4060

Fecha: 2026-09-13 · Estado: **completada**; `pnpm check` completo en verde en una sola pasada, build de
release correcto y benchmark real en la RTX 4060 con los 4 modelos (secciones 9 y 11) · código en `main`.

## 1. Punto de partida

La fase parte de `main` con la fase 09 (`1b20bea`), en el worktree `fase-5-1a1090`. Existían desde la fase 01 la detección de Ollama (`ollama.py`) y de la GPU
(`gpu.py`, `nvidia-smi` y la API del driver CUDA) en el diagnóstico; desde la fase 03 las tablas
reservadas `ai_inference_runs`, `ai_inference_evidence` y `ai_inference_outputs`, que nadie escribía;
y desde la fase 07 `latino.store.ingest_model_candidates`, que acepta declaraciones propuestas por un
modelo solo si las reglas las confirman en la página. No había runtime, tareas, cola ni pantalla.

En la máquina: RTX 4060 8 GB (driver 610.88, CUDA 13), i5-13500, 15,7 GB de RAM, Ollama 0.32.9
instalado pero detenido y con cinco manifiestos sin sus pesos (ningún modelo usable), y LLM Fit 1.1.10
instalado con scoop.

## 2. Qué se construyó

Una capa de inferencia local que el resto del producto consulta pero que no decide nada:

```text
negocio canónico × tarea ─ entrada mínima del almacén (fragmentos del texto ya parseado, hallazgos,
  subpuntuaciones, capturas) ─ caché ─ cola GPU (prioridad, mismo modelo seguido, capacidad 64)
  ─ circuit breaker ─ colocación en VRAM (estimación, descarga de otros modelos, contexto reducido,
  CPU si el perfil lo permite) ─ Ollama /api/chat con JSON schema, temperatura baja, semilla fija
  ─ comprobaciones (esquema, ids citados, citas literales, números, insufficient_evidence)
  ─ una reparación como máximo ─ respuestas crudas y validadas + run auditable
  ─ declaraciones latinas → reglas de la fase 07 (solo ellas deciden si son evidencia)
```

- **Paquete `ai/`** (Python): `tasks.py` (contratos de las 6 tareas, plantillas, saneado, detección de
  inyección, comprobaciones, clave de caché), `prompts/*.txt` (6 plantillas versionadas), `vram.py`
  (estimación y colocación), `resources.py` (RAM, VRAM y compuerta de presión para el crawler),
  `scheduler.py` (cola GPU y circuit breaker), `runner.py` (una inferencia de principio a fin),
  `inputs.py` (entradas desde el almacén), `store.py` (persistencia, lecturas, revisión, recuperación,
  benchmarks y jobs), `benchmark.py` (corpus sintético etiquetado), `llmfit.py` (opinión externa) y
  `engine.py` (servicio: estado, ajustes, jobs, benchmark, descargas).
- **`ollama.py`**: además de la detección, el protocolo `Provider` y `OllamaProvider` (chat con
  esquema, detalles, modelos cargados, descarga, borrado, descarga de VRAM).
- **Migración `0009_local_ai.sql`**: reconstruye `ai_inference_runs` y `ai_inference_outputs`, elimina
  `ai_inference_evidence` (las entradas viven en el run), añade `ai_profile_settings`,
  `ai_task_settings`, `ai_resource_limits`, `ai_benchmark_results` y cuatro enumeraciones.
- **Crawler**: espera antes de cada URL mientras la IA trabaja y falta memoria (120 s como máximo, sin
  consumir el presupuesto de tiempo del sitio). La URL de Ollama solo puede ser loopback (worker y
  configuración del core).
- **10 mensajes IPC nuevos** (`ai.*`), espejo Rust con validación, pantalla **AI Models**, panel
  **IA local** en el inspector de negocio y runs de IA en Jobs. `protocol_version` y
  `data_contract_version` siguen en 1: los mensajes son nuevos y las tablas reconstruidas nunca
  viajaron ni se escribieron.
- **Modelos descargados** (a petición del usuario, elegidos con LLM Fit, todos «Perfect» en GPU):
  `phi4-mini` 2,3 GB, `qwen3:4b` 2,3 GB, `qwen3:8b` 4,9 GB y `qwen2.5vl:3b` 3,0 GB (visión), Q4_K_M.

Referencia completa en [local-ai.md](../local-ai.md). Decisiones D-064 a D-069 en
[decisions.md](../decisions.md).

## 3. Requisitos del prompt y cómo se cumplen

| Requisito                                                                                                                       | Mecanismo                                                                                                                                                                                     |
| ------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| No convertir el LLM en orquestador ni reemplazar lo determinista                                                                | Los jobs deciden qué se pregunta; ninguna salida escribe hechos; solo las reglas latinas (fase 07) pueden convertir una cita propuesta en evidencia                                           |
| Funcionar sin IA y degradarse claramente                                                                                        | Sin runtime cada tarea dice su motivo (`runtime_unavailable`, `no_model_assigned`...), los jobs cuentan omitidas y terminan; la pantalla lo anuncia                                           |
| Ollama por defecto, abstracción para llama.cpp                                                                                  | `Provider` + `OllamaProvider`; llama.cpp server encaja en el mismo protocolo (no implementado)                                                                                                |
| LLM Fit como ayuda externa, sin cambios automáticos                                                                             | Solo a petición, `recommend --json --runtime llamacpp --no-dashboard`, mostrado como opinión                                                                                                  |
| Detectar runtimes/modelos; no descargar sin acción explícita                                                                    | Detección en cada estado; descargas y borrados solo con el nombre escrito dos veces, nunca sin conexión ni con el modelo en uso                                                               |
| Contratos por tarea con schema estricto, versión, límites, timeout y fallo                                                      | 6 tareas en `ai/tasks.py` con salida Pydantic (campos extra rechazados, vocabularios cerrados), versión, schema, plantilla, límites, timeout, temperatura y `repair_once_then_invalid_output` |
| Perfiles `fast`/`balanced`/`quality` configurables                                                                              | `ai_profile_settings` (modelo, contexto, CPU permitida) y `ai_task_settings` (activa, perfil), editables en AI Models                                                                         |
| Recomendar por benchmark, no por nombre                                                                                         | `ai.benchmark` sobre 15 casos etiquetados; recomendación = más aciertos con ≥ 80 % de respuestas válidas y mismo digest, luego más rápido; marcada como recomendación                         |
| No cargar varios modelos grandes en 8 GB; estimar VRAM                                                                          | Estimación pesos + KV f16 + sobrecarga; se descargan otros modelos si el nuevo no cabe junto a ellos; una inferencia a la vez                                                                 |
| Ajustar contexto/concurrencia; sin bucles de crash                                                                              | Contexto a la mitad hasta 2 048, CPU si el perfil lo permite o `insufficient_vram`; OOM: 2 pasos como máximo; breaker que pausa el job                                                        |
| Prompts versionados como plantillas                                                                                             | Ficheros `prompts/<tarea>.txt` con versión; hash de plantilla y de schema en la clave de caché; prueba que ata versiones a hashes                                                             |
| Entradas limpias y mínimas, JSON obligatorio, reparación limitada                                                               | `main_text` ya parseado en fragmentos, límites por tarea; `format` = JSON schema; una reparación                                                                                              |
| Temperatura baja, `evidence_ids` en cada claim, `insufficient_evidence`                                                         | 0 o 0,2 y semilla 42; listas de citas obligatorias; `insufficient_evidence` sin contenido                                                                                                     |
| Defensa contra prompt injection                                                                                                 | Contenido cercado en `<untrusted_data>`, `<`/`>` y caracteres invisibles neutralizados, marcas `suspected_prompt_injection`, sin herramientas, salida solo validada                           |
| Registro por inferencia                                                                                                         | Sección 5                                                                                                                                                                                     |
| Scheduler: cola, prioridad, cancelación, backpressure, batch, health, breaker, pausa del crawl, recuperación, eventos agregados | Sección 6                                                                                                                                                                                     |
| Seguridad                                                                                                                       | Loopback, nombres y parámetros validados en worker y core, sin comandos ni red desde el modelo                                                                                                |
| UI AI Models                                                                                                                    | Sección 7                                                                                                                                                                                     |
| Pruebas y benchmarks                                                                                                            | Secciones 9 y 10                                                                                                                                                                              |
| Configuración recomendada para RTX 4060 8 GB                                                                                    | [local-ai.md](../local-ai.md) sección 13                                                                                                                                                      |
| Sin score comercial                                                                                                             | `opportunity_reasoning` devuelve problemas, fortalezas y lo que falta; ningún campo puntúa                                                                                                    |

## 4. Tareas

| Tarea                     | Lee                                              | Responde                                                      | Timeout | Temp. |
| ------------------------- | ------------------------------------------------ | ------------------------------------------------------------- | ------- | ----- |
| `evidence_extraction`     | fragmentos de páginas `F`                        | claims con tipo, cita literal, fragmentos citados y confianza | 180 s   | 0     |
| `business_classification` | nombre, categorías, fragmentos `F`               | vertical (18), servicios y señales comerciales (10), citados  | 120 s   | 0     |
| `finding_synthesis`       | hallazgos `W` fallidos o con aviso               | resumen y puntos que citan hallazgos, sin cambiar números     | 180 s   | 0,2   |
| `opportunity_reasoning`   | hallazgos `W`, contactabilidad `C1`, latino `L1` | problemas (9), fortalezas y datos que faltan; sin score       | 240 s   | 0,2   |
| `language_detection`      | fragmentos `F`                                   | idioma por fragmento y traducción al inglés                   | 120 s   | 0     |
| `visual_review`           | capturas `S` (apagada; exige modelo con visión)  | observaciones citadas                                         | 240 s   | 0     |

## 5. Qué se registra por inferencia

| Pide el prompt                                | Columna                                                                                                        |
| --------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| tipo y versión de tarea                       | `task_type`, `task_version`                                                                                    |
| provider, runtime, modelo, digest, parámetros | `provider`, `model`, `model_digest`, `profile`, `parameters_json` (incluye contexto usado y reducciones)       |
| versiones de plantilla y de schema            | `prompt_template_id`, `prompt_template_version`, `output_schema_version`                                       |
| hashes de entrada y evidence IDs              | `input_sha256`, `input_json` (referencias con entidad, fuente, texto y sha256)                                 |
| salida cruda y validada separadas             | `ai_inference_outputs.raw_output` y `validated_output_json` o `validation_errors_json`, por llamada            |
| tokens, duración, dispositivo, errores        | `prompt_tokens`, `completion_tokens`, `duration_ms`, `device` (según `/api/ps`), `error_code`, `error_message` |
| revisión humana                               | `review_status` y `review_decisions` con motivo                                                                |
| clave de caché determinista                   | `cache_key`, única para un resultado válido no rechazado                                                       |

Los logs no llevan prompts, respuestas ni secretos.

## 6. Scheduler

| Requisito               | Cómo                                                                                                            |
| ----------------------- | --------------------------------------------------------------------------------------------------------------- |
| Cola independiente      | `GpuQueue`: una inferencia a la vez                                                                             |
| Prioridad               | persona (≤ 5 negocios) → job de proyecto → benchmark                                                            |
| Cancelación             | por dueño (job, benchmark); lo encolado no empieza, lo que corre queda `cancelled`                              |
| Backpressure            | capacidad 64: por encima se rechaza; los jobs esperan cada inferencia antes de pedir otra                       |
| Batch compatible        | tareas cortas del mismo modelo seguidas sin cambiar de modelo; nunca se mezclan en un prompt                    |
| Health checks y breaker | sondeo en cada estado; 3 fallos seguidos abren 30 s (hasta 5 min) y luego una llamada de prueba                 |
| Pausa del crawl         | con la cola ocupada y RAM < 1 024 MiB o VRAM < 512 MiB (configurable), cada URL espera ≤ 120 s                  |
| Recuperación            | al arrancar: encoladas `cancelled`, en curso `failed` (`worker_restarted`); al reanudar, la caché evita repetir |
| Eventos agregados       | checkpoint cada 10 unidades, un evento `batch_finished` cada 25; la UI sondea                                   |

## 7. UI conectada

| Pantalla                               | Qué hace con datos reales                                                                                                                                                                                                               |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **AI Models** (antes «no configurado») | Runtime y GPU con estado, RAM/VRAM y cola en vivo (cada 5 s, 2 s con benchmark o descarga), modelos instalados con tamaño, contexto, capacidades, estimación de VRAM y VRAM cargada; borrar y descargar con el nombre escrito dos veces |
| Perfiles y tareas                      | Modelo, contexto y CPU por perfil; activa y perfil por tarea con el motivo si no está disponible; límites de memoria                                                                                                                    |
| Benchmark y recomendaciones            | Recomendaciones marcadas como tales; benchmark manual (modelos y tareas), progreso y tabla de aciertos, válidas, p50/p95, tok/s, pico de VRAM, RAM mínima y dispositivo                                                                 |
| LLM Fit                                | A petición, como opinión externa                                                                                                                                                                                                        |
| Historial de inferencias               | Filtros por tarea, estado y revisión; detalle con aviso «no es un hecho», entradas citadas con su texto, respuestas crudas y validadas, errores, parámetros, hashes y revisión con motivo                                               |
| **Inspector de negocio ▸ IA local**    | Ejecuta tareas para ese negocio y lista sus inferencias con el mismo detalle                                                                                                                                                            |
| **Jobs**                               | Runs de IA con progreso, reutilizadas, omitidas y fallidas; cancelación                                                                                                                                                                 |

## 8. Archivos

**Nuevos (Python):** `ai/` (`__init__.py`, `tasks.py`, `vram.py`, `resources.py`, `scheduler.py`,
`runner.py`, `inputs.py`, `store.py`, `benchmark.py`, `llmfit.py`, `engine.py`, `prompts/*.txt`),
`migrations/sqlite/0009_local_ai.sql`, `tests/test_ai_tasks.py`, `tests/test_ai.py`,
`tests/test_ai_live.py`.

**Modificados (Python):** `domain.py` (enumeraciones, registros reconstruidos y nuevos, salidas de
tareas, contratos IPC), `contracts.py` (10 mensajes), `handlers.py`, `worker.py` (servicio, monitor
compartido con el crawler, errores del runtime), `settings.py` (loopback), `ollama.py` (provider),
`crawl/engine.py` (compuerta de presión, memoria compartida), `testdata.py` (`ScriptedProvider` y
`synthetic_answer`, solo pruebas y fixtures), `export_contracts.py` (13 fixtures),
`tests/test_latino.py`, `tests/test_ollama.py`.

**Rust:** `data.rs` (espejo con validación), `commands.rs` (10 comandos), `worker.rs`, `lib.rs`,
`config.rs` (loopback y su prueba), `tests/contracts.rs`, `tests/ipc.rs`.

**UI:** `features/ai/` (`AiModelsScreen.tsx`, `InferenceView.tsx`, `AiPanel.tsx`, `labels.ts`,
`AiModelsScreen.test.tsx`), `app/navigation.ts`, `app/ModuleRoute.tsx`,
`features/businesses/BusinessInspector.tsx`, `features/jobs/` (pantalla y prueba), `lib/ipc.ts`,
`lib/locales/{en,es}.ts`, `lib/contracts.test.ts`.

**Contratos:** `packages/shared-types` (esquema, `contracts.ts`, 13 fixtures nuevas).

**Docs:** `local-ai.md` (nuevo), `decisions.md` (D-064…D-069), `architecture.md`, `data-model.md`,
`operations.md`, `ui-system.md`, `README.md` y este reporte.

## 9. Comandos ejecutados y resultados exactos

| Comando                                                                                                           | Resultado                                                                                                                                                                                                                                                                                                                             |
| ----------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ollama serve` (el runtime estaba detenido) · `GET /api/tags`                                                     | Ollama 0.32.9 en marcha, **0 modelos**: los 5 manifiestos no tenían pesos (`total blobs: 0`); CUDA0 RTX 4060, 6,9 GiB libres                                                                                                                                                                                                          |
| `llmfit --max-context 8192 recommend --json --runtime llamacpp` (general, reasoning, multimodal y lista de 2 000) | con nombre de Ollama y ajuste «Perfect» en GPU: `phi4-mini` (39 tok/s estimados), `qwen3:4b` (37,2), `qwen3:8b` (29,2), `qwen2.5vl:3b` (39,8, visión)                                                                                                                                                                                 |
| `ollama pull phi4-mini qwen3:4b qwen3:8b qwen2.5vl:3b`                                                            | exit 0 en 11 min 19 s: 2,32 · 2,33 · 4,87 · 2,98 GiB, Q4_K_M; capacidades y metadatos leídos con `/api/show`                                                                                                                                                                                                                          |
| Prueba en vivo del esquema estricto (`qwen3:4b`, `phi4-mini`, GPU libre)                                          | JSON válido con `think: false`; `qwen3:4b`: inyección → `roofing` (44,4 s en frío, 24,9 s de carga), página vacía → `insufficient_evidence` (0,7 s), extracción (4,2 s); `phi4-mini`: respuesta con citas en `insufficient_evidence`, rechazada por las comprobaciones                                                                |
| `pnpm contracts` (dos exportaciones a directorios temporales)                                                     | 13 fixtures nuevas; las dos exportaciones idénticas (`diff -r`)                                                                                                                                                                                                                                                                       |
| `ruff check` · `ruff format` · `mypy`                                                                             | primeros intentos: 39 avisos en `src` (líneas largas, `# fmt: skip` mal colocados, `×` en docstrings, `assert`), 27 errores de mypy (lambdas, `InferenceRun` antiguo en `test_latino.py`) y 51 avisos en las pruebas (asserts compuestos). Final: sin avisos (93 archivos, mypy 92)                                                   |
| `pytest tests/test_ai_tasks.py`                                                                                   | primer intento: 3 fallos (esperaba 563 MiB de KV cache y son 576; umbral de `partial`; huella sin fijar). Final: **19 ✓**                                                                                                                                                                                                             |
| `pytest tests/test_ai.py` · `tests/test_ollama.py`                                                                | primeros intentos: expectativa de descarga de modelo mal calculada y prueba de reinicio que cancelaba en vez de simular un worker muerto (corregidas). Final: **20 ✓** · **19 ✓**                                                                                                                                                     |
| `pytest tests/test_ai_live.py` (Ollama real, GPU libre)                                                           | **4 ✓** en 48,6 s                                                                                                                                                                                                                                                                                                                     |
| `pytest` completo (antes del juego)                                                                               | **665 ✓, 1 omitida** en 377,9 s                                                                                                                                                                                                                                                                                                       |
| `tsc` · `oxlint`                                                                                                  | primeros intentos: listas acotadas exportadas como tuplas a TypeScript (6 errores, límites movidos a validadores) y `set-state-in-effect` ×3 (pasado a `useLoaded`). Final: exit 0 · exit 0                                                                                                                                           |
| `cargo fmt` · `cargo clippy --all-targets -D warnings` · `cargo test`                                             | primer intento: E0716 en la prueba de configuración (`env_of` exige `'static`). Final: exit 0 · exit 0 · **53 ✓** (22 unitarias + 19 contratos + 4 IPC + 8 worker)                                                                                                                                                                    |
| Primer `pnpm check`                                                                                               | exit 1: ruff en las pruebas nuevas (51 avisos). Corregido                                                                                                                                                                                                                                                                             |
| Segundo `pnpm check` (con un juego ocupando 5,9 GB de VRAM)                                                       | exit 1: `pytest` 664 ✓, 1 omitida, 1 fallo: la prueba en vivo agotó su timeout de 120 s con el modelo en CPU. La prueba ahora se omite con el motivo exacto cuando el modelo no cabe en la VRAM libre                                                                                                                                 |
| Tercer `pnpm check`                                                                                               | exit 1 en 402 s: contratos, prettier, lint y tipos en verde; `pytest` **662 ✓, 4 omitidas** (3 en vivo: «only 1661 MiB of VRAM free (another application holds the GPU); qwen3:4b needs about 3589 MiB at 4096 tokens», 1 de fuentes opt-in); `vitest` 12 fallos: faltaban mocks de IA en dos pruebas (corregido) y timeouts de 5 s   |
| `pnpm test:ts` (juego al 83 % de GPU)                                                                             | 9 fallos, todos timeouts de 5 s en pruebas que pasan por separado                                                                                                                                                                                                                                                                     |
| `vitest run --maxWorkers=2`                                                                                       | **178 ✓** en 27 archivos (85,9 s)                                                                                                                                                                                                                                                                                                     |
| `pnpm contracts:check` · `pnpm test:rust`                                                                         | exit 0 · **53 ✓**                                                                                                                                                                                                                                                                                                                     |
| `pnpm build:app`                                                                                                  | **exit 0** en 213 s: Vite build y `cargo build --release` (2 m 59 s) → `lead-intelligence.exe`                                                                                                                                                                                                                                        |
| `pnpm check` con la GPU libre (juego cerrado, 7 121 MiB libres)                                                   | exit 1 en 255 s: `pytest` **665 ✓, 1 omitida** (las pruebas en vivo corrieron); `vitest` 2 timeouts de 5 s (`LatinoPanel`, `ResearchCenterScreen`) en pruebas que pasan solas en 752 ms y 1 103 ms                                                                                                                                    |
| `pnpm test:ts`                                                                                                    | **178 ✓** en 40 s                                                                                                                                                                                                                                                                                                                     |
| `pnpm check`                                                                                                      | **exit 0** en 257 s: contratos, prettier, lint y tipos; `pytest` **665 ✓, 1 omitida** (fuentes opt-in); `vitest` **178 ✓**; `cargo test` **53 ✓**                                                                                                                                                                                     |
| `real_benchmark.py` (Ollama real, 4 modelos, 5 tareas, 15 casos)                                                  | exit 0 en 174 s, todo en GPU; resultados en la sección 11                                                                                                                                                                                                                                                                             |
| Revisión del estado que devolvió el benchmark                                                                     | defecto: `status` restaba la VRAM del propio modelo cargado y mostraba `qwen3:4b`, `qwen3:8b` y `qwen2.5vl:3b` en `cpu` mientras corrían al 100 % en GPU. Corregido en `AiService.status`; la prueba añadida a `test_status_reports_models_estimates_availability_and_the_queue` falla con la línea anterior y pasa con la corrección |
| `pnpm check` tras la corrección                                                                                   | **exit 0** en 227 s: `pytest` **665 ✓, 1 omitida**; `vitest` **178 ✓**; `cargo test` **53 ✓**                                                                                                                                                                                                                                         |

## 10. Pruebas obligatorias del prompt

| Caso                                                   | Prueba                                                                                                                                                                                                                                                                                                            |
| ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Provider falso solo en unit tests                      | `ScriptedProvider` en `testdata.py`; `test_the_worker_talks_to_the_real_runtime_only` comprueba que el worker usa `OllamaProvider`                                                                                                                                                                                |
| Ollama real cuando está disponible                     | `test_ai_live.py` (4 pruebas `local_services`, pasan con Ollama 0.32.9 y los modelos descargados) y `test_real_local_ollama_when_running`                                                                                                                                                                         |
| Ausencia de Ollama, GPU o modelo                       | `test_without_a_runtime_every_task_says_why_and_jobs_skip_instead_of_failing` (puerto cerrado real, sin GPU), `test_status_reports_models_estimates_availability_and_the_queue`, IPC contra el worker real en Rust                                                                                                |
| JSON inválido y reparación limitada                    | `test_invalid_answers_get_one_repair_and_never_loop` (exactamente 2 llamadas), `test_the_output_schema_is_strict`                                                                                                                                                                                                 |
| Timeout, cancelación y reinicio                        | `test_a_failing_runtime_opens_the_breaker_and_stops_being_called`, `test_a_failed_probe_doubles_the_cooldown`, `test_queued_and_running_work_can_be_cancelled`, `test_a_restart_closes_open_runs_and_never_duplicates_a_valid_result`, `test_a_project_job_runs_every_task_records_everything_and_resumes`        |
| Prompt injection en contenido fuente                   | `test_page_content_cannot_close_the_data_fence_or_hide_instructions`; caso `injection_roofing` del corpus, ejecutado contra `qwen3:4b` real                                                                                                                                                                       |
| `evidence_ids` inexistentes rechazados                 | `test_cited_ids_must_exist_and_be_of_the_task_kind`; en Rust `ai_payloads_that_claim_too_much_are_rejected` (cita `F99`); en TS `contracts.test.ts`                                                                                                                                                               |
| Caché e invalidación por versión                       | `test_cache_keys_change_with_every_version_and_input`, `test_cache_keys_change_when_a_template_changes`, `test_template_and_schema_hashes_are_pinned_to_their_versions`                                                                                                                                           |
| OOM simulado y degradación                             | `test_out_of_memory_reduces_the_context_then_runs_on_cpu`, `test_a_model_that_cannot_fit_is_refused_before_any_call`, `test_a_model_is_unloaded_only_when_the_next_does_not_fit_next_to_it`, `test_a_model_that_does_not_fit_gets_less_context_then_the_cpu`                                                      |
| Benchmark de precisión, latencia, throughput y memoria | `test_a_benchmark_measures_accuracy_latency_and_memory_and_recommends`, `test_recommendations_skip_models_that_mostly_failed_or_changed`; benchmark real en la sección 11                                                                                                                                         |
| Otros                                                  | cola (`test_the_queue_serves_priorities_keeps_a_loaded_model_and_refuses_beyond_capacity`), presión (`test_crawling_waits_for_memory_only_while_ai_works`), revisión, descargas con confirmación, loopback, LLM Fit, VRAM con metadatos reales de qwen3:8b, UI (`AiModelsScreen.test.tsx`, `JobsScreen.test.tsx`) |

## 11. Integración real y benchmark

El runtime real se usó de tres maneras: la prueba en vivo del esquema estricto (tabla anterior), las
pruebas `local_services` y el benchmark del producto (`real_benchmark.py` en el scratchpad de la
sesión, que llama a `AiService.benchmark` con Ollama real, los 4 modelos y las 5 tareas).

Benchmark ejecutado el 2026-09-13 con la GPU libre (Ollama 0.32.9, RTX 4060 8 GB, contexto 4 096,
temperatura y semilla del contrato de cada tarea). Los 4 modelos corrieron al 100 % en GPU. Cada
modelo tardó entre 34,2 y 55,7 s, carga incluida, y el total fue 174 s.

Aciertos por tarea (respuestas válidas entre paréntesis cuando difieren) y latencia p50:

| Tarea (casos)                 | `phi4-mini`           | `qwen3:4b`           | `qwen3:8b`         | `qwen2.5vl:3b`        |
| ----------------------------- | --------------------- | -------------------- | ------------------ | --------------------- |
| `business_classification` (6) | 5 (5 válidas) · 1,6 s | **6** · 2,0 s        | 5 · 3,7 s          | 5 · 1,5 s             |
| `evidence_extraction` (3)     | 2 · 1,9 s             | **3** · 4,1 s        | 2 · 2,5 s          | 0 (1 válida) · 3,1 s  |
| `finding_synthesis` (2)       | 0 (0 válidas) · 2,1 s | 1 (1 válida) · 1,1 s | **2** · 1,5 s      | 0 · 0,8 s             |
| `language_detection` (2)      | 1 · 1,2 s             | 1 · 1,3 s            | **2** · 1,9 s      | 0 · 1,0 s             |
| `opportunity_reasoning` (2)   | 0 (0 válidas) · 2,6 s | **1** · 1,1 s        | 1 · 2,2 s          | 0 (0 válidas) · 3,9 s |
| **Total**                     | 8/15 · 10 válidas     | 12/15 · 14 válidas   | 12/15 · 15 válidas | 5/15 · 11 válidas     |
| Tokens/s                      | 77–80                 | 78                   | 46                 | 99–103                |

- Recomendaciones del producto (el modelo más preciso con ≥ 80 % de respuestas válidas y, si empata, el
  más rápido): extracción `qwen3:4b`, clasificación `qwen3:4b`, razonamiento `qwen3:4b` (empate a 1/2
  con `qwen3:8b`, más rápido), síntesis `qwen3:8b` e idioma `qwen3:8b`.
- `phi4-mini` no dio ninguna respuesta válida en síntesis ni en razonamiento: las comprobaciones de
  citas y cifras las rechazaron todas. `qwen2.5vl:3b` es rápido pero no sirve para las tareas de texto
  (5/15); queda solo para `visual_review`.
- LLM Fit estimaba entre 29 y 40 tok/s; la GPU real dio el doble (46 a 103).
- Tamaños medidos por el runtime: `phi4-mini` 2 945 MiB, `qwen3:4b` 3 031 MiB, `qwen3:8b` 5 320 MiB y
  `qwen2.5vl:3b` 2 764 MiB. La fórmula de KV coincide exactamente (512 y 576 MiB). La estimación total
  sobrestima entre 558 y 1 098 MiB, lo que deja el margen del lado seguro.
- `qwen3:4b` y `phi4-mini` cupieron juntos: 7 178 MiB de VRAM usados en total, con el escritorio. Para
  `qwen3:8b` el runner descargó los dos antes de cargarlo, tal como se diseñó.
- El estado que se leyó tras cada modelo destapó un defecto de visualización ya corregido (sección 9):
  un modelo cargado aparecía estimado en `cpu`.
- El corpus es de 15 casos sintéticos, así que estas cifras ordenan modelos en esta máquina pero no los
  certifican. Antes, un juego había ocupado la GPU (5,9 GB de VRAM, 83 % de uso) y había mandado la
  inferencia a la CPU; por eso esta medición se hizo con el juego cerrado.

## 12. Riesgos y deuda pendiente

| Asunto                                                                                                                                                             | Cuándo importa                                         |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------ |
| **No verificado en la ventana real de la app.** La UI está probada con fixtures del servicio real y el flujo IPC contra el worker real, pero no se pulsó en Tauri. | Antes de cerrar la fase, si se quiere evidencia visual |
| Los modelos pequeños se equivocan; las comprobaciones atrapan respuestas inválidas, no todas las válidas pero erróneas.                                            | Siempre: revisar antes de confiar                      |
| El corpus es pequeño (15 casos) y sintético: ordena modelos, no los certifica.                                                                                     | Elegir entre modelos parecidos                         |
| La estimación de VRAM sobrestima entre 558 y 1 098 MiB y no considera atención híbrida ni flash attention.                                                         | Modelos al borde de los 8 GB                           |
| Una inferencia a la vez.                                                                                                                                           | GPUs grandes o muchos negocios                         |
| llama.cpp server solo como protocolo, sin implementación.                                                                                                          | Si no se quiere Ollama                                 |
| `visual_review` sin benchmark y sin capturas salvo que el crawl las tome.                                                                                          | Tareas visuales                                        |
| Las respuestas de clasificación, idioma, síntesis y razonamiento no alimentan ningún otro motor todavía (por diseño en esta fase).                                 | Fases 11 y 12                                          |
| Otras aplicaciones de GPU (un juego ocupó 5,9 GB durante la fase) mandan la inferencia a la CPU; los límites de memoria lo mitigan pero no lo evitan.              | Uso normal del escritorio                              |
| `ollama serve` quedó arrancado desde esta sesión; Ollama no se inicia solo con la app.                                                                             | Siguiente arranque del equipo                          |

## 13. Lo que esta fase NO hizo

Sin score comercial ni lead score (fase 11), sin ICP, sin exportaciones (13), sin empaquetado del
worker (14). Ninguna salida de un modelo se muestra como hecho, se exporta ni cambia un estado
determinista; la única vía hacia la evidencia son las reglas latinas de la fase 07.
