# PROMPT 11 — ICP BUILDER, OPPORTUNITY ENGINE Y LEAD SCORING

Continúa sobre el proyecto existente. Inspecciona negocios canónicos, evidencia latina, website intelligence, contactos, IA local y versiones de datos. No cambies hechos ni uses el score para fabricar certeza.

## Objetivo

Construir el corazón comercial de Lead Intelligence: decidir qué empresas merecen investigación/contacto, por qué, con qué oferta y con qué nivel de confianza. La meta no es maximizar filas, sino producir una lista pequeña de oportunidades defendibles.

## Separación obligatoria de scores

Implementa componentes independientes de 0–100 o escala equivalente:

```text
ICP_MATCH
LATINO_CONFIDENCE
BUSINESS_HEALTH
DIGITAL_GAP
BUYING_CAPACITY
CONTACTABILITY
COMMERCIAL_INTENT
DATA_CONFIDENCE
OPPORTUNITY_SCORE
FINAL_LEAD_SCORE
```

No mezcles `LATINO_CONFIDENCE` con capacidad de pago ni calidad del negocio. Permite campañas que filtren por `verified_latino_owned`, por afinidad de mercado o sin este criterio.

## ICP Builder

Implementa perfiles versionados con:

- geografía y áreas excluidas;
- verticales/categorías/servicios;
- tamaño usando señales disponibles y sus límites;
- antigüedad/actividad/frescura;
- rating y reviews con fuente;
- madurez digital;
- tipos de problemas deseados;
- canales de contacto mínimos;
- estado de evidencia latina o afinidad requerida;
- presupuesto/capacidad mediante proxies explícitos, nunca afirmaciones financieras inventadas;
- must-have, nice-to-have y exclusion rules;
- pesos, gates y thresholds;
- oferta(s) del vendedor y capacidades que realmente puede entregar.

Incluye preview de cuántos negocios pasarían cada gate usando datos reales y una explicación de exclusiones.

## Opportunity Engine

Debe responder con evidencia:

1. ¿Qué problema observable existe?
2. ¿Qué impacto comercial probable tiene, marcado como inferencia?
3. ¿Qué servicio del catálogo podría resolverlo?
4. ¿Existe una razón para contactar ahora?
5. ¿Qué datos faltan y cuánto reducen la confianza?
6. ¿Hay contradicciones o riesgos?

Ejemplos de señales configurables, no pesos hardcodeados universales:

- sin website o website inaccesible;
- CTA/form/booking débil o ausente;
- SEO local/técnico deficiente;
- medición no detectada;
- buena actividad/reputación pero infraestructura digital débil;
- contacto útil y decision maker publicado;
- señales de crecimiento/frescura;
- evidencia latina relevante a la campaña.

Evita penalizar sectores donde booking, ecommerce o determinado tracking no aplica.

## Motor de scoring

- Fórmulas declarativas y versionadas.
- Gates duros antes de pesos cuando corresponda.
- Normalización clara de features.
- Tratamiento separado para `unknown`; no equivale a cero/fallo.
- Coverage y data confidence afectan la certeza, no necesariamente la oportunidad.
- Desglose por factor: valor, peso, contribución, evidencia y regla.
- Resultado determinista para reglas deterministas.
- IA local puede redactar/estructurar razones usando findings existentes, pero no alterar scores ni crear evidencia.
- Permite recalcular modelos y comparar versiones sin sobrescribir historia.

## Calibración por resultados

Diseña desde ahora, sin inventar resultados, un feedback loop para registrar:

```text
not_contacted
attempted
connected
qualified
meeting_booked
proposal_sent
won
lost + reason
invalid_contact
not_interested
```

- Los pesos iniciales deben declararse heurísticos.
- Añade métricas offline: precision@K, lift por decil, calibration, conversion por cohort y cobertura.
- No entrenes modelos predictivos hasta tener volumen/etiquetas suficientes.
- Separa fecha de snapshot para evitar leakage.

## Explainability

Para cada lead genera una `lead_scorecard` con:

- score total y versión;
- componentes y contribuciones;
- gates pasados/fallidos;
- top 3 razones positivas;
- riesgos/contradicciones;
- datos faltantes;
- evidencia enlazada;
- oferta recomendada y por qué;
- mensaje comercial sugerido solo como borrador, sin enviarlo.

## UI

- Constructor visual de ICP y scoring sin ocultar la fórmula.
- Versionar, clonar, comparar, activar y archivar modelos.
- Simulación sobre snapshot sin mutar leads.
- Distribución de scores y filtros reales.
- Inspector de score por lead.
- Revisión manual con motivo y audit trail.
- Evita gamificación o color sin explicación.

## Pruebas obligatorias

- gates/exclusiones;
- `unknown` vs `fail`;
- verticales con criterios no aplicables;
- fórmula y suma de contribuciones;
- determinismo/versionado;
- evidencia inexistente no puede puntuar;
- cambio de modelo no sobrescribe historia;
- campaña latina verified vs market affinity;
- ranking estable y desempate documentado;
- precision@K sobre fixture etiquetado;
- IA no puede alterar score ni inventar evidence IDs.

Ejecuta pruebas/builds, corrige fallos y documenta modelo inicial, supuestos y limitaciones. No implementes todavía envío de outreach.

