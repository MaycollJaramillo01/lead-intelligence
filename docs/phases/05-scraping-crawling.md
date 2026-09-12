# Fase 05 — Scraping, crawling y adquisición responsable

Fecha: 2026-09-12 · Estado: **completada y verificada** (`pnpm check` en verde, build de release y
una ejecución real contra internet).

## 1. Verificación previa de la fase 04

Con las dependencias instaladas desde los lockfiles se ejecutó `pnpm check` sobre el commit de la
fase 04 (`21b887b`): **exit 0**. No apareció ningún defecto heredado.

## 2. Qué se construyó

Un **motor de adquisición en dos niveles** que convierte candidatos y direcciones en páginas
adquiridas y contenido estructurado con evidencia reproducible, sin decidir nada comercial:

```text
Nivel 1  HTTP        httpx + selectolax        todas las URL
Nivel 2  Navegador   Playwright + Chromium     solo con un criterio verificable
```

- **Paquete `crawl/`** (Python): `urls.py` (URL canónica, alcance, tipo de página, prioridad,
  trampas), `robots.py` (RFC 9309), `guard.py` (SSRF), `fetch.py` (HTTP acotado), `extract.py`
  (charset, sniffing, DOM, sitemaps, hashes, criterios de fallback), `browser.py` (Playwright) y
  `engine.py` (planificación, scheduler, persistencia, control).
- **`jobs.py`**: el control de jobs (eventos, pausa, reanudación, cancelación, recuperación) salió de
  `discovery/engine.py` para que lo compartan los dos motores. Las 35 pruebas de discovery siguen
  en verde.
- **Migración `0004_web_acquisition.sql`**: tabla `crawl_urls` (frontera + registro de fetches),
  enumeraciones `crawl_url_status` y `crawl_target`, valor `business_website` en `source_kind`.
- **5 mensajes IPC nuevos** (aditivos: `protocol_version` y `data_contract_version` siguen en 1),
  espejo Rust con validación y **dos pantallas conectadas a datos reales**: Research Center y Jobs.

Referencia completa en [crawling.md](../crawling.md). Decisiones D-030 a D-035 en
[decisions.md](../decisions.md).

## 3. Políticas obligatorias y cómo se cumplen

| Política del prompt                       | Mecanismo                                                                                                                                                                                                                                                                |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| robots.txt                                | Primera petición de cada sitio; parser RFC 9309 (coincidencia más larga, `Allow` gana empates, `*` y `$`, grupo del token propio); 4xx = sin reglas, 5xx/429/red = nada permitido y el sitio se detiene; `Crawl-delay` respetado hasta 30 s. No es configurable (D-032). |
| No evadir CAPTCHA, paywalls, login, WAF   | Una página de desafío detiene el sitio con `blocked`; nunca se renderiza «para pasarla»; 401/403/407/451 no se reintentan.                                                                                                                                               |
| No automatizar áreas privadas             | Rutas de login, cuenta, carrito, checkout, admin… se omiten con `private_area`.                                                                                                                                                                                          |
| User-agent identificado                   | `LeadIntelligence/<versión> (local desktop research tool)` en HTTP y en el navegador.                                                                                                                                                                                    |
| Concurrencia por host                     | `HostGate`: slot por host, espaciado entre inicios, enfriamiento tras 429/5xx; además slots globales.                                                                                                                                                                    |
| Rate limiting y presupuesto configurables | `CrawlSettings`: peticiones/min por host, peticiones totales, páginas, bytes y segundos por sitio, profundidad, sitios.                                                                                                                                                  |
| Cache y peticiones condicionales          | `If-None-Match` / `If-Modified-Since` desde el último fetch HTTP de la misma URL en el proyecto; un 304 reutiliza el registro parseado (`not_modified`).                                                                                                                 |
| Tamaño máximo, MIME, archivos maliciosos  | 5 MiB por página contados tras descomprimir, tipo declarado comprobado antes de leer, sniffing de bytes (PDF/ZIP/EXE/imágenes rechazados aunque digan HTML), cuerpos guardados solo como artefactos direccionados por contenido.                                         |

