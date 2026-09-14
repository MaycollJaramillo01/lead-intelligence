# Fase 11 — ICP Builder, Opportunity Engine y lead scoring

Fecha: 2026-09-14 · Estado: **completada**; cada etapa de `pnpm check` en verde (el `pnpm check` completo tuvo timeouts de vitest mientras un juego ocupaba la máquina, sección 7), build de release correcto · código en `main`.

## 1. Punto de partida

La fase parte de `main` con la fase 10 cerrada (`4313ca6`), en el worktree `fase-4-b49e7b`.

- **Tablas.** La fase 03 había reservado `icp_profiles`, `icp_profile_versions`, `scoring_models`,
  `scoring_model_versions`, `score_runs`, `business_scores`, `component_scores` y
  `score_explanations`, que nadie escribía, y la máquina de estados `lead_stage`.
- **Pantallas.** ICP Builder y Scoring Models decían «no configurado» y Lead Explorer estaba vacío.
- **Hechos.** Los aportan las fases anteriores:
  - negocios canónicos (06);
  - evidencia latina (07);
  - auditorías de sitios (08);
  - canales y contactabilidad (09);
  - respuestas de IA local (10).

## 2. Qué se construyó

Un motor que decide qué negocios merecen investigación o contacto, por qué, con qué oferta y con qué
certeza, sin cambiar hechos ni dejar que un modelo mueva un número:

```text
hechos del negocio canónico (almacén o hechos guardados de un run)
  ─ 24 features (conocida con evidencia, desconocida o no aplicable)
  ─ reglas por categoría y ofertas (no aplicable)
  ─ gates: no contactar, cerrado, exclusiones, requisito latino, problema observable, must-have
  ─ componentes 0–100 (lo desconocido queda fuera) ─ OPPORTUNITY_SCORE ─ FINAL_LEAD_SCORE ─ tier
  ─ razones, señales para contactar ahora, impactos inferidos, riesgos, datos faltantes,
    oferta recomendada, borrador de mensaje ─ scorecard y hechos guardados, inmutables
  ─ revisión humana y resultados del lead ─ calibración offline sin fuga
```

- **Paquete `scoring/`** (Python):
  - `model.py`: el motor puro, sin almacén, reloj, red ni modelo. Contiene features, criterios,
    gates, fórmulas, tier, razones, riesgos, faltantes, oferta, borrador, ranking y métricas.
  - `facts.py`: los hechos de cada negocio canónico, independientes del ICP y del modelo.
  - `store.py`: perfiles, modelos y versiones; simulación, runs, leads, scorecards, revisiones,
    resultados, comparación y calibración.
  - `engine.py`: el job reanudable.
- **Migración `0010_lead_scoring.sql`:**
  - reconstruye las versiones de modelos, `score_runs`, `business_scores` y `component_scores`;
  - añade versión activa y marca de archivo a perfiles y modelos;
  - elimina `score_explanations` y `explanation_kind`;
  - añade `lead_tier` y `lead_outcomes`;
  - versiones, runs, scores y resultados quedan inmutables y permanentes (triggers).
- **13 mensajes IPC `scoring.*`**, con espejo en Rust que valida:
  - que las contribuciones suman el total;
  - que el tier coincide con los gates;
  - que no hay afirmación sin evidencia;
  - que las razones de IA solo citan hallazgos del scorecard;
  - que las páginas respetan el orden y los filtros.

  `protocol_version` y `data_contract_version` siguen en 1: los mensajes son nuevos y las tablas
  reconstruidas nunca se escribieron ni viajaron.

- **UI:** pantallas ICP Builder y Scoring Models, Lead Explorer con leads reales, panel de lead score
  en el inspector y runs de scoring en Jobs.
- **Semilla de desarrollo:** un perfil ICP sintético (excluye NJ; ofertas `website_rebuild` y
  `local_seo`), un modelo sintético (contactabilidad primero) y dos runs. El segundo puntúa de nuevo
  los hechos guardados del primero. Incluye un resultado `attempted` y una confirmación. Siguen siendo
  38 negocios.

