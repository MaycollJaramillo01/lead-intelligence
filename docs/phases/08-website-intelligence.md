# Fase 08 — Website intelligence engine

Fecha: 2026-09-13 · Estado: **completada y verificada** (`pnpm check` en verde y build de release).

## 1. Punto de partida

La fase parte del commit de la fase 07 (`d7638ac`), en el worktree `fase-7-finalizacion-f4e335`. Las
cuatro tablas de auditoría web (`website_audits`, `website_findings`, `website_technologies`,
`conversion_elements`) existían desde la fase 03 pero ningún motor las había escrito, y estaban
modeladas alrededor de `web_pages` y `evidence_items`, que el rastreador no llena para un sitio. El
snapshot que guarda el rastreador solo conserva lo que necesita la adquisición (título, meta,
encabezados hasta h3, enlaces, JSON-LD, texto principal), no formularios, scripts, imágenes ni
iframes.

## 2. Qué se construyó

Un motor **determinista** que convierte lo que el rastreador guardó de un sitio en un diagnóstico
técnico y comercial reproducible:

```text
último rastreo terminado de cada sitio ─ HTML guardado ─▶ hechos por página (extractor versionado)
          ─ snapshot (todo lo que leen las reglas) ─▶ 58 reglas versionadas en 9 categorías
          ─ auditoría inmutable: hallazgos con evidencia · tecnologías · elementos de conversión
            · subpuntuaciones con cobertura y confianza · diff con la auditoría anterior del sitio
```

- **Paquete `website/`** (Python): `extract.py` (hechos puros de una página: metadatos, encabezados,
  enlaces, teléfonos, direcciones, horarios, formularios, CTA, imágenes, contenido mixto, firmas de
  tecnologías, señales de confianza, datos estructurados), `rules.py` (catálogo, subpuntuaciones,
  auditoría y comparación puras), `store.py` (sitios rastreados, snapshots, auditorías, lecturas y
  runs) y `engine.py` (job reanudable).
- **Migración `0007_website_intelligence.sql`**: reconstruye las cuatro tablas alrededor de proyecto,
  sitio rastreado y rastreo leído; `snapshot_json`, `summary_json`, `diff_json`, `evidence_json`;
  tablas de consulta `website_category` y `website_audit_status`; triggers que impiden editar o borrar
  la historia.
- **5 mensajes IPC nuevos** (`website.start`, `website.control`, `website.runs`, `website.audits`,
  `website.audit`; aditivos: `protocol_version` y `data_contract_version` siguen en 1), espejo Rust
  con validación, vista **Website intelligence** en Research Center y runs en Jobs.

Referencia completa, regla por regla con sus límites, en
[website-intelligence.md](../website-intelligence.md). Decisiones D-052 a D-057 en
[decisions.md](../decisions.md).

## 3. Requisitos del prompt y cómo se cumplen

