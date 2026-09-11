# PROMPT 06 — NORMALIZACIÓN, ENTITY RESOLUTION Y DEDUPLICACIÓN

Continúa sobre el mismo proyecto. Inspecciona el modelo de datos, registros crudos, procedencia, discovery y crawling. No elimines ni sobrescribas datos crudos y no conviertas heurísticas en hechos.

## Objetivo

Implementar un motor reproducible que transforme registros heterogéneos en negocios canónicos y detecte cuándo varias fuentes representan la misma empresa. Debe minimizar duplicados sin fusionar empresas distintas por coincidencias superficiales.

## Separación obligatoria

Mantén tres niveles:

```text
Raw source record      dato original e inmutable
Normalized observation valor normalizado con referencia al raw
Canonical business     entidad resuelta, reversible y auditable
```

Una normalización no modifica el valor original. Una fusión debe conservar todos los miembros del cluster y poder deshacerse.

## Normalización

Implementa funciones puras, versionadas y testeadas para:

- nombres comerciales: Unicode, casefold, puntuación, sufijos legales (`LLC`, `Inc.`, etc.) sin perder el nombre original;
- teléfonos: E.164 cuando exista país/contexto válido, extensiones separadas, tipo y validez estructural;
- emails: trim/case correcto, dominio IDN, rol vs persona, sin alterar local-parts de forma peligrosa;
- websites: esquema, `www`, trailing slash, parámetros de tracking, canonical domain, subdominio y registrable domain;
- direcciones de Estados Unidos: componentes, abreviaturas, ZIP/ZIP+4, suite/unidad y coordenadas si ya existen;
- categorías/servicios: vocabulario canónico versionado con aliases, sin perder etiquetas fuente;
- nombres de ciudad/estado/county/metro;
- redes sociales y handles;
- whitespace, HTML entities, mojibake y campos nulos.

No inventes valores faltantes. Distingue `missing`, `unknown`, `not_applicable` e `invalid` cuando el modelo lo requiera.

## Entity Resolution

Implementa estrategia por etapas:

1. **Exact blocking:** dominio, teléfono E.164, identificador estable de fuente.
2. **Candidate blocking:** ZIP/ciudad, tokens de nombre, calle, email domain y coordenadas cercanas.
3. **Features de similitud:** nombre, dirección, teléfono, dominio, categorías y geodistancia.
4. **Decisión:** `auto_merge`, `manual_review`, `distinct`.

La decisión debe ser explicable y guardar:

- features utilizadas;
- pesos/reglas/versiones;
- score de similitud;
- razones a favor/en contra;
- registros afectados;
- timestamp y actor;
- historial de merge/unmerge.

No uses apellido, idioma o supuesto origen étnico para deduplicar personas/empresas.

## Reglas de seguridad de datos

- Un teléfono compartido por varias sucursales no basta por sí solo para fusionar.
- Franquicias con el mismo dominio pueden ser establecimientos distintos.
- Negocios con nombres genéricos y dirección diferente no deben fusionarse automáticamente.
- Cambios de dirección/nombre pueden representar historia del mismo negocio; conserva temporalidad.
- Define entidad `organization` y `location` si el modelo actual necesita distinguir marca, empresa y sucursal.
- Los valores canónicos se seleccionan por políticas de confianza/frescura, no por “último gana”.

## Rendimiento

- Evita comparaciones O(n²) globales.
- Usa índices y blocking keys.
- Procesa en lotes con checkpoints.
- Mantén determinismo para la misma versión/configuración.
- Permite recalcular clusters cuando cambie el algoritmo sin destruir decisiones manuales.

## UI de revisión

Implementa una cola de posibles duplicados:

- comparación lado a lado;
- highlights de coincidencias/conflictos;
- evidencia y fuente por campo;
- acciones merge, keep separate, defer y undo;
- selección del valor canónico por campo;
- historial de decisiones;
- filtros por score/riesgo/fuente.

No ocultes conflictos después de fusionar.

## Pruebas obligatorias

Crea un corpus sintético con casos difíciles:

- mismo negocio con sufijos/nombres distintos;
- teléfono formateado de varias formas;
- dominios redirect/www;
- sucursales legítimas;
- franquicias;
- nombres genéricos iguales en estados diferentes;
- cambio de dirección;
- typo pequeño vs negocio distinto;
- datos incompletos;
- merge y unmerge;
- reejecución determinista;
- rendimiento sobre decenas de miles de candidatos sintéticos.

Mide precision/recall en el corpus etiquetado y define umbrales conservadores. Prefiere revisión manual ante ambigüedad.

## Entrega

Conecta resultados canónicos a `Businesses`, documenta algoritmo, features, thresholds, versión y flujo reversible. Ejecuta pruebas/builds y corrige fallos. No implementes todavía clasificación latina, IA comercial ni lead scoring.

