# Fase 15 — Revisión, QA y auditoría final integral

Fecha: 2026-09-15 · Veredicto: **CONDITIONALLY READY** (sección 1).

Auditoría sobre el worktree `fase-5-1a1090` con las fases 01–14 aplicadas. No añade funcionalidades:
cada cambio corrige un defecto demostrado y deja su prueba de regresión.

## 1. Veredicto

**CONDITIONALLY READY.** No queda ningún hallazgo `critical`. El único `high` abierto (A-09, binarios sin
firmar) solo se cierra con validación externa: un certificado de firma real, que no existe y no se inventa.
Build limpio y pruebas en verde (`pnpm check` exit 0), instalador real verificado en Windows 11, flujo
extremo a extremo aprobado con el worker real y la app de release, integridad y scoring recalculados sin
desviaciones, evidencia latina sin clasificaciones incorrectas y seguridad sin vulnerabilidades altas sin
mitigar. Los `medium` abiertos (feed de actualizaciones, support bundle, asistente de primera ejecución,
soak test) están documentados con su impacto y no rompen el flujo principal.

Para pasar a **READY**: firmar sidecar, app e instalador con un certificado real (packaging.md §7) y
repetir el checklist de la sección 15.

## 2. Entorno y toolchain

| Elemento    | Versión                                                           |
| ----------- | ----------------------------------------------------------------- |
| Sistema     | Windows 11 Home 26200 · Intel Core i5-13500 · 15,7 GiB · RTX 4060 |
| WebView2    | 152.0.4191.66                                                     |
| Node · pnpm | 22.20.0 · 11.20.0                                                 |
| Rust        | rustc/cargo 1.98.1 (MSVC) · tauri-cli 2.11.4                      |
| Python · uv | 3.12.13 · 0.5.9                                                   |
| Ollama      | 0.32.9 instalado en Windows, **no estaba en marcha** al empezar   |

## 3. Inventario y arquitectura

- **Capas:** React (apps/desktop) → Tauri/Rust (src-tauri: 18 módulos de comandos validados en
  `data.rs`) → worker Python por JSON Lines (services/research-engine) → SQLite (12 migraciones),
  DuckDB/Parquet (3 migraciones), artefactos direccionados por contenido; Ollama y Playwright/Edge
  opcionales; empaquetado NSIS con sidecar PyInstaller.
- **Las 14 fases existen y están conectadas:** cada módulo de la navegación tiene pantalla (ninguno
  queda «no configurado»), y el pipeline de la fase 13 recorre los engines de las fases 04–11 con el
  worker real (`test_a_pipeline_drives_the_real_engines_and_an_export_writes_its_file`).
- **Archivos grandes:** `src-tauri/src/data.rs` (10 507 líneas, espejo de contratos), `domain.py`
  (7 278, contratos Pydantic), `commands.rs` (2 394); son listas de tipos generadas a mano y
  verificadas por las pruebas de contratos, no lógica duplicada.
- **TODO/FIXME/HACK:** ninguno en código (`grep` sin coincidencias fuera de los prompts).
- **Secretos:** ninguno en archivos versionados (escaneo de patrones de la fase 14).
- **Documentación contra implementación:** se corrigieron textos que aún anunciaban trabajo futuro
  (exports en `scoring.md`/`lead-explorer.md`, bloqueo de instancia en `operations.md`, worker en
  `architecture.md`, módulos en `ui-system.md`).

## 4. Registro de hallazgos