| Requisito                                            | Mecanismo                                                                                                                                                                                                                                                                                                                                                    |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| No sustituir el crawler                              | La auditoría lee lo que el rastreador guardó (filas, artefactos, payloads); `crawl/` no cambió una línea y una prueba end to end comprueba que auditar no toca sus tablas (D-052)                                                                                                                                                                            |
| Ningún LLM inventa hallazgos                         | Solo reglas deterministas sobre hechos extraídos; ningún modelo participa                                                                                                                                                                                                                                                                                    |
| Identidad y contenido                                | title, meta description, canonical, favicon, Open Graph, H1–H6 y su orden, palabras del contenido principal, páginas About/Contact/Services/FAQ/Privacy, idiomas, año de copyright como señal de mantenimiento                                                                                                                                               |
| SEO local y técnico                                  | indexabilidad (meta robots, X-Robots-Tag, robots.txt para Googlebot), robots.txt, sitemaps, canonical, hreflang, cadena de redirecciones, errores y enlaces rotos internos, titles/descriptions duplicados, JSON-LD válido y tipos LocalBusiness/Organization/Service/FAQ, teléfono, dirección o área, horario, mapa, ubicaciones, consistencia del teléfono |
| AEO y GEO con reglas explicables                     | contenido sin JavaScript, datos estructurados, preguntas y respuestas, perfiles enlazados, rastreadores de IA permitidos en robots.txt                                                                                                                                                                                                                       |
| Conversión y UX heurística                           | CTA principal y secundarios, tap-to-call, formularios (propósito, campos, obligatorios, etiquetas, acción segura; nunca se envían), booking, cotización, chat y mensajería, mapas, señales de confianza, navegación, viewport y zoom, accesibilidad básica                                                                                                   |
| Tecnología y medición                                | ~70 firmas (CMS, frameworks, analítica, tag managers, píxeles, call tracking, booking, chat, formularios, reseñas, mapas, captcha) con confianza por tipo de rastro; HTTPS, contenido mixto y cabeceras que el rastreador conserva                                                                                                                           |
| `not_detected`, nunca "no usa Ads"                   | La analítica no detectada es advertencia (o desconocido si hay tag manager); publicidad y call tracking son informativos y quedan desconocidos si no se detectan; ninguna regla de medición falla                                                                                                                                                            |
| Performance: Lighthouse/PageSpeed cuando sea viable  | No viable en el sidecar (Node + perfil de Chrome) y PageSpeed enviaría las URLs a un tercero: se documenta (D-055)                                                                                                                                                                                                                                           |
| Lab vs field; ambiente, fecha, URL, perfil, versión  | Cada página con tiempo de respuesta, render, tamaño, scripts e imágenes, herramienta y versión, perfil (`crawler_http`/`crawler_browser`) y fecha; notas fijas `field_data_unavailable` y `core_web_vitals_unavailable`                                                                                                                                      |
| No inventar Core Web Vitals                          | Ninguna regla los estima                                                                                                                                                                                                                                                                                                                                     |
| Campos de cada hallazgo                              | `rule_id` + `rule_version`, categoría, resultado (pass/fail/warning/unknown/not_applicable), severidad, valor observado, condición esperada, referencias de evidencia, confianza, remediación, relevancia comercial                                                                                                                                          |
| Nueve subpuntuaciones; global con fórmula o nada     | Nueve subpuntuaciones con fórmula guardada, cobertura y confianza; **sin puntuación global** (D-053)                                                                                                                                                                                                                                                         |
| No penalizar `unknown` como `fail`                   | `unknown` y `not_applicable` quedan fuera del cálculo; el core Rust rechaza un detalle cuyas subpuntuaciones no cuenten exactamente sus hallazgos                                                                                                                                                                                                            |
| Snapshot por auditoría y diff entre ejecuciones      | `snapshot_json` y `diff_json` por auditoría; historial por sitio (D-054)                                                                                                                                                                                                                                                                                     |
| Reglas versionadas sin alterar auditorías históricas | Una auditoría por sitio × rastreo × extractor × reglas; reglas nuevas recalculan desde el snapshot guardado en una auditoría nueva; triggers impiden editar o borrar                                                                                                                                                                                         |
| Cambios de dominio/redirect                          | Regla `technical.domain_redirect` y `domain_changed` en el diff                                                                                                                                                                                                                                                                                              |
| Auditoría parcial con cobertura                      | Estado `partial`/`empty`, páginas esperadas y analizadas, motivos (`gap`), reglas evaluadas/desconocidas/no aplicables, límites del rastreo                                                                                                                                                                                                                  |
| UI                                                   | Sección 7                                                                                                                                                                                                                                                                                                                                                    |
| No recomendación por IA ni score comercial final     | Nada de eso existe en esta fase                                                                                                                                                                                                                                                                                                                              |

## 4. Reglas y puntuación

| Categoría                                  | Reglas | Ejemplos                                                                   |
| ------------------------------------------ | -----: | -------------------------------------------------------------------------- |
| Técnica (`technical`)                      |      9 | HTTPS, contenido mixto, redirecciones, errores, enlaces rotos, compresión  |
| Buscadores (`seo`)                         |      9 | indexable, title, description, H1, canonical, sitemap, hreflang            |
| Búsqueda local (`local_seo`)               |      8 | teléfono, dirección o área, LocalBusiness, NAP, horario, mapa, ubicaciones |
| Contenido (`content`)                      |      8 | páginas de contacto/about/servicios/privacidad, contenido, frescura        |
| Conversión (`conversion`)                  |      9 | CTA principal, tap-to-call, formulario, fricción, confianza, navegación    |
| Móvil (`mobile`)                           |      2 | viewport, zoom                                                             |
| Accesibilidad (`accessibility`)            |      5 | lang, alt, etiquetas, enlaces con nombre, orden de encabezados             |
| Medición (`measurement`)                   |      3 | analítica, tag manager, publicidad y call tracking                         |
| Motores de respuesta (`aeo_geo_readiness`) |      5 | contenido sin JS, datos estructurados, FAQ, perfiles, rastreadores de IA   |