Referencia completa en [scoring.md](../scoring.md). Decisiones D-070 a D-075 en
[decisions.md](../decisions.md).

## 3. Requisitos del prompt y cómo se cumplen

| Requisito                                                                                                                                                                                                          | Mecanismo                                                                                                                                                                                                                                          |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Diez componentes independientes de 0 a 100                                                                                                                                                                         | `ScoreComponent`: cinco de base (salud, brecha digital, capacidad, intención, confianza de datos), ICP_MATCH, LATINO_CONFIDENCE, CONTACTABILITY, OPPORTUNITY_SCORE y FINAL_LEAD_SCORE; cada uno con valor, peso, contribución, cobertura y fórmula |
| No mezclar lo latino con capacidad o calidad; campañas verified, afinidad o sin criterio                                                                                                                           | LATINO_CONFIDENCE no es entrada de OPPORTUNITY_SCORE y pesa 0 en el final por defecto (aviso si se pondera); `gate.latino` con `none`, `market_affinity` o `verified_latino_owned`                                                                 |
| ICP versionado con geografía, categorías, tamaño, frescura, reputación, madurez, problemas, canales, requisito latino, capacidad por proxies, must-have/nice-to-have/exclusiones, pesos/gates/thresholds y ofertas | `IcpDefinition`: 9 criterios con importancia y peso, `latino`, `exclusions`, `offers` (qué resuelven, capacidades, categorías donde no aplican); thresholds y reglas en `ScoringDefinition`; versiones inmutables                                  |
| Preview de cuántos negocios pasan cada gate, con datos reales y explicación de exclusiones                                                                                                                         | `scoring.simulate` sobre el almacén (≤ 5 000 negocios) o los hechos guardados de un run: pasa, falla o indeciso por gate con hasta 5 ejemplos del motivo; no escribe nada                                                                          |
| Problema observable, impacto como inferencia, servicio, razón para contactar ahora, datos faltantes y su peso, contradicciones y riesgos                                                                           | Features con evidencia; `impacts` con `is_inference`; `offer` con el porqué; `contact_now`; `missing` con la parte de la fórmula final; `risks`                                                                                                    |
| Señales configurables y sin penalizar sectores donde no aplican                                                                                                                                                    | Peso por feature en cada versión; reglas `not_applicable` por categoría (booking en roofing, construcción…); `not_for_categories` en ofertas; un problema que ninguna oferta resuelve no se pondera                                                |
| Fórmulas declarativas y versionadas, gates antes de pesos, normalización clara                                                                                                                                     | `ScoringDefinition` versionada; la fórmula se muestra como texto en la UI y se guarda en cada componente; los gates se deciden primero                                                                                                             |
| `unknown` no equivale a cero                                                                                                                                                                                       | Medias sobre entradas conocidas, renormalizadas; `insufficient_data` con un gate indeciso o menos del 60 % del peso final conocido                                                                                                                 |
| Cobertura y confianza de datos afectan la certeza                                                                                                                                                                  | `confidence = DATA_CONFIDENCE / 100 × parte conocida`; cobertura por componente; no cambian la oportunidad                                                                                                                                         |
| Desglose con valor, peso, contribución, evidencia y regla                                                                                                                                                          | `ScoreComponentView`, `ScoreFeature`, `GateView`, `CriterionView` con evidencia (≤ 8) y detalle de la regla                                                                                                                                        |
| Resultado determinista                                                                                                                                                                                             | Motor puro; prueba de determinismo; runs sobre hechos guardados                                                                                                                                                                                    |
| La IA redacta sin alterar scores ni crear evidencia                                                                                                                                                                | `store.ai_reasons` solo conserva problemas cuyas citas son hallazgos del propio scorecard; los demás se descartan y cuentan; Rust rechaza razones con evidencia ajena                                                                              |
| Recalcular y comparar versiones sin sobrescribir historia                                                                                                                                                          | Versiones, runs, scores y componentes inmutables y permanentes; `facts_from_run_id`; `scoring.compare` (entran y salen del top K, cambio medio de posición, mismos hechos)                                                                         |
| Feedback loop con los diez estados y `lost` con motivo                                                                                                                                                             | `lead_outcomes` más la máquina `lead_stage`; `lost` exige motivo y solo `lost` lo lleva                                                                                                                                                            |
| Pesos iniciales heurísticos, métricas offline, sin entrenar, fecha de snapshot                                                                                                                                     | `is_heuristic`; precision@K (10/25/50/100), lift por decil, bandas de calibración, conversión por tier y cobertura; etiquetas solo de resultados posteriores a `facts_collected_at` (`leakage_excluded`)                                           |
| `lead_scorecard` completo                                                                                                                                                                                          | Total y versiones, componentes y contribuciones, gates, top 3 razones, riesgos, faltantes, evidencia enlazada, oferta y porqué, borrador de mensaje (`is_draft`)                                                                                   |
| UI sin ocultar la fórmula; versionar, clonar, comparar, activar y archivar; simulación sin mutar; distribución y filtros reales; inspector; revisión con motivo y auditoría; sin gamificación                      | Sección 5                                                                                                                                                                                                                                          |
| No implementar el envío de outreach                                                                                                                                                                                | El mensaje es un borrador; nada se envía                                                                                                                                                                                                           |