| ID   | Severidad | Capa          | Hallazgo y evidencia                                                                                                                                                            | Estado y regresión                                                                                                   |
| ---- | --------- | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| A-01 | high      | datos         | `export_status pending → failed` existía en la migración 0012 y no en `domain.TRANSITIONS`: un export que fallaba antes de escribir violaba el modelo (`test_model.py` en rojo) | **Corregido** · `test_state_machines_match_the_transition_table`                                                     |
| A-02 | medium    | pruebas       | La regla de nulabilidad de columnas JSON no contemplaba listas (`exports.columns_json`)                                                                                         | **Corregido** · `test_every_record_matches_its_table_column_by_column[exports]`                                      |
| A-03 | high      | Windows/datos | Con la carpeta de datos a 157 caracteres, la semilla (y cualquier crawl) fallaba: la ruta temporal de un artefacto pasaba de 260                                                | **Corregido** (D-091) · Rust `a_data_folder_too_deep_for_windows_paths_is_refused_before_launch`; semilla verificada |
| A-04 | medium    | seguridad     | La ventana tenía `core:default` aunque solo usa `invoke` y eventos                                                                                                              | **Corregido** (D-090) · app instalada verificada escuchando el estado del worker                                     |
| A-05 | medium    | fiabilidad    | Dos instancias podían abrir los mismos almacenes (DuckDB bloqueado)                                                                                                             | **Corregido** (D-090) · QA de instalación: 1 proceso tras un segundo lanzamiento                                     |
| A-06 | medium    | dependencias  | `time` 0.3.45 (DoS) y `serde_with` 3.17 (pánico) con avisos                                                                                                                     | **Corregido** (D-092) · OSV sobre `Cargo.lock`                                                                       |
| A-07 | low       | pruebas UI    | `router.test.tsx` esperaba `/exports` «no configurado»                                                                                                                          | **Corregido** · prueba de Exports y Logs                                                                             |
| A-08 | low       | seguridad     | Sin prueba explícita de traversal para `data_path`                                                                                                                              | **Corregido** · `test_a_stored_path_never_leaves_the_data_directory` (6 casos)                                       |
| A-09 | high      | distribución  | Binarios e instalador sin firmar (no hay certificado)                                                                                                                           | **Abierto — validación externa** (packaging.md §7)                                                                   |
| A-10 | medium    | distribución  | Sin feed de actualizaciones firmado                                                                                                                                             | Abierto — requiere servidor                                                                                          |
| A-11 | medium    | operación     | Sin support bundle con redacción                                                                                                                                                | Abierto                                                                                                              |
| A-12 | medium    | UX            | Sin asistente de primera ejecución (carpeta, términos, demo en release)                                                                                                         | Abierto                                                                                                              |
| A-13 | medium    | rendimiento   | Soak test de horas no ejecutado                                                                                                                                                 | Abierto                                                                                                              |
| A-14 | low       | seguridad     | Comandos propios sin lista por comando en app manifest                                                                                                                          | Abierto (CSP + sin HTML renderizado)                                                                                 |
| A-15 | low       | dependencias  | `quick-xml` 0.38 (DoS, solo en build)                                                                                                                                           | Abierto hasta que `plist`/Tauri actualicen                                                                           |
| A-16 | low       | código muerto | `ModuleNotConfigured` y el estado `not_configured` ya no se usan en ningún módulo                                                                                               | Abierto (defensivo; borrar con su prueba)                                                                            |
| A-17 | low       | pruebas       | `the_crawl_flow_runs_end_to_end_through_ipc` falló una vez con la suite en paralelo y pasó sola                                                                                 | Abierto — vigilar                                                                                                    |
| A-18 | low       | rendimiento   | Bundle JS de 1,3 MB en un chunk, sin coste de arranque medido                                                                                                                   | Abierto (presupuesto en performance.md)                                                                              |
| A-19 | low       | UI            | Un clic en «Centro de investigación» no navegó durante el recorrido automático de la app real                                                                                   | **No reproducido** (5/5 navegaciones correctas); sin cambio                                                          |

## 5. Flujo extremo a extremo

| Paso del prompt                            | Verificación                                                                                                                                                                |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Campaña → descubrir → adquirir             | Pruebas de discovery y crawl con fuentes sintéticas y el guard SSRF; pipeline real con `no_campaign` omitido cuando no hay campaña                                          |
| Normalizar → deduplicar                    | `test_resolution.py`, `test_matching.py`; en la semilla, un par pendiente de revisión manual (`Synthetic Bakery 0200`/`LLC`, riesgo alto) y dos oficinas de una misma marca |
| Evidencia latina → website → contactos     | Suites de las fases 07–09 contra el worker real                                                                                                                             |
| IA local                                   | Ollama real con `qwen3:4b`: 31 pruebas en vivo y benchmark de 15 casos; sin Ollama, tareas omitidas con motivo                                                              |
| ICP / oportunidad / scoring                | `test_scoring.py`; recálculo independiente (sección 7)                                                                                                                      |
| Revisar lead → dataset → exportar          | `test_leads.py`, `test_dataset_snapshots.py`, `test_exports.py`, pipeline real que termina en snapshot y export                                                             |
| Reiniciar y comprobar persistencia         | `test_research_work_survives_a_worker_crash`; `test_a_restart_resumes_the_engine_job…`; app instalada reiniciada sobre el mismo almacén                                     |
| Pausa, cancelación, crash                  | `test_pause_resume_and_cancel_reach_the_engine_job_of_the_stage`, recuperación de exports interrumpidos                                                                     |
| Offline, sin GPU, sin Ollama, sin Chromium | Sin Ollama: verificado ahora (diagnóstico lo informa en ~2 s, tareas de IA omitidas con motivo); sin GPU/Chromium: pruebas de fases 05 y 10 con el fallback a Edge y a CPU  |

