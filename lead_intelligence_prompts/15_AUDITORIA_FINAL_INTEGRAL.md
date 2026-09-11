# PROMPT 15 — REVISIÓN, QA Y AUDITORÍA FINAL INTEGRAL

Realiza una auditoría completa del proyecto **Lead Intelligence** ya construido. Este prompt no autoriza nuevas funcionalidades, rediseños arbitrarios ni cambios de stack. Su objetivo es encontrar, demostrar, priorizar y corregir defectos reales antes de considerar el producto terminado.

## Regla principal

No te limites a revisar superficialmente ni a decir que “todo está bien”. Inspecciona el repositorio completo, reconstruye el flujo real desde cero y ejecuta pruebas. Cada afirmación debe apoyarse en archivos, comandos, logs, mediciones o pruebas reproducibles.

No borres funcionalidades para hacer pasar pruebas. No cambies contratos públicos sin migración. No ocultes errores, warnings o tests fallidos. No uses mocks para afirmar que una integración real funciona.

## 1. Inventario y arquitectura

- Mapea frontend React, Tauri/Rust, Python sidecar, SQLite, DuckDB/Parquet, Ollama/llama.cpp, Playwright y empaquetado.
- Detecta código duplicado, capas cruzadas, dependencias circulares, archivos gigantes, módulos muertos y contratos inconsistentes.
- Verifica que las 14 fases anteriores existen realmente y están conectadas.
- Contrasta documentación con implementación.
- Busca TODO/FIXME/HACK, stubs, mocks de producción, hardcodes, secretos y rutas no portables.

## 2. Build reproducible

Desde un checkout/entorno limpio cuando sea viable:

- instala usando lockfiles;
- ejecuta format check, lint, typecheck, unit, integration y end-to-end;
- compila frontend, Rust y Python sidecar;
- crea el bundle/installer correspondiente;
- registra versiones de toolchain y comandos;
- investiga todos los warnings relevantes.

Si algo no puede ejecutarse por limitación del entorno, indícalo exactamente y no lo marques como aprobado.

## 3. Auditoría funcional end-to-end

Con fuentes/fixtures legales y datos sintéticos, verifica el flujo completo:

```text
crear campaña
→ descubrir candidatos
→ adquirir páginas
→ normalizar
→ deduplicar
→ evaluar evidencia latina
→ analizar website
→ descubrir/validar contactos
→ ejecutar IA local cuando exista
→ aplicar ICP/opportunity/scoring
→ revisar lead
→ crear dataset
→ exportar
→ reiniciar y comprobar persistencia/lineage
```

Prueba happy path, datos parciales, fuente caída, cancelación, pausa/reanudación, crash, offline, sin GPU, sin Ollama y sin Chromium.

## 4. Calidad e integridad de datos

- Raw data inmutable y normalizados trazables.
- Provenance completa campo → evidencia → fuente/snapshot.
- Merge/unmerge reversible y sin pérdida.
- No confundir organization/location/franchise.
- Timestamps, IDs, enums, constraints, indexes y migraciones.
- `unknown` diferente de `false/fail/zero`.
- Snapshots inmutables, schema versions y diffs.
- Export reproducible y manifest/hash.
- Suppressions/DNC respetadas en consultas y exportes.

Crea consultas de integridad para huérfanos, duplicados, referencias rotas, transiciones imposibles y scores sin evidencia.

## 5. Auditoría específica de Latino Evidence

Demuestra mediante pruebas que:

- apellido, nombre, foto, apariencia, vecindario o nombre comercial nunca prueban propiedad latina;
- español/bilingüe solo puede producir afinidad de mercado sin evidencia adicional;
- `verified_latino_owned` exige evidencia explícita/autodeclarada/certificada según reglas;
- contradicciones, expiración y fuentes débiles se muestran;
- cada evaluación conserva versión, evidencia e historial;
- los filtros no etiquetan probabilidades como certezas.

Trata cualquier incumplimiento como defecto crítico de integridad.

## 6. Auditoría de scoring