## 4. Modelo inicial (heurístico)

Todos los pesos son un juicio declarado, no ajustado a resultados:

| Parámetro                       | Valor                                                                                     |
| ------------------------------- | ----------------------------------------------------------------------------------------- |
| Features                        | 24: brecha digital 8, salud 5, capacidad 4, intención 3, confianza de datos 4 (pesos 1–3) |
| OPPORTUNITY_SCORE               | DIGITAL_GAP 45 · BUSINESS_HEALTH 20 · BUYING_CAPACITY 20 · COMMERCIAL_INTENT 15           |
| FINAL_LEAD_SCORE                | ICP_MATCH 35 · OPPORTUNITY_SCORE 40 · CONTACTABILITY 25 · LATINO_CONFIDENCE 0             |
| Tiers                           | priority ≥ 70 · review ≥ 50 · low; `insufficient_data` con < 60 % del peso final conocido |
| Subpuntuación débil             | < 60                                                                                      |
| Problema observable obligatorio | sí                                                                                        |
| No aplicable por categoría      | booking en roofing, construction, general contractor, wholesale, manufacturing            |
| Desempate                       | tier → total (sin total al final) → confianza → id del negocio                            |

## 5. UI conectada

| Pantalla                                              | Qué hace con datos reales                                                                                                                                                                                                                                                                                              |
| ----------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **ICP Builder** (antes «no configurado»)              | Perfiles con marca de datos de prueba; 9 criterios con importancia, peso y parámetros; requisito latino; exclusiones; ofertas; guardar versión o como nuevo; preview sobre el almacén o sobre los hechos guardados de un run; versiones para cargar, comparar campo a campo y activar; archivar y restaurar con motivo |
| **Scoring Models ▸ Modelos** (antes «no configurado») | Fórmula en texto, pesos finales y de oportunidad, umbrales, pesos de features con descripción, reglas por categoría, aviso de pesos heurísticos, versiones y archivo                                                                                                                                                   |
| **Scoring Models ▸ Runs**                             | Iniciar (hechos del almacén o de un run), pausar, reanudar, cancelar; tiers y distribución por run; comparar dos runs                                                                                                                                                                                                  |
| **Scoring Models ▸ Calibración**                      | Etiquetados, positivos, resultados excluidos por fuga, cobertura, precision@K, lift por decil, bandas y cohortes, con avisos                                                                                                                                                                                           |
| **Lead Explorer**                                     | Leads del último run terminado por posición, con tier, total, confianza, componentes, razón principal, oferta y etapa; filtros en el almacén (tier, problema, puntuación mínima, búsqueda); resumen y distribución del run                                                                                             |
| **Inspector de negocio ▸ Lead score**                 | Scorecard completo, historial de scores, redacción de IA conservada o descartada, resultados y revisiones; confirmar, descartar o retirar revisión y registrar resultados con motivo                                                                                                                                   |
| **Jobs**                                              | Runs de scoring con progreso, tiers y cancelación                                                                                                                                                                                                                                                                      |