## 6. Integridad de datos

`scripts/integrity_check.py` sobre un almacén sembrado por el worker real (38 negocios):
integridad SQLite y claves foráneas limpias, **0** evaluaciones de propiedad sin evidencia enlazada,
**0** evidencias de otro negocio (salvo registros fusionados en él, que cuentan por diseño), **0**
leads puntuados sin componentes o con componentes sin valor, **0** jobs terminados con tareas o intentos
abiertos, **0** snapshots sin lineage, **0** exports escritos sin hash o recuento. Informativo: dos
dominios compartidos por negocios vivos, ambos intencionados en la semilla (un par en revisión manual
y una marca con oficinas en Austin y Denver, organización frente a ubicación).

## 7. Scoring

Recálculo independiente de los 68 scores guardados a partir de `component_scores`:
**0** totales con desviación > 0,01, **0** `OPPORTUNITY_SCORE` con desviación > 0,01, umbrales 70/50 y
peso conocido mínimo 60 % según la versión del modelo. Los 52 leads con tier `insufficient_data` y total
calculable tienen una puerta sin decidir (regla 2 de `scoring.md`), así que el tier es correcto. La
evidencia latina pesa 0 en el score final por defecto (aviso `latino_weighted` si se pondera).
Precision@K y calibración: no hay datos etiquetados reales; no se inventan métricas.

## 8. Latino evidence

Cubierto por `test_latino_rules.py` (16 pruebas) y `test_latino.py`, en verde en la corrida final:
apellidos, nombres, fotos y lugares nunca suben de `unknown`; español o mercado hispano solo da
afinidad; una autodeclaración sola no llega a `verified`; una certificación de confianza sí; negaciones
más recientes contradicen y se muestran; evidencia expirada o retirada deja de contar; la evidencia de
una organización compartida entre ubicaciones limita la propiedad; un modelo puede señalar una
declaración pero nunca decidirla; mismas reglas y evidencia dan el mismo resultado. En la semilla, la
evaluación con una negación de un registro fusionado queda en `unknown`.

## 9. IA local

- **Pruebas en vivo** contra Ollama 0.32.9 de Windows (arrancado para la auditoría y detenido después):
  `pytest tests/test_ai_live.py tests/test_ollama.py tests/test_gpu.py` → **31 ✓ en 14,7 s**, incluidos
  los casos de inyección de prompts; `qwen3:4b` cargado entero en la GPU (3,18 GB en VRAM).
- **Benchmark del corpus etiquetado** con el worker real (`ai.benchmark`, 15 casos, 36,4 s):

| Tarea                     | Casos | Salidas válidas | Correctas | p50 / p95 (ms) | Tokens/s |
| ------------------------- | ----- | --------------- | --------- | -------------- | -------- |
| `business_classification` | 6     | 6               | 6         | 1 959 / 2 564  | 79,1     |
| `evidence_extraction`     | 3     | 3               | 3         | 4 085 / 4 311  | 79,1     |
| `finding_synthesis`       | 2     | 1               | 1         | 1 029 / 4 984  | 78,3     |
| `language_detection`      | 2     | 2               | 1         | 1 266 / 1 893  | 79,1     |
| `opportunity_reasoning`   | 2     | 2               | 1         | 1 072 / 3 572  | 78,5     |

Total **12/15 correctas** (igual que en la fase 10), una salida inválida rechazada por el esquema,
VRAM pico 4,1 GiB, RAM libre mínima 3,96 GiB. Son mediciones de esta máquina, no estimaciones.

- **Sin Ollama:** al empezar la auditoría no corría; diagnóstico lo informa (≈ 2 s por el puerto cerrado)
  y las tareas de IA se omiten con motivo; las pruebas en vivo se omiten con explicación (6 en `pnpm check`).
- **Contratos:** esquema estricto por tarea, IDs de evidencia validados, `insufficient_evidence`, runtime,
  digest del modelo y versión del prompt registrados, cancelación y circuit breaker (fase 10). Ningún
  modelo tiene herramientas, red ni comandos; su respuesta nunca cambia hechos ni el score determinista.

## 10. Seguridad

Threat model y mitigaciones en [security.md](../security.md); SBOM, escaneos y triage en la fase 14.
Pruebas: SSRF/rebinding/redirects (fase 05), traversal (A-08), formula injection y límites de
Excel (fase 13), inyección de prompts (fase 10), destinos de export fuera de los almacenes, sin
puertos, permisos de ventana mínimos (A-04), operaciones destructivas con preview y motivo.