- Recalcula muestras manualmente y compara contribuciones.
- Verifica gates, pesos, thresholds, no aplicables y unknowns.
- Impide que evidencia inexistente puntúe.
- Separa afinidad latina de capacidad/health.
- Verifica versión, reproducibilidad y no sobrescritura histórica.
- Mide precision@K/lift/calibration únicamente con fixtures etiquetados o datos reales válidos; no inventes métricas.
- Revisa leakage temporal y sesgo por falta de datos.
- Confirma que IA no altera hechos ni score determinista.

## 7. Auditoría de IA local

- Prompt injection desde páginas y outputs malformados.
- Schemas, timeouts, cancelación, circuit breaker y cache invalidation.
- Evidence IDs válidos y `insufficient_evidence`.
- Runtime/model digest/prompt version registrados.
- Consumo VRAM/RAM, OOM y fallback.
- Comportamiento sin Ollama/GPU/modelo.
- Ninguna capacidad de ejecutar comandos o red arbitraria.

Benchmarkea corpus etiquetado y reporta precisión, fallos, latencia, throughput y memoria sin presentar estimaciones como mediciones.

## 8. Seguridad y privacidad

Ejecuta threat model y pruebas para:

- SSRF, DNS rebinding y redirects;
- path traversal/zip slip;
- command/SQL/HTML injection;
- Tauri permissions y superficies IPC;
- localhost auth/binding;
- importaciones maliciosas y CSV formula injection;
- secretos en repo/config/logs/build;
- permisos de archivos y support bundles;
- dependencias vulnerables/SBOM;
- contenido web no confiable;
- operaciones destructivas sin confirmación.

Corrige vulnerabilidades críticas y altas. Para medias/bajas, corrige o documenta mitigación, impacto y prioridad.

## 9. Rendimiento y estabilidad

Mide con datasets sintéticos representativos:

- startup e idle;
- 100k–1M filas en explorador mediante backend paginado;
- discovery/crawl/parser throughput;
- browser concurrency y cleanup;
- SQLite contention y DuckDB queries;
- IPC/event flooding;
- GPU inference;
- export grande sin OOM;
- soak test prolongado;
- crash/restart y power-loss recovery simulado.

Identifica regresiones y corrige hotspots comprobados, no micro-optimizaciones especulativas.

## 10. UI/UX y accesibilidad

- Flujos completos con teclado y focus visible.
- Dark/light, densidad, 1280×720, 1440×900, 1920×1080 y escalado 125/150 %.
- Loading/empty/error/offline/partial/stale/processing/success.
- Tablas, filtros, saved views, inspector y navegación conservan contexto.
- Acciones destructivas requieren alcance/confirmación adecuados.
- Scores y evidencias no dependen solo del color.
- Cero métricas/datos simulados presentados como reales.

## Proceso de corrección

1. Crea un registro de hallazgos con ID, severidad `critical/high/medium/low`, capa, evidencia, impacto, reproducción y corrección propuesta.
2. Corrige primero critical y high.
3. Después de cada grupo, ejecuta pruebas focalizadas.
4. Al final, vuelve a ejecutar el suite completo y build limpio.
5. Agrega una prueba de regresión por cada bug corregido cuando sea razonable.
6. No marques un hallazgo como resuelto sin verificación.

## Criterios de salida

El producto solo puede clasificarse:

- `READY`: cero critical/high abiertos, build y flujo E2E aprobados, integridad verificada.
- `CONDITIONALLY READY`: cero critical, high bloqueados únicamente por validación externa claramente documentada.
- `NOT READY`: cualquier critical abierto, pérdida/corrupción de datos, clasificación latina incorrecta, seguridad alta sin mitigar o flujo principal roto.

## Entrega final obligatoria

Entrega:

1. veredicto exacto y justificación;
2. matriz de hallazgos antes/después;
3. defectos corregidos y pruebas de regresión;
4. hallazgos abiertos con impacto y próximo paso;
5. comandos y resultados completos de QA/build;
6. cobertura funcional del flujo E2E;
7. reporte de integridad de datos;
8. reporte de seguridad y SBOM;
9. reporte de rendimiento con hardware/entorno;
10. limitaciones de pruebas no ejecutadas;
11. checklist de release y rollback.

No agregues funcionalidades nuevas salvo la mínima corrección necesaria para resolver un defecto demostrado. No declares el proyecto terminado si no satisface los criterios anteriores.