## 6. Archivos

**Nuevos (Python):** `scoring/` (`__init__.py`, `model.py`, `facts.py`, `store.py`, `engine.py`),
`migrations/sqlite/0010_lead_scoring.sql`, `tests/test_scoring_model.py`, `tests/test_scoring.py`.

**Modificados (Python):**

- `domain.py`: `LeadTier`, registros reconstruidos y `LeadOutcome`, definiciones, hechos, scorecard y
  contratos IPC;
- `contracts.py`: 13 mensajes;
- `handlers.py` y `worker.py`: servicio, rutas y `ScoringError` → `invalid_payload`;
- `testdata.py`: `seed_scoring`;
- `export_contracts.py`: 21 fixtures.

**Rust:** `data.rs` (espejo con validación), `commands.rs` (13 comandos), `worker.rs`, `lib.rs`,
`tests/contracts.rs`, `tests/ipc.rs`.

**UI:**

- `features/scoring/`: `IcpBuilderScreen.tsx`, `ScoringModelsScreen.tsx`, `ScorecardPanel.tsx`,
  `parts.tsx`, `labels.ts`, `definitions.ts` y 3 pruebas;
- `features/businesses/`: `LeadExplorerScreen.tsx` y su prueba, `columns.tsx`,
  `BusinessInspector.tsx`, `BusinessesScreen.test.tsx`;
- `features/jobs/`: pantalla y prueba;
- `app/navigation.ts`, `app/ModuleRoute.tsx`, `app/router.test.tsx`;
- `lib/ipc.ts`, `lib/locales/{en,es}.ts`, `lib/contracts.test.ts`.

**Contratos:** `packages/shared-types` (esquema, `contracts.ts`, 21 fixtures nuevas).

**Docs:** `scoring.md` (nuevo), `decisions.md` (D-070…D-075), `architecture.md`, `data-model.md`,
`operations.md`, `ui-system.md`, `README.md` y este reporte.

## 7. Comandos ejecutados y resultados exactos