## 11. Rendimiento

[performance.md](../performance.md): worker listo a 1,75 s de lanzar la app instalada, reposo
0–1,35 % de un núcleo, 146 MiB core + worker, explorador de 1 M filas en 178 ms la primera ventana,
export de 50 038 filas con pico < 48 MiB.

## 12. UI y accesibilidad

Auditoría de la **app de release real** (no mocks): `lead-intelligence.exe` con su worker empaquetado y
un almacén sembrado por el worker (38 negocios), WebView2 con puerto de DevTools y Playwright por CDP:

- **16 módulos** abiertos desde la barra lateral: **0 errores de consola**, ninguna pantalla de error ni
  alerta, datos reales en cada uno (p. ej. Datasets con 1 dataset de 38 filas marcado como datos de
  prueba; Constructor de ICP con el perfil sintético activo v1; Explorador con 38 leads, tiers,
  «Parcial», «Datos de prueba» y conflictos en texto, no solo color). Interfaz lista a los 2,75 s.
- **Teclado:** las seis primeras paradas de Tab muestran anillo de foco.
- **Modo oscuro** aplicado al emular `prefers-color-scheme: dark`.
- **Idioma:** la app arrancó en español (`lang="es"`).
- Un clic sobre «Centro de investigación» no navegó en el recorrido (A-19); **no se reprodujo** en 5
  intentos (desde el explorador a 0,5, 3 y 6 s, desde Negocios y por `location.hash`): el clic se perdió
  durante la primera carga del explorador. La pantalla carga bien (formulario de rastreo, filtros y
  estado vacío honesto).
- Pruebas de componentes: 247 ✓ (estados loading/empty/error/offline, acciones destructivas con preview
  y motivo, navegación por teclado del grid).
- **No verificado:** 1440×900, 1920×1080 y escalado 125/150 % por separado (la ventana usa su tamaño
  por defecto de 1280×800).

## 13. Comandos y resultados

| Comando o prueba                                                   | Resultado                                                                                                                                                            |
| ------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pnpm check` (cierre de la fase 14, sin cambios de código después) | **exit 0 en 354 s**: contratos, prettier, oxlint, clippy `-D warnings`, ruff, `tsc`, mypy; `pytest` **758 ✓, 6 omitidas**; `vitest` **247 ✓**; `cargo test` **61 ✓** |
| `pnpm build` · QA de instalación                                   | exit 0 en 339 s · instalar, primer inicio, segunda instancia, cierre, reinicio y desinstalación en verde                                                             |
| `scripts/integrity_check.py`                                       | exit 0 (sección 6)                                                                                                                                                   |
| Recálculo de scores                                                | 68 scores, 0 desviaciones (sección 7)                                                                                                                                |
| IA en vivo · benchmark                                             | 31 ✓ · 12/15 (sección 9)                                                                                                                                             |
| Auditoría de UI por CDP · repro de navegación                      | 16 módulos, 0 errores · 5/5 navegaciones                                                                                                                             |
| `pnpm audit --prod` · `pip-audit` · OSV                            | Sin vulnerabilidades · sin vulnerabilidades · avisos triados (fase 14)                                                                                               |
| Escaneo de secretos · TODO/FIXME/HACK                              | Sin coincidencias · sin coincidencias                                                                                                                                |

## 14. Limitaciones de lo no ejecutado

- Firma de código y SmartScreen con descarga real (sin certificado).
- Actualización entre versiones instaladas (no existe versión anterior ni feed).
- Soak test de horas; escalado 125/150 % y resoluciones 1440×900/1920×1080 medidos por separado.
- Precision@K/lift con datos reales etiquetados.
- Fuentes de discovery en vivo (opt-in).

## 15. Checklist de release y rollback

1. `pnpm run setup` desde un checkout limpio con lockfiles.
2. `pnpm check` en verde.
3. `pnpm build` y `pnpm run build:sbom`; `pnpm audit --prod`, `pip-audit` y OSV sin avisos nuevos.
4. Firmar sidecar, app e instalador (packaging.md §7) y verificar con `signtool verify`.
5. Instalar en una máquina limpia: primer inicio, segundo lanzamiento, cierre, reinicio,
   desinstalación conservando datos (script de QA de la fase 14).
6. Publicar instalador, SBOM y hashes.

Rollback: cerrar la app, restaurar el backup previo a la migración desde `data/backups/`
(operations.md), reinstalar el instalador anterior (el nuevo impide downgrades sobre datos migrados,
por eso primero se restaura el backup y se desinstala).
