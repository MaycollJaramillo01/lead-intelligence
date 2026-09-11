# PROMPT 14 — PRODUCCIÓN, SEGURIDAD, RENDIMIENTO Y EMPAQUETADO WINDOWS

Continúa sobre el producto completo. Inspecciona antes de editar. Esta fase endurece y prepara para uso diario; no cambia el alcance ni añade módulos comerciales nuevos.

## Objetivo

Convertir Lead Intelligence en una aplicación desktop instalable, actualizable, recuperable y eficiente en Windows 11, incluyendo el sidecar Python y sus dependencias. Debe operar localmente, soportar sesiones prolongadas y fallar de forma segura.

## Empaquetado

- Empaqueta React/Tauri en modo production sin dev server.
- Compila el sidecar Python con Nuitka o PyInstaller tras una comparación práctica; documenta decisión.
- Incluye únicamente dependencias necesarias.
- Gestiona Playwright/Chromium de forma explícita: instalación, ruta, versión, ausencia y actualización.
- No incluyas Ollama/modelos dentro del instalador; detecta instalación externa y guía al usuario.
- Genera Tauri bundle/installer para Windows con versión semántica, iconos y metadata.
- Configura firma de código como proceso documentado; no inventes certificados ni desactives seguridad si no hay firma.
- Asegura que desinstalar no borre datasets del usuario sin opción/advertencia clara.
- Define rutas correctas para app data, cache, logs, exports y config.

## Updates y compatibilidad

- Diseña actualización firmada/segura usando mecanismo compatible con Tauri, pero no publiques endpoints inexistentes.
- Migraciones SQLite/DuckDB/data contracts antes de abrir datos nuevos.
- Backup y rollback ante migración fallida.
- Compatibilidad de proyectos/datasets creados por versiones previas.
- Mensajes claros si una versión es demasiado nueva/antigua.

## Seguridad

Realiza threat model de:

- contenido web no confiable y prompt injection;
- SSRF/DNS rebinding/redirects;
- path traversal y zip slip;
- command injection en Rust/Python/process args;
- Tauri permissions/capabilities;
- localhost APIs y puertos;
- secrets/API keys;
- logs y exports con datos personales empresariales;
- dependency/supply chain;
- archivos importados maliciosos;
- HTML/screenshots almacenados;
- modelos locales y respuestas no confiables.

Implementa mitigaciones reales:

- allowlist mínima de Tauri commands/capabilities;
- argumentos de procesos sin shell cuando sea posible;
- validación canónica de paths bajo directorios permitidos;
- secretos en credential manager del sistema o solución local segura, nunca plaintext/logs;
- permisos mínimos de archivos;
- red local enlazada a loopback con token efímero si aplica;
- límites de tamaño/tiempo/recursos;
- sanitización de contenido mostrado;
- SBOM y escaneo de dependencias.

No implementes telemetría externa por defecto. Si existe observabilidad, debe ser local y configurable.

## Rendimiento

Perfila, no adivines:

- tiempo de arranque;
- idle CPU/RAM;
- scrolling/filter de tabla;
- throughput HTTP/parser;
- browser concurrency;
- SQLite contention;
- DuckDB/Parquet queries;
- IPC payload/event rate;
- GPU inference/VRAM;
- tamaño del bundle;
- export grande.

Define budgets medibles y corrige hotspots confirmados. Usa streaming, paginación, batching, indexes y virtualización. No cargues datasets/HTML completos en IPC.

## Confiabilidad

- supervisor del worker y Chromium;
- shutdown limpio y recuperación después de crash/power loss;
- lock de instancia o multi-instance seguro;
- manejo de disco lleno, permisos, base bloqueada/corrupta, GPU/Ollama ausentes y red offline;
- health checks sin auto-restart infinito;
- logs rotativos y exportable support bundle con redacción de secretos;
- backups configurables y restore probado.

## UX de primera ejecución

- Detecta dependencias y explica estados reales.
- Permite elegir/confirmar directorio de datos.
- Muestra términos de adquisición responsable y límites.
- Configura Ollama/modelo opcionalmente; la app debe poder continuar sin IA.
- Incluye dataset/campaña de demostración sintética opcional, claramente identificada.

## QA requerido

- builds clean desde checkout nuevo con lockfiles;
- instalación, primer inicio, cierre, reinicio, upgrade y uninstall en Windows cuando el entorno lo permita;
- antivirus-friendly: procesos/rutas documentados;
- soak test de varias horas con fixtures locales;
- dataset sintético grande y export;
- crash/recovery;
- offline/no GPU/no Ollama/no Chromium;
- security tests para SSRF, traversal, injection y permisos;
- escaneo de secretos y dependencias;
- accesibilidad y resoluciones definidas.

Si el entorno actual no es Windows, prepara CI/build reproducible y distingue con honestidad lo compilado de lo no ejecutado. No afirmes que el instalador funciona en Windows sin probarlo allí.

## Entrega

Entrega artefactos/build instructions, threat model, SBOM, performance report, budgets, runbook de recuperación, archivos modificados, pruebas y resultados exactos. Corrige fallos críticos/altos antes de concluir. Deja el proyecto listo para el Prompt 15 de auditoría final.