## 4. Pipeline HTTP

URL canónica segura · resolución DNS con **protección SSRF en el propio `connect`** (loopback,
privadas, link-local/metadata, compartidas, reservadas, multicast, IPv4 mapeadas en IPv6 y nombres
locales rechazados; la conexión va a la dirección comprobada, así que el rebinding DNS no pasa;
proxies del entorno ignorados) · redirects a mano, máximo 5, auditados en `redirects_json` ·
timeouts separados (connect 5 s, read/write 15 s, pool 30 s) y 30 s por intento · reintentos solo
transitorios (timeouts, resets, 408, 429, 5xx) con backoff exponencial + jitter y nunca menos que
`Retry-After` · gzip/deflate con límite de bytes tras descomprimir · charset BOM → cabecera →
`<meta>` → UTF-8 → windows-1252 · extracción con selectolax (título, metadatos, headings, enlaces,
emails y teléfonos visibles, JSON-LD, texto principal) como **hallazgos crudos** · `body_sha256` y
`content_sha256` (texto visible normalizado) con deduplicación de páginas idénticas · persistencia
de estado, cabeceras permitidas, tiempos y procedencia.

## 5. Estrategia de crawling

robots.txt (prioridad 1000) → sitemaps declarados o `/sitemap.xml` (900, máx. 4 por sitio y 200 URL
cada uno) → home (100) → contacto, quiénes somos y servicios (90/80/70, en inglés y español) → el
resto (20), restando 10 por nivel de profundidad. Alcance por dominio (`www.` es el mismo sitio;
subdominios solo si se pide). Se evitan calendarios, facetas, parámetros de tracking y de sesión,
logout, carritos y binarios. Filtros include/exclude por glob. Toda URL decidida queda registrada
(hasta 400 por sitio) con su motivo; lo que un límite deja fuera se omite en una sola escritura con
un evento `limit_reached`.

## 6. Fallback Playwright

Solo por criterios verificables, medidos en la página HTTP y guardados en sus `signals`:
`empty_shell` (scripts y < 30 caracteres visibles, o punto de montaje de app vacío y < 200),
`js_required` (la página o su `<noscript>` lo dice), `http_functional_block` (406 o «navegador no
compatible») o `browser = always` (`configured`). Un desafío nunca es criterio (D-034).

Navegador reutilizado, contexto nuevo por render (sin cookies entre sitios), imágenes/medios/
fuentes/CSS/sockets bloqueados, solo http(s) a direcciones públicas, descargas rechazadas, espera por
señales (DOM, red inactiva, texto visible) con topes, captura opcional de pantalla, **razón y coste**
(`fallback_reason`, `browser_ms`) registrados, contextos cerrados en `finally` también al cancelar.
Un render que navega a otro sitio no se guarda como página del sitio.

**Procesos huérfanos**: antes del primer lanzamiento el worker se mete en un job object de Windows
con `KILL_ON_JOB_CLOSE`; Chromium y el driver de Playwright mueren con el worker aunque lo maten sin
aviso (D-035, sustituye la nota de D-007). Probado matando el proceso a la fuerza.

Navegador: el Chromium de Playwright si está instalado; si no, Edge (presente en Windows 11) o
Chrome. En esta máquina ya estaba el build `chromium-1234` que pide Playwright 1.62: no se descargó
nada.

## 7. Scheduler y recursos

Colas de prioridad `asyncio.PriorityQueue` por sitio con `per_host_concurrency` workers; semáforos
separados para HTTP y navegador (un render lento nunca retiene un slot HTTP). Slots según la
máquina: HTTP = `min(16, 2 × CPUs)`; navegador = hasta 2, uno por GiB libre por encima de 2 GiB y
máximo uno por cada 4 CPUs. Adaptación a la red: enfriamiento por host tras 429/5xx que decae con
respuestas normales. Pausa, reanudación y cancelación conservan la frontera en la base de datos;
la pausa y la cancelación cortan en el acto y cierran el navegador.

