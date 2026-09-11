# PROMPT 07 — LATINO EVIDENCE ENGINE

Continúa sobre el proyecto actual. Inspecciona procedencia, businesses canónicos, sources y modelo de evidencia. No cambies la deduplicación ni mezcles identidad cultural con opportunity scoring.

## Objetivo

Implementar un sistema ético y auditable que distinga entre **propiedad latina explícitamente evidenciada** y **afinidad comercial con el mercado hispanohablante**. Cada resultado debe explicar qué señales lo sostienen y qué no puede concluirse.

## Estados obligatorios

Usa exactamente estos estados de negocio o una representación compatible y versionada:

```text
verified_latino_owned
high_confidence_latino_owned
probable_latino_owned
spanish_market_affinity
unknown
```

La UI debe mostrar etiquetas humanas claras, pero conservar los códigos estables.

## Prohibiciones absolutas

Nunca clasifiques una empresa como latina basándote únicamente en:

- apellido o nombre del dueño;
- nombre comercial;
- fotografía, apariencia o voz;
- ubicación en un vecindario;
- idioma español aislado;
- nacionalidad inferida;
- una predicción opaca de un LLM.

Estas señales, especialmente apellidos/fotografías, no deben usarse como sustituto de autodeclaración. `spanish_market_affinity` no significa `latino_owned`.

## Taxonomía de evidencia

Implementa evidencia positiva con fuente y fuerza configurables, por ejemplo:

- atributo autodeclarado `Latino-owned` de una plataforma/fuente confiable;
- declaración explícita en el sitio oficial o perfil oficial;
- certificación o registro de empresa Hispanic/Latino-owned;
- membresía verificable en cámara/directorio hispano cuyo criterio implique propiedad;
- entrevista o perfil público que declare propiedad, con fuente;
- idioma español/bilingüe, servicios a comunidad hispana o contenido social en español únicamente como afinidad de mercado.

Para cada evidencia almacena:

```text
evidence_id
business_id
claim_type
observed_value
source_type
source_url/source_record_id
page/field/context
captured_at
extractor_version
content_hash o snapshot reference
strength
freshness
status: active|stale|contradicted|revoked
```

Guarda fragmentos mínimos necesarios; no copies páginas completas sin justificación.

## Motor de evaluación

- Define reglas transparentes y versionadas, no una caja negra.
- Separa `evidence_strength`, `source_reliability`, `freshness` y `assessment_confidence`.
- Evidencia explícita fuerte puede producir `verified`.
- Varias señales independientes pueden producir `high_confidence` o `probable` según reglas documentadas.
- Solo señales de idioma/mercado producen `spanish_market_affinity`.
- Sin evidencia suficiente produce `unknown`, nunca una suposición.
- Evidencia contradictoria debe mostrarse y reducir/conflictuar el resultado, no descartarse silenciosamente.
- Permite revisión humana con decisión, motivo, fecha e historial; no borra el cálculo automático.
- Recalcula cuando cambien reglas o evidencia, conservando versiones anteriores.

## Uso de IA

En esta fase, si el runtime local ya existe, solo puede ayudar a extraer una posible declaración de texto adquirido. Toda salida debe tratarse como candidato y enlazarse al fragmento verificable. El modelo no puede adjudicar propiedad sin evidencia explícita ni navegar por sí solo.

## UI

Implementa en Business/Research:

- estado actual y confianza;
- separación visual de propiedad vs afinidad de mercado;
- lista de evidencias ordenada por fuerza/frescura;
- fuente y contexto consultable;
- contradicciones y advertencias;
- historial de evaluaciones;
- filtros por estado, confianza, fuente y revisión;
- acción de revisión manual con motivo obligatorio.

No uses banderas nacionales, estereotipos ni colores que conviertan probabilidad en certeza.

## Pruebas obligatorias

Construye fixtures etiquetados para:

- autodeclaración explícita;
- directorio/certificación confiable;
- web en español sin declaración de propiedad;
- nombre/apellido hispano sin evidencia;
- señales contradictorias;
- evidencia expirada o removida;
- múltiples ubicaciones con evidencia organizacional;
- extracción IA falsa o no soportada;
- revisión humana y recálculo versionado.

Prueba que apellido/nombre/foto por sí solos jamás eleven el estado sobre `unknown`. Prueba que español solo dé como máximo `spanish_market_affinity`.

## Entrega

Documenta matriz de evidencia, reglas, limitaciones y ejemplos. Conecta filtros a Businesses/Lead Explorer sin llamar “verificado” a lo que no lo es. Ejecuta tests/builds y corrige fallos. No implementes aún opportunity scoring.