```text
subpuntuación = round(100 × Σ peso × crédito / Σ peso)   sobre las reglas evaluadas de la categoría
crédito = pass 1 · warning 0.5 · fail 0     peso = critical 10 · high 6 · medium 3 · low 1 · info 0
cobertura = peso evaluado / (evaluado + desconocido)      confianza = media ponderada de las evaluadas
```

## 5. Snapshot, versiones, comparación y cobertura

| Situación                                     | Efecto                                                                                                  |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Primer run sobre un rastreo                   | una auditoría por sitio con su snapshot, hallazgos, tecnologías, elementos y subpuntuaciones            |
| Otro run sin rastreo nuevo                    | nada se escribe (`unchanged`)                                                                           |
| Rastreo nuevo del sitio                       | auditoría nueva que apunta a la anterior y guarda qué cambió                                            |
| Reglas nuevas sobre el mismo rastreo          | auditoría nueva desde el snapshot guardado (aunque ya no existan los artefactos); la anterior no cambia |
| Páginas fallidas, en cola o sin HTML guardado | auditoría `partial` con su motivo por página; las reglas que las necesitan quedan `unknown`             |
| Ninguna página analizable                     | auditoría `empty`: sin subpuntuaciones                                                                  |
| El sitio termina en otro dominio              | advertencia `technical.domain_redirect` y `domain_changed` en la comparación                            |

## 6. Performance del sitio

Solo mediciones de laboratorio del propio rastreador, con herramienta, perfil y fecha; ni datos de
campo ni Core Web Vitals. Lighthouse y PageSpeed Insights no se integran (D-055).

## 7. UI conectada

| Pantalla                                   | Qué hace con datos reales                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Research Center ▸ Website intelligence** | Última auditoría de cada sitio rastreado (estado, páginas analizadas, cobertura, fallidas, advertencias, desconocidas, fechas); último run con pausa, reanudación y cancelación; botón para auditar todos los sitios rastreados.                                                                                                                                                                                                                                                                              |
| **Detalle de auditoría**                   | Resumen con estado y código, fecha, versiones de reglas y extractor, dirección final; hallazgos accionables primero, filtrables por resultado, categoría y severidad, cada uno con observado, esperado, qué hacer, por qué importa y evidencia; cobertura y límites; subpuntuaciones con conteos y fórmula; tecnologías con confianza; elementos de conversión; páginas leídas con mediciones de laboratorio y motivo si faltan; comparación con la auditoría anterior; historial; volver a auditar el sitio. |
| **Jobs**                                   | Auditorías junto a descubrimientos, rastreos, resoluciones y evaluaciones latinas, con cancelación a través del motor dueño.                                                                                                                                                                                                                                                                                                                                                                                  |

## 8. Archivos

**Nuevos (Python):** `website/` (`__init__.py`, `extract.py`, `rules.py`, `store.py`, `engine.py`),
`migrations/sqlite/0007_website_intelligence.sql`, `tests/test_website_rules.py`,
`tests/test_website.py`.

**Modificados (Python):** `domain.py` (enumeraciones `WebsiteCategory` y `WebsiteAuditStatus`,
registros reconstruidos y contratos de runs, auditorías, hallazgos, cobertura y comparación),
`contracts.py` (5 mensajes), `handlers.py`, `worker.py` (servicio, recuperación y apagado),
`testdata.py` (`insert_site_pages` con artefactos y robots.txt, `seed_website` con dos sitios
sintéticos y un segundo rastreo; la semilla de desarrollo guarda también los artefactos de los sitios
de la fase 07), `export_contracts.py` (7 fixtures).

**Rust:** `data.rs` (espejo con validación), `commands.rs` (5 comandos), `worker.rs`, `lib.rs`,
`tests/contracts.rs`, `tests/ipc.rs`.

**UI:** `features/website/` (`WebsiteIntelligenceView.tsx`, `WebsiteAuditPanel.tsx`, `labels.ts` y
prueba), `features/research/ResearchCenterScreen.tsx`, `features/jobs/` (pantalla y prueba),
`lib/ipc.ts`, `lib/locales/{en,es}.ts`, `lib/contracts.test.ts`, `app/router.test.tsx`.

**Contratos:** `packages/shared-types` (esquema, `contracts.ts`, 7 fixtures nuevas).

**Docs:** `website-intelligence.md` (nuevo), `decisions.md` (D-052…D-057), `architecture.md`,
`data-model.md`, `operations.md`, `README.md`.