**Métricas**, agregadas de `crawl_urls` en cada lectura: peticiones, páginas obtenidas/fallidas,
URL omitidas y en cola, bytes, cache hits (304), reintentos, bloqueos, páginas HTTP y navegador,
fallbacks, páginas idénticas, páginas/min y latencia p50/p95/máx (solo tiempo de red); errores
agrupados por motivo con URL de ejemplo; URL en vuelo desde la memoria del worker.

## 8. UI conectada

| Pantalla            | Qué hace con datos reales                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Research Center** | Campaña o direcciones, límites editables dentro de los márgenes seguros, iniciar; por rastreo: progreso por sitios, presupuesto de peticiones usado y restante, páginas HTTP frente a navegador, cola, bytes, latencia, cache hits, reintentos, bloqueos, fallbacks, omitidas, idénticas, URL descargándose ahora, errores agrupados y sitios fallidos con su motivo; pausar/reanudar/cancelar. Tabla de URL (filtrable por estado) con motivo, método, fallback, HTTP, tamaño y título; inspector con cómo se obtuvo (método, razón y coste del fallback, redirects, tiempos), hallazgos crudos, hashes, artefacto y registro crudo. |
| **Jobs**            | Descubrimientos y rastreos juntos, más recientes primero, con progreso, ritmo y errores; cancelación a través del motor dueño del job. Consulta solo mientras algo se mueve.                                                                                                                                                                                                                                                                                                                                                                                                                                                          |

`navigation.ts` pasa Research Center a `available`. Ambas pantallas dicen en palabras que **las
páginas se adquieren, no se juzgan**.

## 9. Archivos

**Nuevos (Python):** `crawl/` (`urls.py`, `robots.py`, `guard.py`, `fetch.py`, `extract.py`,
`browser.py`, `engine.py`), `jobs.py`, `migrations/sqlite/0004_web_acquisition.sql`,
`tests/test_crawl.py` (motor contra un sitio de fixtures local), `tests/test_crawl_rules.py`.

**Modificados (Python):** `domain.py` (registro `CrawlUrl`, 3 enumeraciones y 13 contratos),
`repository.py` (`update`, lecturas de rastreos, URL y detalle con métricas), `contracts.py`,
`handlers.py`, `worker.py`, `discovery/engine.py` (usa `jobs.py`), `testdata.py` (`seed_crawl`),
`export_contracts.py` (6 fixtures nuevas), `pyproject.toml`/`uv.lock` (`selectolax`, `playwright`).

**Rust:** `data.rs` (espejo con validación de límites, totales y cadena de procedencia),
`commands.rs` (5 comandos), `worker.rs`, `lib.rs`, `tests/contracts.rs`, `tests/ipc.rs` (rastreo de
punta a punta contra el worker real).

**UI:** `features/research/` (pantalla, panel de rastreo, inspector, ajustes y motivos, pruebas),
`features/jobs/` (pantalla real y pruebas), `lib/ipc.ts`, `lib/locales/{en,es}.ts`,
`app/{navigation,ModuleRoute}.tsx`, `app/router.test.tsx`.

**Docs:** `crawling.md` (nuevo), `decisions.md` (D-030…D-035), `architecture.md`, `data-model.md`,
`operations.md`, `README.md`.

## 10. Comandos ejecutados y resultados exactos

