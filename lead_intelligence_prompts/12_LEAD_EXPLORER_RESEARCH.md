# PROMPT 12 — LEAD EXPLORER E INVESTIGACIÓN COMERCIAL

Continúa sobre la aplicación actual. Inspecciona todos los contratos, scores, evidencia, findings, contactos e historial. Esta fase integra la experiencia del analista; no reimplementa los engines.

## Objetivo

Construir una interfaz de investigación de alta densidad que permita encontrar, comparar, revisar y convertir negocios calificados en **sales-ready leads**. Cada decisión debe poder rastrearse a datos y evidencia.

## Lead Explorer

Implementa una tabla virtualizada capaz de trabajar con datasets grandes mediante consultas paginadas del backend. Columnas/configuración mínima:

- empresa y ubicación;
- vertical/servicios;
- lead stage;
- score total y componentes seleccionables;
- estado de evidencia latina y confianza;
- website/digital gap;
- business health/buying capacity como estimaciones etiquetadas;
- contactability y canal principal;
- principal oportunidad;
- freshness/data coverage;
- revisión/asignación/tags.

Incluye:

- sort multi-columna;
- filtros avanzados y grupos AND/OR;
- saved views versionadas;
- columnas visibles/reordenables/fijables;
- bulk actions seguras;
- selección persistente por ID, no índice;
- export preview antes de exportar;
- búsqueda rápida con debounce/cancelación;
- navegación por teclado.

## Inspector de negocio

El panel lateral y la vista completa deben contener:

1. Resumen canónico y fuentes.
2. Scorecard explicada y versión.
3. “Why this lead” con hechos separados de inferencias.
4. Evidencia latina/afinidad con estado exacto.
5. Website findings y snapshots.
6. Contactos, validación, rol y supresiones.
7. Línea temporal de fuentes, cambios y análisis.
8. Evidencia consultable por dato.
9. Conflictos/unknowns.
10. Oferta recomendada y razonamiento.
11. Notas y revisión humana auditada.

## Research Workspace

Implementa un flujo de investigación controlado:

- checklist configurable por vertical/campaña;
- solicitar refresh de páginas/contactos/evidencia;
- tareas pendientes por dato faltante;
- comparación de hasta 3–4 negocios;
- pin de evidencia importante;
- aceptar/rechazar outputs de IA sin borrar originales;
- marcar `sales_ready`, `needs_research`, `disqualified` con razón obligatoria;
- undo/historial donde corresponda.

No permitas editar silenciosamente datos de fuente; las correcciones crean overrides auditados.

## Sales Brief

Genera un brief estructurado por lead, basado solo en información existente:

```text
Business snapshot
Why it matches this ICP
Verified opportunity
Evidence
Recommended offer
Contact route
Decision maker, if evidenced
Conversation hooks
Risks/unknowns
Last verified date
```

El LLM local puede redactar el brief, pero todos los claims deben enlazar evidence IDs/findings y el sistema debe rechazar referencias inexistentes. No generes acusaciones ni lenguaje ofensivo sobre deficiencias del negocio.

## Funnel y feedback

Permite registrar manualmente el resultado comercial sin enviar mensajes:

- lead stage y timestamp;
- intento/canal;
- resultado;
- reunión/propuesta/won/lost;
- valor opcional;
- motivo de pérdida;
- calidad del contacto;
- notas.

Conecta estos datos al sistema de calibración sin modificar retroactivamente el snapshot usado para puntuar.

## Rendimiento y UX

- No cargar HTML/snapshots grandes al abrir la tabla.
- Prefetch limitado del siguiente registro.
- Cancelar consultas obsoletas.
- Mostrar stale/partial/offline claramente.
- Preservar selección/filtros al volver.
- Deep links internos a business, job, source, score version y dataset.
- Estados vacíos honestos y sin métricas simuladas.

## Pruebas obligatorias

- tabla con 100k–1M registros sintéticos sin cargar todo en memoria;
- combinación de filtros AND/OR;
- saved views y migración de esquema;
- selección al paginar/ordenar;
- datos stale/partial/conflict;
- supresión visible y respetada;
- brief con evidence IDs válidos y rechazo de alucinaciones;
- stage transitions y audit trail;
- keyboard navigation y accesibilidad;
- recuperación tras reiniciar.

Ejecuta pruebas, profiling, lint, typecheck y builds. Corrige fallos antes de cerrar. Entrega mapa de flujo del analista y criterios exactos para `sales_ready`. No implementes outreach automático.