## 9. Comandos ejecutados y resultados exactos

| Comando                                                                        | Resultado                                                                                                                                 |
| ------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `pnpm contracts`                                                               | exit 0: esquema, `contracts.ts` y 7 fixtures nuevas; ninguna fixture existente cambió                                                     |
| `ruff format` · `ruff check` · `mypy`                                          | sin avisos (71 archivos); el primer `ruff check` marcó líneas largas, `fmt: skip` inválidos y asserts compuestos (corregidos)             |
| `pytest tests/test_website_rules.py tests/test_website.py tests/test_model.py` | primer intento: 214 ✓ y 1 fallo (la prueba del zoom duplicaba el viewport; corregida la prueba)                                           |
| `tsc` (desktop)                                                                | primer intento: exit 2 (variante `secondary` inexistente en `Button`; corregido)                                                          |
| `oxlint` · `vitest` (website, jobs, research, contratos)                       | exit 0 · 21 ✓ en 4 archivos                                                                                                               |
| `cargo fmt` · `cargo clippy --all-targets -- -D warnings`                      | exit 0                                                                                                                                    |
| Primer `pnpm check`                                                            | exit 1 en 331 s: `router.test.tsx` no simulaba `listWebsiteRuns`, así que Jobs mostraba un error en vez de "No jobs have run" (corregido) |
| `pnpm check` (final)                                                           | **exit 0** en 411 s                                                                                                                       |
| `pnpm test:py`                                                                 | **558 ✓, 2 omitidas** en 234.3 s (prueba en vivo opt-in de fuentes; Ollama detenido)                                                      |
| `pnpm test:ts`                                                                 | **163 ✓** en 25 archivos                                                                                                                  |
| `pnpm test:rust`                                                               | **48 ✓** (21 unitarias + 15 contratos + 4 IPC + 8 worker)                                                                                 |
| `pnpm lint` / `pnpm typecheck`                                                 | ruff, oxlint, prettier, clippy, `tsc` y mypy `--strict` sin avisos                                                                        |
| `pnpm build:app`                                                               | **exit 0** en 158 s: Vite build y `cargo build --release` (2 m 12 s) → `lead-intelligence.exe`                                            |

## 10. Pruebas obligatorias del prompt

| Caso                              | Prueba                                                                                                                                                                                              |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Sitio correcto y completo         | `test_a_complete_site_passes_what_its_pages_show`; en el almacén, `synthetic-roofing-0601` queda `complete` sin fallos (`test_seeded_sites_are_audited_with_evidence_history_and_their_difference`) |
| Sin CTA/formulario                | `test_a_site_without_a_call_to_action_or_a_form`; en el almacén, el primer rastreo de `synthetic-cleaning-0602`                                                                                     |
| SPA renderizada                   | `test_a_rendered_single_page_app_is_judged_on_both_htmls`; end to end con Chromium real, `test_a_rendered_page_is_audited_from_its_rendered_html`                                                   |
| Schema inválido                   | `test_invalid_structured_data_is_reported_with_its_fragment`                                                                                                                                        |
| Redirects/mixed content           | `test_redirect_chains_domain_changes_and_mixed_content`; end to end, `test_a_crawled_site_is_audited_from_what_the_crawler_stored` (301, enlace 404, robots.txt que bloquea GPTBot, sitemap)        |
| Multi-location/bilingüe           | `test_multiple_locations_and_two_languages`; el sitio completo sintético tiene versión en español con hreflang                                                                                      |
| Tracking detectado/no detectable  | `test_tracking_is_detected_or_left_unknown_never_denied` (GA4, GTM, píxel, nada)                                                                                                                    |
| Auditoría parcial                 | `test_missing_pages_make_a_partial_audit_and_a_missing_home_an_empty_one`, `test_pages_without_stored_html_make_partial_and_empty_audits`                                                           |
| Reglas desconocidas/no aplicables | `test_unknown_and_not_applicable_never_lower_a_score`, `test_ai_crawlers_and_search_engines_read_robots_txt`; en Rust, `website_payloads_that_do_not_add_up_are_rejected`                           |
| Diff entre snapshots              | `test_the_difference_between_two_snapshots`, `test_newer_rules_recalculate_from_the_snapshot_without_touching_history`; en el almacén, el segundo rastreo de `synthetic-cleaning-0602`              |
| Determinismo                      | `test_audits_are_deterministic_and_reproducible_from_their_snapshot`                                                                                                                                |
| Performance razonable             | `test_performance_on_many_crawled_sites` y sección 11                                                                                                                                               |
| No regresión del crawler          | `crawl/` sin cambios; la suite `test_crawl.py` completa; la prueba end to end compara las tablas del rastreo antes y después de auditar                                                             |
| Jobs                              | `test_audit_jobs_are_controlled_and_recovered`; en Rust, `data_commands_serve_real_store_queries_through_ipc` audita por IPC contra el worker real                                                  |
| UI                                | `WebsiteIntelligenceView.test.tsx` (lista, detalle con evidencia y comparación, filtros, volver a auditar, error), `JobsScreen.test.tsx`, `contracts.test.ts`                                       |