| Comando                                                          | Resultado                                                                                 |
| ---------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `pnpm install --frozen-lockfile` · `uv --no-cache sync --locked` | exit 0                                                                                    |
| `pnpm check` (base, fase 04)                                     | **exit 0**                                                                                |
| `uv --no-cache add selectolax playwright`                        | selectolax 0.4.11, playwright 1.62.0 (Chromium `chromium-1234` ya presente: sin descarga) |
| `pnpm contracts`                                                 | esquema + 6 fixtures nuevas + `contracts.ts` regenerados                                  |
| `pnpm check` (final)                                             | **exit 0**                                                                                |
| `pnpm test:py`                                                   | **389 ✓, 2 omitidas** (prueba en vivo opt-in de USAspending; Ollama detenido)             |
| `pnpm test:ts`                                                   | **148 ✓** en 22 archivos                                                                  |
| `pnpm test:rust`                                                 | **42 ✓** (21 unitarias + 9 contratos + 4 IPC + 8 worker)                                  |
| `pnpm lint` / `pnpm typecheck`                                   | ruff, oxlint, prettier, clippy, tsc y mypy `--strict` sin avisos                          |
| `pnpm build:app`                                                 | **exit 0**: Vite build y `cargo build --release` (2 m 38 s) → `lead-intelligence.exe`     |
| Rastreo real (script desechable, almacén temporal)               | ver abajo                                                                                 |

### Ejecución real contra internet

Script desechable sobre un almacén temporal, con el `CrawlService` y el user-agent reales:
`https://example.com/` más dos objetivos que nunca deben tocarse.

```text
store sqlite ok sqlite 3.50.4 · schema v4 · query ok
run succeeded in 2.6s, sites 3/3
  site example.com: succeeded pages=1 requests=3 error=None
  site 127.0.0.1:8080: failed pages=0 requests=1 error=unsafe_target
  site 169.254.169.254: failed pages=0 requests=1 error=unsafe_target
  metrics requests=5 pages_fetched=1 urls_skipped=2 bytes=559 latency_p50_ms=203 latency_p95_ms=251
  failed   robots   https://example.com/robots.txt http=404 reason=not_found  (RFC 9309: sin reglas)
  fetched  page     https://example.com/ http=200 method=http_fetch title='Example Domain'
  failed   sitemap  https://example.com/sitemap.xml http=404 reason=not_found
  skipped  page     http://127.0.0.1:8080/ reason=unsafe_target
  skipped  page     http://169.254.169.254/ reason=unsafe_target
  home headers {content-encoding: gzip, last-modified: ..., server: cloudflare, ...}
  artifact artifacts/web/ff/ff67a9d7….html
```

El enlace a `iana.org` no se siguió (otro sitio) y ninguna petición salió hacia las direcciones
privadas.

## 11. Pruebas obligatorias del prompt

