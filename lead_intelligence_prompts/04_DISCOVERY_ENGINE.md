# PROMPT 04 — DISCOVERY ENGINE Y ARQUITECTURA DE FUENTES

Continúa sobre la base existente. Inspecciona modelo de datos, contratos, jobs y pantallas. No reconstruyas módulos anteriores.

## Objetivo

Implementar un **Discovery Engine extensible** que genere candidatos de negocios a partir de fuentes legales y configurables. Esta fase descubre referencias; no debe convertirse todavía en un crawler profundo ni afirmar que un candidato es un lead calificado.

## Regla central

No construyas un scraper monolítico. Cada fuente debe ser un adaptador intercambiable bajo un contrato común, por ejemplo:

```python
class DiscoverySource(Protocol):
    metadata: SourceMetadata
    async def validate_config(self, config): ...
    async def estimate(self, query): ...
    async def discover(self, query, cursor, context): ...
    async def checkpoint(self): ...
```

La interfaz exacta puede mejorar, pero debe soportar capacidades, paginación, checkpoints, rate limits, términos de uso y errores estructurados.

## Fuentes y estrategia

Crea infraestructura para:

- APIs oficiales o datasets públicos;
- directorios empresariales y cámaras hispanas;
- registros públicos empresariales cuando su uso esté permitido;
- búsquedas web públicas;
- plataformas con API/autorización válida;
- archivos CSV/XLSX/JSON importados por el usuario;
- seeds manuales de URLs, dominios o nombres.

No implementes evasión de CAPTCHA, autenticación indebida, bypass de restricciones, rotación para eludir bloqueos ni scraping de áreas privadas. Si una fuente prohíbe automatización o requiere API, el conector debe bloquearse o exigir configuración válida.

## Campaign/Discovery Builder

Implementa un constructor real de campañas con:

- nombre y objetivo;
- geografía: país, estado, metro, ciudad, ZIP/radio si la fuente lo soporta;
- verticales, categorías, servicios y exclusiones;
- idiomas/afinidad de mercado como señales, no como prueba de propiedad;
- tamaños o proxies disponibles;
- fuentes seleccionadas y prioridad;
- límites por fuente, presupuesto de requests y fecha de frescura;
- estrategia broad → narrow;
- preview de consulta y estimación marcada como estimación;
- validación antes de iniciar.

Representa la consulta en un DSL/schema versionado, no solo como texto.

## Implementación inicial verificable

Incluye al menos:

1. conector de importación local CSV/JSON con mapeo de columnas y preview;
2. conector de seeds manuales;
3. un conector público estructurado que pueda probarse legalmente sin credenciales, o un fixture HTTP grabado si el entorno no permite red;
4. plantilla deshabilitada/configurable para fuentes que requieran API key, sin inventar credenciales;
5. registro de capacidades y salud de cada fuente.

## Comportamiento operacional

- Persistir query, configuración efectiva, versión del conector y resultados crudos.
- Emitir progreso por páginas/registros.
- Soportar pause, resume, cancel y reanudación desde checkpoint.
- Aplicar rate limit por host/fuente, jitter prudente, timeout y retries únicamente para fallos transitorios.
- Diferenciar `rate_limited`, `auth_required`, `blocked`, `invalid_query`, `schema_changed`, `network_error` y `source_unavailable`.
- Detectar candidatos repetidos de manera ligera sin resolver todavía la entidad final.
- Mantener provenance para cada candidato.
- No mezclar resultados de campañas sin lineage.

## UI

Conecta `Discovery`, `Campaigns` y `Sources` a datos reales:

- crear/editar/duplicar campaña;
- validar configuración;
- seleccionar fuentes y ver condiciones/capacidades;
- iniciar, pausar, reanudar y cancelar;
- ver progreso, candidatos, errores y fuente;
- abrir el registro crudo y su procedencia;
- no mostrar candidatos como leads finales.

## Pruebas y aceptación

- contrato común de adaptadores;
- conector CSV/JSON con columnas incompletas/malformadas;
- paginación y checkpoints;
- retries con backoff y `Retry-After`;
- cancelación cooperativa;
- recuperación al reiniciar la aplicación;
- resultados crudos inmutables y con lineage;
- límites globales y por fuente;
- no duplicar páginas al reanudar;
- UI conectada sin mocks engañosos.

Ejecuta pruebas, lint, typecheck y builds. Corrige fallos antes de concluir. Documenta cómo agregar un nuevo adaptador y no implementes aún crawling profundo, evaluación latina ni scoring.