## 11. Rendimiento

Medido en esta máquina con 300 sitios sintéticos (150 completos de 7 páginas y 150 de una página;
1 200 páginas con sus artefactos y robots.txt):

| Paso                                     | Resultado                                                                                               |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Inserción de los rastreos                | 8.7 s                                                                                                   |
| Primera auditoría                        | 59.3 s (198 ms por sitio): 300 auditados, 1 850 reglas fallidas, 2 750 advertencias, 1 500 desconocidas |
| Segunda auditoría sin rastreos nuevos    | **0.15 s** (300 sin cambios, nada escrito)                                                              |
| Página de últimas auditorías (50 de 300) | 20.0 ms                                                                                                 |
| Detalle de una auditoría                 | 5.8 ms                                                                                                  |
| Tamaño                                   | snapshot medio 11.2 KB por sitio; 17 400 hallazgos; base de datos de 26 MB incluidos los rastreos       |

El perfil de 30 sitios de 7 páginas atribuye el 70 % del tiempo a **abrir los artefactos recién
escritos** en `%TEMP%` (28 ms por archivo, consistente con el análisis antivirus en tiempo real). El
trabajo propio es de ~6 ms por página para los hechos y ~8 ms por sitio para las 58 reglas.

## 12. Riesgos y deuda pendiente

| Asunto                                                                                                                                                                                        | Cuándo importa                                         |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| **No verificado en la ventana real de la app.** La UI está probada con fixtures generadas por consultas reales y el flujo IPC contra el worker real, pero no se pulsó en la ventana de Tauri. | Antes de cerrar la fase, si se quiere evidencia visual |
| Títulos, condiciones y remediaciones de las reglas están solo en inglés (categorías, resultados y severidades sí se traducen).                                                                | Usuarios que trabajan solo en español                  |
| Sin Lighthouse ni datos de campo: los tiempos son una petición del rastreador por página.                                                                                                     | Diagnósticos de velocidad finos                        |
| Las firmas solo ven rastros en el HTML: etiquetas cargadas por un tag manager o un gestor de consentimiento son invisibles (por eso nunca se concluye ausencia).                              | Sitios con todo dentro de GTM                          |
| CTA, propósito de formularios, señales de confianza, horarios y área de servicio son patrones en inglés y español: habrá falsos positivos y negativos.                                        | Sitios con redacción poco común o en otros idiomas     |
| Direcciones y teléfonos con patrones de EE. UU.                                                                                                                                               | Negocios fuera de EE. UU.                              |
| Las auditorías pertenecen a sitios rastreados, no a negocios: aún no se muestran en el inspector de negocio.                                                                                  | Fases 11-12 (scoring y leads)                          |
| Las cabeceras de seguridad y el hosting no se juzgan porque el rastreador no los conserva.                                                                                                    | Auditorías de seguridad                                |
| La historia es inmutable y permanente: una política de retención necesitará una migración.                                                                                                    | Proyectos con muchos re-rastreos                       |
| Abrir artefactos en Windows con antivirus domina el tiempo de la primera auditoría.                                                                                                           | Miles de sitios en una sola ejecución                  |
| La lista de rastreadores de IA de robots.txt es fija.                                                                                                                                         | Cuando aparezcan agentes nuevos                        |
| La semilla de desarrollo añade dos sitios rastreados sintéticos y guarda los artefactos de los tres sitios de la fase 07.                                                                     | Solo desarrollo                                        |

## 13. Lo que esta fase NO hizo

Sin recomendaciones por IA ni runtime de IA local (10), sin validación de contactos (09), sin ICP ni
scoring comercial (11) y sin puntuación global de sitio. Ninguna pantalla llama «lead» a un negocio ni
usa una auditoría como puntuación de oportunidad.
