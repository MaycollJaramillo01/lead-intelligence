# PROMPT 05 — SCRAPING, CRAWLING Y ADQUISICIÓN RESPONSABLE

Continúa sobre el proyecto existente. Revisa el Discovery Engine, jobs, persistencia, contratos y UI antes de editar. No reemplaces los adaptadores ni mezcles discovery con análisis comercial.

## Objetivo

Implementar un motor de adquisición en dos niveles:

```text
Nivel 1: HTTP rápido con httpx + selectolax
Nivel 2: Playwright async + Chromium solo cuando sea necesario
```

El motor debe convertir candidatos/URLs en páginas adquiridas y contenido estructurado con evidencia reproducible. No debe decidir todavía si una empresa es latina, si es buena oportunidad o qué venderle.

## Políticas obligatorias

- Respetar términos aplicables, robots.txt cuando corresponda, límites de fuente y configuración del usuario.
- No evadir CAPTCHA, paywalls, login, WAF ni controles de acceso.
- No automatizar áreas privadas sin autorización explícita.
- Identificar claramente el user-agent del producto cuando sea apropiado.
- Concurrencia por host, no solo global.
- Rate limiting y presupuesto de requests configurables.
- Cache y conditional requests (`ETag`, `Last-Modified`) cuando proceda.
- Tamaño máximo de respuesta, tipos MIME aceptados y defensa contra archivos maliciosos.

## Pipeline HTTP

Implementa:

- canonicalización segura de URL;
- resolución DNS y protección SSRF: bloquear localhost, metadata endpoints, redes privadas/reservadas salvo modo de prueba explícito;
- redirects limitados y auditados;
- timeouts separados de conexión/lectura/pool;
- retries solo para errores transitorios con backoff + jitter;
- descompresión y charset robustos;
- verificación MIME y límites de bytes;
- extracción DOM con selectolax;
- contenido principal, metadatos, headings, enlaces, emails/teléfonos visibles y JSON-LD como hallazgos crudos;
- hash de cuerpo/contenido normalizado y deduplicación de páginas idénticas;
- persistencia de status, headers permitidos, timings y procedencia.

## Estrategia de crawling

Para un sitio empresarial prioriza, con límites estrictos:

```text
/
/about | /about-us | equivalentes
/contact | equivalentes
/services | páginas de servicio
robots.txt
sitemap.xml y sitemaps hijos limitados
```

- Mantén scope por dominio y política de subdominios.
- Calcula prioridad por URL, profundidad y tipo de página.
- Evita calendarios infinitos, facetas, parámetros de tracking, logout, carts y recursos binarios irrelevantes.
- Permite filtros include/exclude y máximo de páginas/bytes/tiempo.
- Descubre URLs, pero no las sigue fuera del presupuesto.

## Fallback Playwright

Activa navegador únicamente mediante criterios verificables: shell HTML vacío, contenido requerido tras JS, bloqueo funcional del HTTP simple o configuración específica.

- Reutiliza browser/context de forma segura.
- Bloquea recursos innecesarios cuando no afecten el contenido.
- Usa tiempos máximos y espera basada en señales, no sleeps arbitrarios.
- Captura HTML renderizado y opcionalmente screenshot cuando un módulo posterior lo solicite.
- Registra la razón del fallback y su costo.
- Cierra páginas/contextos incluso ante cancelación.
- No ejecuta descargas ni permite navegación a esquemas peligrosos.

## Scheduler y recursos

- Usa `asyncio.Queue`/semaforos con backpressure.
- Ajusta concurrencia a CPU, RAM, red y límites por host.
- Separa colas HTTP y browser.
- Expone métricas: requests, páginas/min, bytes, cache hit, retries, bloqueos, browser fallbacks, errores por tipo y latencia percentil.
- Pause/resume/cancel deben conservar checkpoints y no dejar Chromium huérfano.

## UI

En `Research Center` y `Jobs` muestra información real:

- cola y URL actual sin saturar eventos;
- progreso, presupuesto consumido y restante;
- HTTP vs browser;
- errores agrupados;
- páginas y contenido adquirido;
- razón de cada fallback;
- cancelación segura.

## Pruebas obligatorias

Usa un servidor local de fixtures controlado para probar:

- HTML estático, charset extraño y JSON-LD;
- redirects y loops;
- 429 con `Retry-After`, 5xx y timeout;
- contenido demasiado grande/MIME inválido;
- robots y reglas include/exclude;
- sitemap index;
- sitio JS que exige Playwright;
- SSRF y URLs privadas bloqueadas;
- cancelación y limpieza de procesos;
- reanudación sin duplicar contenido;
- determinismo de hashes.

Ejecuta pruebas, lint, typecheck y builds reales. Corrige errores antes de finalizar. Documenta límites seguros, configuración y criterios de fallback. No implementes todavía deduplicación empresarial, Latino Evidence ni scoring.

