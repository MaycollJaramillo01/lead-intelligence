# PROMPT 08 — WEBSITE INTELLIGENCE ENGINE

Continúa sobre el mismo proyecto. Revisa páginas adquiridas, datos canónicos, procedencia, UI y jobs. No sustituyas el crawler y no permitas que un LLM invente hallazgos técnicos.

## Objetivo

Implementar un motor determinista que convierta el contenido y las mediciones de un sitio empresarial en un diagnóstico técnico/comercial reproducible. Debe identificar problemas y oportunidades concretas sin confundir ausencia detectable con certeza absoluta.

## Áreas de análisis

### Identidad y contenido

- title, meta description, canonical, favicon, Open Graph y social metadata;
- H1–H6, estructura, contenido principal y consistencia NAP;
- servicios, ubicaciones, áreas de servicio, horarios e idiomas declarados;
- páginas About, Contact, Services, FAQ, Privacy y Terms;
- freshness/señales de mantenimiento cuando sean comprobables.

### SEO local y técnico

- indexability básica, robots, sitemaps, canonicals, hreflang;
- status/redirect chains, enlaces rotos internos y duplicados básicos;
- schema.org/JSON-LD válido y tipos LocalBusiness/Organization/Service/FAQ;
- titles/descriptions faltantes, duplicados o extremos;
- consistencia de ubicación/servicio;
- readiness SEO local, AEO y GEO mediante reglas definidas y explicables.

### Conversión y UX heurística

- CTA principal y secundarios;
- teléfono clicable, formularios, chat, booking, cotización y mapas;
- formularios: campos, fricción, confirmación y señales técnicas; no enviar formularios;
- visibilidad de confianza: reviews/testimonios, licencias, garantías, casos, fotos reales;
- navegación, mobile viewport, legibilidad y jerarquía heurística;
- accesibilidad básica automatizable;
- claridad de propuesta, servicios y cobertura usando reglas, no gustos arbitrarios.

### Tecnología y medición

- CMS/framework/host detectables con evidencia;
- Google Analytics, Google Tag Manager, Meta Pixel y otros tags detectables;
- herramientas de booking/chat/forms;
- HTTPS, mixed content y headers observables;
- no afirmar “no usa Ads” solo porque no se detectó un pixel. Usa `not_detected`.

### Performance

- integra mediciones reproducibles mediante Lighthouse/PageSpeed local cuando sea viable;
- separa lab data de field data;
- guarda ambiente, timestamp, URL, device profile y versión;
- no inventa Core Web Vitals si no existen datos.

## Findings y scoring

Cada hallazgo debe tener:

```text
rule_id + rule_version
category
status: pass|fail|warning|unknown|not_applicable
severity
observed_value
expected_condition
evidence references
confidence
remediation
commercial_relevance
```

Crea subscores separados (`technical`, `seo`, `local_seo`, `content`, `conversion`, `mobile`, `accessibility`, `measurement`, `aeo_geo_readiness`). El score global, si existe, debe mostrar fórmula, cobertura y confianza. No penalices `unknown` como `fail`.

## Comparación y versiones

- Snapshot por auditoría y diff entre ejecuciones.
- Reglas versionadas para recalcular sin alterar auditorías históricas.
- Detecta cambios de dominio/redirect.
- Permite auditoría parcial si faltan páginas y muestra cobertura.

## UI

Construye una vista de website intelligence con:

- resumen, cobertura, fecha y versión;
- subscores y explicación;
- findings filtrables por severidad/categoría;
- evidencia por hallazgo;
- tecnología detectada con confianza;
- páginas adquiridas y errores;
- comparación histórica;
- opción de ejecutar/reintentar dentro de límites.

No reduzcas la vista a gauges; prioriza findings accionables.

## Pruebas obligatorias

Usa fixtures controlados para sitios:

- correcto y completo;
- sin CTA/formulario;
- SPA renderizada;
- schema inválido;
- redirects/mixed content;
- multi-location/bilingüe;
- tracking detectado/no detectable;
- auditoría parcial;
- reglas desconocidas/no aplicables;
- diff entre snapshots.

Valida determinismo, cobertura, performance razonable y no regresión del crawler. Ejecuta pruebas/builds y corrige fallos. Documenta cada regla y sus límites. No implementes todavía recomendación por IA ni score comercial final.