| Comando                                                                         | Resultado                                                                                                                                                                                                                                                                                                                                                                                |
| ------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Sonda de extremo a extremo sobre la semilla sintética (script en el scratchpad) | Semilla en 0,9 s; 38 negocios raíz, 4 con auditoría; hechos en 0,01 s; scorecards idénticos al repetir; run en 0,06 s; run sobre hechos guardados en 0,04 s; simulación: 8 review y 30 insufficient_data; unos 21 KB de scorecard y 9 KB de hechos por negocio                                                                                                                           |
| `ruff check` · `ruff format` · `mypy`                                           | Primeros intentos: 80 avisos en `model.py` (E501, RUF001 por `×`, F821) y errores de mypy (criterios tipados como `object`, variables reutilizadas entre ramas de `match`, `next()` sobre generador o tupla en `facts.py`, `ScoringError` no exportado). Final: sin avisos; mypy sin errores en 99 archivos                                                                              |
| `pytest tests/test_model.py` (tras la migración)                                | **210 ✓**                                                                                                                                                                                                                                                                                                                                                                                |
| `pytest tests/test_scoring_model.py tests/test_scoring.py`                      | Primeros intentos: el gate de problema observable quedaba indeciso cuando ninguna oferta resolvía los problemas vistos (ahora cuentan como evaluados y el gate falla); el CHECK de la migración impedía `insufficient_data` sin total (dividido en dos); esperaba 6 cambios auditados y son 5. Final: **20 ✓**; con `test_model.py`, **230 ✓** en 50,4 s                                 |
| `pnpm contracts`                                                                | 21 fixtures nuevas (106 en total), las existentes sin cambios; 2,5 s                                                                                                                                                                                                                                                                                                                     |
| `cargo fmt` · `cargo clippy --all-targets -D warnings` · `cargo test`           | exit 0 · exit 0 (1 min 34 s) · **55 ✓** (22 unitarias + 21 contratos, entre ellas `scoring_fixtures_round_trip_and_pass_validation` y `scoring_payloads_that_claim_too_much_are_rejected`, + 4 IPC con el flujo de scoring + 8 worker)                                                                                                                                                   |
| `pnpm test:py` (con un juego usando la GPU)                                     | **682 ✓, 4 omitidas** en 330 s: 3 pruebas de IA en vivo («only 519 MiB of VRAM free (another application holds the GPU)») y 1 de fuentes opt-in                                                                                                                                                                                                                                          |
| `pnpm contracts:check` (en segundo plano tras pytest)                           | exit 1: «stale contract files: schema/contracts.schema.json», porque `domain.py` cambió mientras corría pytest (fila siguiente). `pnpm contracts` y `pnpm contracts:check`: exit 0                                                                                                                                                                                                       |
| `tsc -b`                                                                        | Primer intento: 2 errores, `offers` exportado a TypeScript como unión de tuplas por `max_length=20` en Pydantic (el mismo caso de la fase 10). Límite movido al validador `_between(0, MAX_OFFERS)`. Final: exit 0                                                                                                                                                                       |
| `oxlint --deny-warnings`                                                        | Primeros intentos: `no-conditional-expect` en `ScorecardPanel.test.tsx`. Final: exit 0                                                                                                                                                                                                                                                                                                   |
| `vitest run` (juego en marcha: GPU 90 %, CPU 71 %)                              | exit 1: 8 fallos. Uno real («Do-not-contact list» aparece en la etiqueta y en el detalle del gate) y 7 timeouts de 5 s en pruebas que escriben con `user.type` (3 nuevas, 4 de fases anteriores). Aserción corregida; las pruebas nuevas usan `user.paste`                                                                                                                               |
| `vitest run --maxWorkers=2`                                                     | **191 ✓** en 31 archivos (109 s)                                                                                                                                                                                                                                                                                                                                                         |
| `pnpm check` (juego en marcha)                                                  | exit 1 en 504 s: contratos, prettier, oxlint, clippy, ruff y tipos en verde; `pytest` **682 ✓, 4 omitidas** (3 IA en vivo por VRAM ocupada, 1 fuentes opt-in); `vitest` 182 ✓ y 9 fallos por carga (8 timeouts de 5 s y un `findBy` de 1 s agotado en `AiModelsScreen`), en pruebas que pasan con `--maxWorkers=2`; `cargo test` no llegó a correr porque la cadena se corta en `vitest` |
| `pnpm test:rust`                                                                | exit 0 en 48 s: **55 ✓** (22 unitarias + 21 contratos + 4 IPC + 8 worker)                                                                                                                                                                                                                                                                                                                |
| `pnpm build:app`                                                                | **exit 0** en 383 s: Vite build (2,0 s) y `cargo build --release` (5 min 51 s) → `lead-intelligence.exe` (19 MB)                                                                                                                                                                                                                                                                         |

## 8. Pruebas obligatorias del prompt

