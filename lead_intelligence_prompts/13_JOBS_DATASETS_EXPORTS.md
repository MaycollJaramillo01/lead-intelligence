# PROMPT 13 — ORQUESTACIÓN DE JOBS, DATASETS, EXPORTACIONES Y OPERACIÓN

Continúa sobre el proyecto existente. Inspecciona los engines, persistencia, UI y estados actuales. No crees un segundo scheduler paralelo ni exportes datos sin respetar supresiones y procedencia.

## Objetivo

Cerrar el flujo operacional de punta a punta: ejecutar campañas duraderas, controlar recursos, producir datasets reproducibles y exportar únicamente leads seleccionados con calidad verificable.

## Job Orchestrator

Implementa un DAG/pipeline explícito equivalente a:

```text
discovery
→ acquisition
→ normalization
→ entity resolution
→ latino evidence
→ website intelligence
→ contact intelligence
→ local AI tasks
→ ICP/opportunity/scoring
→ review/export eligibility
```

- Cada etapa define inputs, outputs, versión, dependencias y política de retry.
- Jobs/tasks usan estados válidos y transiciones atómicas.
- Checkpoints permiten reiniciar la app/equipo sin perder trabajo.
- Idempotencia por task key evita duplicados.
- Cancelación cooperativa y pause/resume reales.
- Retry solo para errores recuperables; errores de schema/configuración requieren intervención.
- Dead-letter/review queue para fallos persistentes.
- Prioridades y límites por recurso: HTTP, browser, CPU, RAM, disk, GPU.
- Backpressure entre etapas.
- No ejecutar dos escrituras incompatibles sobre el mismo dataset/business.

## Observabilidad de jobs

Registra y muestra:

- progreso total y por etapa;
- throughput, ETA como estimación y cobertura;
- queued/running/succeeded/failed/skipped/cancelled;
- retries y causa raíz;
- uso CPU/RAM/GPU/VRAM/disk y browsers activos;
- source budgets/rate limits;
- logs filtrables por job/task/business/request;
- eventos persistidos con retención configurable.

Evita emitir miles de eventos UI por segundo; agrega y limita frecuencia.

## Datasets y snapshots

- Cada dataset tiene schema version, query/campaign, filtros, fecha de corte, fuentes, pipeline versions y lineage.
- Snapshot inmutable para reproducibilidad.
- Parquet con tipos explícitos, particionado razonable y compresión.
- DuckDB registra metadatos y consulta sin cargar todo.
- Quality report por dataset: filas, duplicados, cobertura, unknowns, errores, freshness, distribución de estados/scores.
- Diff entre snapshots: añadidos, removidos, cambiados y razón.
- Retención y cleanup seguros con preview; nunca borrar fuentes necesarias para lineage.

## Exportaciones

Soporta:

- CSV UTF-8 con BOM opcional;
- XLSX;
- JSON/JSONL;
- Parquet.

Antes de exportar:

- mostrar cantidad y muestra real;
- aplicar filtros/saved view/snapshot exactos;
- excluir suppressed/DNC por defecto y bloquear inclusión accidental;
- marcar hipótesis/no validados;
- elegir columnas y nombres;
- incluir `exported_at`, dataset/score versions y freshness cuando corresponda;
- proteger contra CSV formula injection;
- sanear límites/formatos de Excel;
- escribir de forma streaming/por lotes y atómica (`.partial` → final);
- cancelar y limpiar archivos parciales;
- hash y manifest del export;
- historial de quién/qué/cuándo/configuración.

No envíes el archivo a servicios externos. El usuario selecciona la ruta local mediante diálogo seguro.

## Plantillas de exportación

Crea presets útiles:

- Sales-ready concise;
- Full evidence audit;
- Contact review;
- Website opportunity report;
- Dataset analytical.

Los presets se versionan y pueden clonarse; no deben esconder el significado de cada campo.

## UI

- Vista Jobs con jerarquía job → stages → tasks → attempts.
- Acciones pause/resume/cancel/retry permitidas por estado.
- Datasets con quality report, lineage y snapshot diff.
- Wizard de exportación con preview, advertencias y progreso.
- Logs vinculados desde cada error.
- Notificación al terminar sin asumir que el archivo se abrió.

## Pruebas obligatorias

- reinicio abrupto y resume;
- idempotencia y no duplicación;
- cancelación en HTTP/browser/GPU/export;
- retry classification;
- backpressure y límites de recursos;
- dataset snapshot/lineage/diff;
- CSV formula injection y caracteres Unicode;
- XLSX con límites/tipos;
- export grande sin OOM;
- suppressions excluidas;
- fallo de disco/espacio/permisos y cleanup `.partial`;
- manifest/hash reproducible donde aplique.

Ejecuta pruebas de extremo a extremo con fixtures sintéticos, lint, typecheck y builds. Corrige fallos y documenta runbook operativo. No agregues envío de emails, llamadas o automatización de outreach.