| Criterio                                   | Prueba                                                                                                                                                                                                                                                      |
| ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| HTML estático, charset extraño y JSON-LD   | `test_a_business_site_is_crawled_within_its_policies` (home gzip con JSON-LD, «Quiénes somos» en windows-1252), `test_charsets_are_detected_in_order`, `test_a_page_becomes_raw_findings_with_deterministic_hashes`                                         |
| Redirects y loops                          | `test_redirect_loops_chains_and_escapes_are_audited` (bucle, 6.º redirect sin petición, salida a otro sitio) y la cadena 301 auditada del sitio completo                                                                                                    |
| 429 con `Retry-After`, 5xx y timeout       | `test_transient_failures_are_retried_and_final_ones_are_not`, `test_retry_after_is_honoured`, `test_retry_after_in_both_forms`                                                                                                                              |
| Contenido demasiado grande / MIME inválido | `test_oversized_and_wrongly_typed_answers_are_refused` (declarado, gzip descomprimido, imagen, PDF servido como HTML), `test_binary_bodies_and_decompression_bombs_are_recognised`                                                                          |
| robots y reglas include/exclude            | `test_robots_follow_rfc_9309_longest_match`, `test_robots_fetch_outcomes_follow_rfc_9309`, `test_an_unreadable_robots_txt_stops_the_site`, `test_include_exclude_and_budgets`, `test_traps_private_areas_binaries_and_path_rules`                           |
| Sitemap index                              | Índice → hijo gzip → URL, sitemap de otro sitio ignorado; `test_sitemaps_are_read_without_expanding_entities` (billion laughs)                                                                                                                              |
| Sitio JS que exige Playwright              | `test_a_javascript_page_is_rendered_only_when_a_criterion_asks` (auto/never/always, email visible solo tras render, captura PNG)                                                                                                                            |
| SSRF y URL privadas                        | `test_the_guard_refuses_private_targets_before_any_request` (el servidor local no recibe ni una petición; rebinding a 127.0.0.1 rechazado en el connect), 14 casos de `test_private_local_and_metadata_targets_are_refused`, e IPC de punta a punta en Rust |
| Cancelación y limpieza de procesos         | `test_cancelling_a_render_closes_the_browser_and_leaves_no_process`, `test_chromium_dies_with_a_worker_that_is_killed_outright`                                                                                                                             |
| Reanudación sin duplicar contenido         | `test_a_paused_crawl_resumes_without_fetching_or_storing_twice` (worker nuevo; ninguna URL guardada se vuelve a pedir), `test_a_crawl_left_running_by_a_dead_worker_is_paused`, `test_unchanged_pages_are_answered_from_the_cache`                          |
| Determinismo de hashes                     | `test_a_page_becomes_raw_findings_with_deterministic_hashes` (mismos bytes → mismo snapshot; cambios solo de marcado → mismo `content_sha256`), artefacto = sha256 del cuerpo                                                                               |
| Bloqueos no evadidos                       | `test_a_blocked_site_is_not_worked_around` (403 y desafío «Just a moment…», sin render aunque `browser = always`)                                                                                                                                           |

## 12. Riesgos y deuda pendiente

| Asunto                                                                                                                                                                                | Cuándo importa                                                         |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| **No verificado en la ventana real de la app en esta sesión.** La UI está probada con las fixtures reales y el flujo IPC con el worker real, pero no se pulsó en la ventana de Tauri. | Antes de cerrar la fase, si se quiere evidencia visual como en 02 y 03 |
| El guard SSRF sustituye un atributo privado de httpx (`transport._pool._network_backend`). Las pruebas lo detectan si una actualización lo mueve (D-033).                             | Al actualizar httpx                                                    |
| El navegador comprueba la dirección de cada petición con su propia resolución DNS; Chromium resuelve de nuevo (ventana teórica de rebinding solo en el nivel 2).                      | Si se renderizan sitios hostiles a propósito                           |
| El presupuesto de peticiones se reserva antes de cada petición, pero reintentos y redirects en vuelo pueden excederlo por unas pocas.                                                 | Presupuestos muy pequeños                                              |
| El tiempo por sitio se mide por ejecución (se reinicia al reanudar).                                                                                                                  | Sitios pausados muchas veces                                           |
| Percentiles calculados en Python sobre todas las latencias del job (`ponytail:` en `repository.py`).                                                                                  | Rastreos de cientos de miles de URL                                    |
| La detección de desafíos y de «shell vacío» es por marcadores y umbrales: puede haber falsos negativos o positivos. Las señales quedan guardadas para revisarlo.                      | Sitios con protecciones nuevas                                         |
| `noindex` se registra pero no impide guardar la página (no es un buscador); `nofollow` sí se respeta.                                                                                 | Si el criterio legal cambia                                            |
| Una URL en vuelo al pausar se vuelve a pedir al reanudar (nada se guarda dos veces).                                                                                                  | Nunca en la práctica                                                   |
| Los contactos encontrados son hallazgos crudos sin validar ni normalizar.                                                                                                             | Fases 06 y 09                                                          |

## 13. Lo que esta fase NO hizo

Sin deduplicación ni resolución de entidad (fase 06), sin Latino Evidence (07), sin auditoría de
sitio (08), sin validación de contactos (09), sin IA local (10) y sin ICP ni scoring (11). Ninguna
pantalla puntúa, califica ni llama «lead» a nada de lo adquirido.