| Caso                                           | Prueba                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Gates y exclusiones                            | `test_a_failed_gate_excludes_and_an_undecided_one_asks_for_more_data`, `test_exclusions_the_do_not_contact_list_and_closed_businesses_are_hard_gates`                                                                                                                                                                                                                                                                                                                            |
| `unknown` frente a `fail`                      | `test_unknown_facts_are_left_out_while_failures_count`                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Verticales con criterios no aplicables         | `test_a_feature_that_does_not_apply_never_counts`                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Fórmula y suma de contribuciones               | `test_contributions_add_up_to_each_formula_and_to_the_total`; en Rust `scoring_payloads_that_claim_too_much_are_rejected`; en TS `contracts.test.ts`                                                                                                                                                                                                                                                                                                                             |
| Determinismo y versionado                      | `test_the_same_facts_and_versions_always_give_the_same_scorecard`, `test_versions_are_history_and_only_a_changed_definition_makes_a_new_one`                                                                                                                                                                                                                                                                                                                                     |
| Evidencia inexistente no puntúa                | `test_nothing_counts_without_evidence`; el core rechaza features, razones y ofertas sin evidencia                                                                                                                                                                                                                                                                                                                                                                                |
| Cambio de modelo no sobrescribe historia       | `test_a_new_model_version_never_rewrites_the_scores_of_earlier_runs`                                                                                                                                                                                                                                                                                                                                                                                                             |
| Campaña latina verified frente a afinidad      | `test_latino_requirements_filter_without_moving_the_number`                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Ranking estable y desempate documentado        | `test_ranking_is_stable_and_ties_break_by_confidence_then_business_id`, `test_the_rank_is_the_engine_order_and_pages_keep_it`                                                                                                                                                                                                                                                                                                                                                    |
| precision@K sobre fixture etiquetado           | `test_offline_metrics_on_a_labeled_fixture`                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| La IA no altera scores ni inventa evidence IDs | `test_a_local_model_cannot_change_a_score_or_add_evidence`; el core rechaza razones de IA que citen evidencia ajena                                                                                                                                                                                                                                                                                                                                                              |
| Otros                                          | Reanudación (`test_a_run_scores_each_business_once_and_resumes_after_a_stop`), simulación sin escritura igual al run (`test_a_simulation_writes_nothing_and_matches_the_run_over_the_same_facts`), resultados y fuga (`test_outcomes_follow_the_stage_machine_and_calibration_ignores_earlier_ones`), oferta y borrador, validación de definiciones, rutas IPC; UI (`ScorecardPanel`, `IcpBuilderScreen`, `ScoringModelsScreen`, `LeadExplorerScreen`, Jobs, Businesses, router) |

## 9. Riesgos y deuda pendiente

| Asunto                                                                                                                                                                                                     | Cuándo importa                                    |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| **No verificado en la ventana real de la app.** La UI está probada con fixtures del servicio real y el flujo IPC contra el worker real, pero no se pulsó en Tauri (el equipo estaba ocupado con un juego). | Antes de usarla en serio                          |
| Los pesos son heurísticos y no están validados con resultados: las métricas solo describen hasta tener etiquetas (aviso por debajo de 30).                                                                 | Hasta registrar resultados reales                 |
| Ninguna fuente conectada da ratings o reviews: el criterio de reputación siempre queda indeciso.                                                                                                           | ICPs que exijan reputación                        |
| El tamaño solo se ve por ubicaciones y la capacidad por proxies explícitos.                                                                                                                                | ICPs de empresas grandes                          |
| Solo se lee la auditoría más reciente; los canales se deciden al leer los hechos.                                                                                                                          | Sitios que cambian mucho                          |
| Frases, impactos y borradores solo en inglés.                                                                                                                                                              | Outreach en español                               |
| Una simulación sobre el almacén lee como mucho 5 000 negocios.                                                                                                                                             | Proyectos grandes: simular sobre hechos guardados |
| Cada score guarda hechos y scorecard (unos 30 KB por negocio por run).                                                                                                                                     | Muchos runs sobre proyectos de miles de negocios  |
| Rendimiento medido solo sobre la semilla (38 negocios).                                                                                                                                                    | Proyectos de miles de negocios                    |
| `score_runs.dataset_snapshot_id` eliminado; exportar leads llega en la fase 13.                                                                                                                            | Fase 13                                           |
| El `pnpm check` completo no se repitió con la máquina libre: con el juego abierto, 9 pruebas de UI agotan su timeout y las 3 pruebas de IA en vivo se omiten por falta de VRAM.                            | Siguiente `pnpm check` con la GPU libre           |

## 10. Lo que esta fase NO hizo

Queda fuera:

- el envío de outreach: el mensaje es un borrador;
- entrenar un modelo o ajustar pesos;
- las exportaciones (fase 13);
- el empaquetado del worker (fase 14).

Ningún score cambia un hecho, y la IA no puntúa ni aporta evidencia.
