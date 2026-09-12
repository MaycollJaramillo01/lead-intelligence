# Fase 06 — Normalización, entity resolution y deduplicación

Fecha: 2026-09-12 · Estado: **completada y verificada** (`pnpm check` en verde y build de release).

## 1. Punto de partida

La fase parte del commit de la fase 05 (`19c44ca`), entregada con `pnpm check` en verde. Se trabajó en
un worktree nuevo con las dependencias instaladas desde los lockfiles. No se repitió `pnpm check`
sobre el commit base: la verificación completa se hizo al final, sobre el resultado.

## 2. Qué se construyó

Un **motor reproducible y reversible** que convierte candidatos heterogéneos en negocios canónicos
sin fusionar empresas distintas por coincidencias superficiales:

```text
candidato ─ ingesta ─▶ registro + observaciones normalizadas (con el valor crudo al lado)
          ─ blocking ─▶ pares ─ features y pesos ─ reglas de seguridad ─▶ auto_merge · manual_review · distinct
          ─ clusters (personas primero) ─▶ negocio raíz + historial de merges ─▶ valores canónicos por campo
```

- **Paquete `resolution/`** (Python): `normalize.py` (normalizadores puros y versionados),
  `matching.py` (blocking, comparación, reglas, clusters), `store.py` (ingesta, pares, clusters,
  valores canónicos, cola de revisión y decisiones) y `engine.py` (job reanudable).
- **Migración `0005_entity_resolution.sql`**: `entity_records`, `match_candidates`,
  `business_merges` y `canonical_values`; enumeraciones `match_outcome`, `match_status`,
  `match_risk`, `canonical_field` y `selection_method`; máquina de estados de los pares y vuelta de
  un registro fusionado a su estado anterior; triggers de inmutabilidad e historial.
- **7 mensajes IPC nuevos** (aditivos: `protocol_version` y `data_contract_version` siguen en 1),
  espejo Rust con validación y **Businesses conectado a los negocios canónicos**, con una vista
  nueva de posibles duplicados.

Referencia completa en [resolution.md](../resolution.md). Decisiones D-036 a D-044 en
[decisions.md](../decisions.md).

## 3. Requisitos del prompt y cómo se cumplen

| Requisito                                   | Mecanismo                                                                                                                                                                                                                           |
| ------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Tres niveles separados                      | `raw_records` y `discovery_candidates` inmutables; observaciones con valor crudo y normalizado, cada una de una extracción versionada (`resolution.candidate`, `resolution.site`); negocio canónico = raíz de un cluster (D-036)    |
| Fusión que conserva miembros y se deshace   | Ninguna observación se mueve: `merged_into_id` apunta a la raíz; deshacer cambia dos columnas y restaura el estado previo; `business_merges` guarda merge y unmerge con actor, motivo y hora, sin borrar nada                       |
| Normalización pura, versionada y probada    | `normalize.py` sin almacén, red ni reloj; `NORMALIZER_VERSION 1.0.0` y `TAXONOMY_VERSION 2026.1`; cada resultado distingue `present`, `missing`, `unknown`, `not_applicable` e `invalid`                                            |
| Blocking exacto y candidato                 | Dominio registrable, E.164 e id estable de fuente; token distintivo por ciudad o ZIP, nombre completo, calle, dominio de email y celdas de ~1 km; bloques de más de 250 omitidos y contados (D-039)                                 |
| Decisión explicable y guardada              | `match_candidates`: campos comparados con su acuerdo, claves de blocking, cada peso y regla con su detalle, score, resultado, riesgo, versión de reglas, job y actor; las decisiones humanas van a `review_decisions` con su motivo |
| Sin apellido, idioma ni origen              | Ninguna feature los usa: los nombres se comparan como cadenas normalizadas. El corpus sintético usa sílabas neutras y palabras de oficio                                                                                            |
| Teléfono compartido no basta                | Regla `shared_phone_only`; las líneas toll-free y premium solo suman +1.0 y nunca cuentan como identidad                                                                                                                            |
| Franquicias con el mismo dominio            | `same_brand_different_location`: `distinct` y separación dura entre sus clusters                                                                                                                                                    |
| Nombres genéricos con dirección distinta    | `generic_name`: nunca automático; un nombre genérico solo se compara con el mismo nombre en el mismo lugar                                                                                                                          |
| Cambios de dirección o nombre como historia | `address_changed` manda el par a revisión; cada ubicación conserva su fecha y la ubicación canónica prioriza la más reciente sin ocultar la anterior                                                                                |
| Entidades `organization` y `location`       | No hacen falta todavía: un negocio es un establecimiento y la relación de marca queda como par distinto con su regla (D-044)                                                                                                        |
| Valores canónicos por confianza y frescura  | `canonical_values`: confianza de la fuente → confianza (frescura primero en ubicación) → id más antiguo; elección manual conservada; `alternative_count` (D-041)                                                                    |
| Sin O(n²), índices, lotes y checkpoints     | Blocking con tope; índices de cola y de `merged_into_id`; ingesta en lotes de 200 con cursor; escrituras en lotes de 500 (D-042)                                                                                                    |
| Determinismo                                | Orden estable en blocking, comparación y union-find; raíz = id más pequeño; pruebas con entrada desordenada y segunda ejecución sin cambios                                                                                         |
| Recalcular sin destruir decisiones manuales | Una ejecución nueva refresca la evidencia pero no el estado de un par decidido por una persona; merges manuales primero; separaciones manuales impiden cadenas (`cluster_conflict`) (D-040)                                         |
| UI de revisión                              | Sección 8                                                                                                                                                                                                                           |
| No ocultar conflictos tras fusionar         | `conflict_count` en la lista; el inspector marca el valor mostrado, cuenta los valores distintos y muestra todos con su fuente                                                                                                      |

## 4. Normalización

- **Texto**: entidades HTML, mojibake de UTF-8 leído como cp1252 (`CafÃ©` → `Café`), NFC,
  caracteres invisibles y de control, espacios; `N/A`, `null` o `-` son `missing`, `unknown` o
  `TBD` son `unknown`, `not applicable` es `not_applicable`. Nada se inventa.
- **Nombres**: el original se conserva; la clave pliega mayúsculas, acentos y puntuación (`&` →
  `and`), quita formas legales solo al final (`LLC`, `L.L.C.`, `Inc.`…), separa `dba` (los dos
  nombres identifican al negocio) y distingue palabras distintivas de genéricas, también con typos.
- **Teléfonos**: E.164 para NANP y números con `+`; extensión aparte; etiquetas `Tel:` y letras
  vanity; tipo geográfico, toll-free, premium, ficticio (555-01xx) o internacional. Un número sin
  país fuera de NANP es `invalid`, nunca adivinado.
- **Emails**: sin `mailto:` ni adornos; dominio en minúsculas y punycode; parte local intacta (puntos
  y `+tag` son buzones distintos); marca de rol (`info@`) y de proveedor gratuito.
- **Websites**: esquema y host en minúsculas, sin `www.`, puerto y documento por defecto, fragmento,
  parámetros de tracking ni barra final; IDN a punycode; dominio registrable, subdominio y
  plataformas de perfil marcadas.
- **Redes sociales**: plataforma y handle estable (Facebook, Instagram, X, LinkedIn, YouTube, TikTok,
  Yelp, WhatsApp, Pinterest, Nextdoor); un enlace de compartir no es un perfil.
- **Direcciones de EE. UU.**: una línea o componentes (ganan los componentes): número y calle con
  abreviaturas USPS, unidad (`Suite 4`, `Ste 4`, `#4`) esté donde esté, ciudad, estado, ZIP y ZIP+4
  (un ZIP de 4 dígitos recupera su cero) y coordenadas si existen. Ciudad, estado, county y metro
  normalizados.
- **Categorías**: vocabulario versionado de 24 claves con alias en inglés, español y NAICS; gana el
  alias más largo y la etiqueta fuente se conserva.

**Ingesta.** Cada candidato se convierte en un registro (`entity_records`) con las observaciones de
su extracción `resolution.candidate`: nombres legal y comercial, ubicación, sitio o perfil social,
teléfono y email con su validez estructural (`contact_validations`, método `structure`) y
categoría. Las páginas rastreadas del propio sitio (fase 05) añaden, como `resolution.site`, los
emails, teléfonos y perfiles que publican, con la página como procedencia y sin repetir valores ya
observados. La ingesta es idempotente.

## 5. Entity resolution — `RULES_VERSION 1.0.0`

**Features y pesos** (log-odds, prior −5.0, score = logística del total):

| A favor                                                                 | Peso | En contra                         | Peso |
| ----------------------------------------------------------------------- | ---: | --------------------------------- | ---: |
| mismo id de fuente estable                                              | +7.0 | otro estado                       | −6.0 |
| mismo nombre · mismo sitio                                              | +3.5 | nombre distinto (similitud < 0.5) | −4.0 |
| mismo teléfono local · misma calle                                      | +3.0 | otra suite en la misma calle      | −3.0 |
| nombre similar (≥ 0.90)                                                 | +2.5 | otra calle en la misma zona       | −2.5 |
| a menos de 150 m                                                        | +2.0 | otra ciudad · a más de 5 km       | −2.0 |
| mismo dominio de email                                                  | +1.5 | sitios distintos                  | −1.5 |
| misma ciudad · nombre relacionado (≥ 0.80) · línea toll-free compartida | +1.0 | teléfonos distintos               | −1.0 |
| mismo ZIP · misma categoría                                             | +0.5 | otra categoría                    | −0.5 |

Un nombre genérico cuenta la mitad. Los nombres numerados con números distintos («Store 12» y
«Store 13») son nombres distintos.

**Decisión.** `auto_merge` con score ≥ **0.97**, dos identidades coincidentes (una de ellas el
nombre o el id de fuente), sin conflictos y sin nombre genérico; `manual_review` con score ≥
**0.25** o cuando una regla lo pide; `distinct` en otro caso. Las reglas de seguridad solo añaden
cautela: `shared_phone_only`, `same_brand_different_location`, `address_changed`, `generic_name`,
otro estado (salvo el mismo registro de fuente, que va a revisión), `insufficient_identity` y
`cluster_conflict`. Un merge automático tiene riesgo bajo con tres o más identidades y medio con
dos; cualquier otro par tiene riesgo alto cuando actúa una regla, hay un conflicto o una revisión
puntúa por debajo de 0.6, medio en las demás revisiones y bajo si es distinto.

## 6. Clusters, merge/unmerge y valores canónicos

- **Clusters** recalculados desde los pares guardados con union-find: los merges de personas
  primero y siempre; los automáticos por score, salvo que unan registros que una persona o un
  conflicto duro separaron (el par vuelve a revisión con `cluster_conflict`). La raíz es el id más
  pequeño (UUIDv7: el registro más antiguo), así que no depende del orden.
- **Escrituras**: solo diferencias, en lotes. Un registro que entra queda `merged` con
  `merged_into_id` y una fila en `business_merges` (par, explicación, actor y motivo); uno que sale
  recupera su estado y su merge se cierra (`unmerged_at`, `unmerged_by`, `unmerge_reason`). Ambos se
  auditan (`merge`, `unmerge`).
- **Decisiones**: fusionar, mantener separados, aplazar y deshacer, con motivo opcional (máx. 2000
  caracteres). Deshacer revierte la última decisión del par; deshacer lo que decidió el motor lo
  manda a una persona. Se rechazan mientras un run trabaja en el mismo proyecto.
- **Re-ejecución**: refresca la evidencia de todos los pares sin tocar el estado de los decididos
  por personas; los pares del motor que las reglas ya no comparan se retiran con
  `no_longer_compared`.
- **Valores canónicos** por campo (nombre, ubicación, sitio, teléfono, email, categoría):
  confianza de la fuente (API oficial y registro público 3; directorio, dataset, API de plataforma y
  sitio del negocio 2; búsqueda web, importación, semilla manual y sintético 1), luego confianza
  (frescura primero para la ubicación), luego el id más antiguo. Una elección manual se conserva
  mientras su observación siga en el cluster. El nombre mostrado del negocio sigue al nombre
  elegido.

## 7. Rendimiento y métricas

Corpus sintéticos en `tests/resolution_corpus.py`: nombres ficticios, dominios `.example` y
teléfonos 555-01xx. La precisión se juzga sobre los clusters resultantes (cuenta la transitividad).

| Corpus                                          | Registros | Pares verdaderos | Pares fusionados automáticamente | Precisión automática | Recall automático | Recall con revisión |
| ----------------------------------------------- | --------: | ---------------: | -------------------------------- | -------------------: | ----------------: | ------------------: |
| Casos difíciles etiquetados                     |        31 |               11 | 8 (8 correctos)                  |            **1.000** |             0.727 |           **1.000** |
| Generado a escala (18 000 entidades, semilla 6) |    30 719 |           16 305 | 14 438 (todos correctos)         |           **1.0000** |            0.8855 |          **0.9989** |

- **Escala**: 55 093 pares comparados de 471 813 121 posibles, 45 700 bloques (el mayor de 28
  registros, ninguno omitido), **15.4 s** en esta máquina (8.6 s normalizando). A revisión: 1 861
  pares, 1 849 de ellos duplicados verdaderos.
- **Run completo en el almacén** (2 551 candidatos generados): inserción 0.6 s y resolución
  **7.0 s** (367 candidatos/s): 11 526 observaciones, 3 823 bloques, 1 628 pares (1 153 automáticos,
  212 a revisión, 1 distinto), 678 clusters y 935 registros fusionados.
- **Lista canónica** con 20 000 negocios: escritura a 660 negocios/s; primera página 25.9 ms,
  página 51 26.5 ms, búsqueda 85.3 ms, orden por última vista 21.9 ms, detalle 3.0 ms (umbral 250 ms).

## 8. UI conectada

| Pantalla                 | Qué hace con datos reales                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Businesses**           | Lista de negocios canónicos: cada fila muestra el valor elegido de cada campo, cuántos registros forman el negocio y cuántos campos tienen valores en conflicto. Los registros fusionados no aparecen, pero la búsqueda los encuentra a través de su negocio. Conmuta entre negocios y posibles duplicados.                                                                                                                                                                                                                                                                                         |
| **Posibles duplicados**  | Cola ordenada por score, filtrable por estado (pendientes por defecto), riesgo, score mínimo y fuente; último run con su progreso y botón para resolver. Revisión de un par: score, estado, riesgo, resultado recomendado y versión de reglas; comparación campo a campo con coincidencias y conflictos resaltados; razones con sus pesos y claves de blocking; motivo y acciones fusionar, mantener separados, aplazar y deshacer (según el estado); evidencia de los dos registros lado a lado con fuente, extractor, versión, fecha y confianza de cada valor; historial de decisiones y merges. |
| **Inspector de negocio** | Registros que forman el negocio, valor mostrado marcado en cada grupo, aviso cuando hay valores distintos, acción para mostrar otro valor (queda como elección manual), historial de merges y aviso cuando un registro pertenece a otro negocio.                                                                                                                                                                                                                                                                                                                                                    |
| **Jobs**                 | Runs de resolución junto a descubrimientos y rastreos, con progreso y pausa, reanudación o cancelación a través del motor dueño del job.                                                                                                                                                                                                                                                                                                                                                                                                                                                            |

Ninguna pantalla puntúa ni califica: el score es solo similitud entre dos registros.

## 9. Archivos

**Nuevos (Python):** `resolution/` (`normalize.py`, `matching.py`, `store.py`, `engine.py`),
`migrations/sqlite/0005_entity_resolution.sql`, `tests/resolution_corpus.py`,
`tests/test_normalize.py`, `tests/test_matching.py`, `tests/test_resolution.py`.

**Modificados (Python):** `domain.py` (4 registros, 5 enumeraciones, transiciones y contratos de
runs, cola, par, acciones y elección de valor; `BusinessListItem` y `BusinessDetail` con registros,
valores canónicos y merges), `repository.py` (lista canónica, búsqueda que devuelve la raíz, detalle
del cluster, filtro de la extracción vigente), `datasets.py` (el snapshot excluye registros
fusionados), `contracts.py`, `handlers.py`, `worker.py` (servicio, recuperación y apagado),
`testdata.py` (candidatos sintéticos y `seed_resolution`), `export_contracts.py` (9 fixtures),
`tests/test_model.py`, `tests/test_worker_process.py`.

**Rust:** `data.rs` (espejo con validación: registros, observaciones y valores canónicos de cada
negocio, filtros y orden de la cola, un detalle describe su par, motivos), `commands.rs`
(7 comandos), `worker.rs`, `lib.rs`, `tests/contracts.rs`, `tests/ipc.rs`.

**UI:** `features/resolution/` (`DuplicatesView.tsx`, `MatchReview.tsx`, `labels.ts` y pruebas),
`features/businesses/` (`BusinessesScreen.tsx`, `BusinessInspector.tsx`, `columns.tsx` y pruebas),
`features/jobs/`, `lib/ipc.ts`, `lib/locales/{en,es}.ts`, `lib/contracts.test.ts`,
`app/router.test.tsx`.

**Contratos:** `packages/shared-types` (esquema, `contracts.ts`, 9 fixtures nuevas y 2 actualizadas).

**Docs:** `resolution.md` (nuevo), `decisions.md` (D-036…D-044), `architecture.md`,
`data-model.md`, `operations.md`, `README.md`.

## 10. Comandos ejecutados y resultados exactos

| Comando                                                          | Resultado                                                                                                                                                                       |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pnpm install --frozen-lockfile` · `uv --no-cache sync --locked` | exit 0                                                                                                                                                                          |
| `pnpm contracts`                                                 | esquema, `contracts.ts`, 9 fixtures nuevas y 2 actualizadas                                                                                                                     |
| `cargo fmt` · `cargo clippy --all-targets -- -D warnings`        | exit 0                                                                                                                                                                          |
| `ruff format` · `ruff check` · `mypy`                            | sin avisos (58 archivos)                                                                                                                                                        |
| Pruebas lanzadas en paralelo con la compilación de clippy        | `test_performance_on_a_synthetic_dataset` bajo su umbral (201 negocios/s < 300) y 5 pruebas de vitest pasadas de 5 s, dos en pantallas que esta fase no tocó: contención de CPU |
| Las mismas pruebas sin carga                                     | vitest **152 ✓** en 23 archivos; rendimiento del repositorio ✓ (sección 7); normalización, matching y resolución **101 ✓** en 29.2 s                                            |
| `pnpm check` (final)                                             | **exit 0** en 320 s                                                                                                                                                             |
| `pnpm test:py`                                                   | **502 ✓, 2 omitidas** en 179.7 s (prueba en vivo opt-in de fuentes; Ollama detenido)                                                                                            |
| `pnpm test:ts`                                                   | **152 ✓** en 23 archivos                                                                                                                                                        |
| `pnpm test:rust`                                                 | **44 ✓** (21 unitarias + 11 contratos + 4 IPC + 8 worker)                                                                                                                       |
| `pnpm lint` / `pnpm typecheck`                                   | ruff, oxlint, prettier, clippy, `tsc` y mypy `--strict` sin avisos                                                                                                              |
| `pnpm build:app`                                                 | **exit 0** en 257 s: Vite build y `cargo build --release` (4 m 01 s) → `lead-intelligence.exe`                                                                                  |

## 11. Pruebas obligatorias del prompt

| Caso                                          | Prueba                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Mismo negocio con sufijos o nombres distintos | Corpus `rivera` (LLC, Inc., mayúsculas), `sol` (dba), `cafe` (entidades HTML frente a mojibake) y `widgets` (mismo id oficial): fusión automática en `test_hard_cases_are_decided_conservatively`; `test_business_names_keep_the_original_and_compare_by_key`, `test_dba_generic_and_empty_names`                                                                                                                                                       |
| Teléfono formateado de varias formas          | `rivera`: `(512) 555-0110`, `512.555.0110`, `+1 512 555 0110 ext. 3`; `test_phones_become_e164_with_the_extension_apart`, `test_structurally_invalid_phones_are_not_guessed`                                                                                                                                                                                                                                                                            |
| Dominios redirect/www                         | `rivera`: `http://www.…/?utm_source=…`, `https://…` y `…/index.html`; `test_websites_are_canonical_and_know_their_registrable_domain`, `test_international_domains_are_stored_as_punycode`, `test_platform_pages_and_bad_urls`                                                                                                                                                                                                                          |
| Sucursales legítimas                          | `sunrise`: misma marca y dominio en otra ciudad → `distinct` con `same_brand_different_location`; un segundo registro incompleto de una sucursal → revisión; `test_numbered_establishments_are_different_businesses`                                                                                                                                                                                                                                    |
| Franquicias                                   | `quicklube`: mismo nombre, sitio y línea 800 en Austin, Dallas y Miami → `distinct`                                                                                                                                                                                                                                                                                                                                                                     |
| Nombres genéricos en estados diferentes       | `cleaning`: ni siquiera se comparan (`test_generic_names_in_different_states_are_not_even_compared`); el mismo nombre genérico en otra calle de la misma ciudad → `distinct` con `generic_name`                                                                                                                                                                                                                                                         |
| Cambio de dirección                           | `blue-heron`: mismo nombre, sitio y teléfono local en otra calle → revisión con `address_changed` (`test_every_decision_explains_itself`)                                                                                                                                                                                                                                                                                                               |
| Typo pequeño frente a negocio distinto        | `garcia` y su typo («Rofing») → fusión automática; `garza` → `distinct`; `joes` (mismo nombre, misma ciudad, otra dirección) → `distinct`                                                                                                                                                                                                                                                                                                               |
| Datos incompletos                             | `lotus` (solo nombre y ciudad frente a ficha completa) → revisión; `test_address_components_win_and_nothing_is_invented`, `test_a_missing_phone_is_missing_not_invalid`, `test_text_is_cleaned_without_inventing_values`                                                                                                                                                                                                                                |
| Teléfono compartido                           | `northside` y `harbor`: la misma línea en el mismo edificio, otra suite → `distinct` con `shared_phone_only` y `different_unit`                                                                                                                                                                                                                                                                                                                         |
| Merge y unmerge                               | `test_merge_undo_defer_and_keep_separate_are_recorded_and_reversible`, `test_people_win_over_later_runs`, `test_a_manual_separation_stops_an_automatic_merge_and_says_why`, `test_people_win_and_kept_apart_records_are_never_chained`, `test_a_person_chooses_the_value_a_business_shows`; en Rust, `the_discovery_flow_runs_end_to_end_through_ipc` resuelve, rechaza un motivo en blanco, deshace un merge y elige un valor canónico a través de IPC |
| Reejecución determinista                      | `test_the_same_input_in_any_order_resolves_identically`, `test_a_second_run_changes_nothing`, `test_normalisers_are_deterministic`, `test_an_interrupted_run_resumes_from_its_checkpoint`                                                                                                                                                                                                                                                               |
| Rendimiento con decenas de miles              | `test_resolution_scales_to_tens_of_thousands_of_records` (30 719 registros; exige < 180 s, precisión ≥ 0.995 y recall con revisión ≥ 0.85), `test_a_store_run_over_thousands_of_candidates`                                                                                                                                                                                                                                                             |
| Precision/recall y umbrales conservadores     | `test_precision_and_recall_on_the_labelled_corpus`; umbrales 0.97 y 0.25 más las reglas de seguridad (sección 5)                                                                                                                                                                                                                                                                                                                                        |

## 12. Riesgos y deuda pendiente

| Asunto                                                                                                                                                                                            | Cuándo importa                                             |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| **No verificado en la ventana real de la app.** La UI está probada con fixtures generadas por las consultas reales y el flujo IPC contra el worker real, pero no se pulsó en la ventana de Tauri. | Antes de cerrar la fase, si se quiere evidencia visual     |
| La lista de sufijos públicos es un subconjunto de la PSL y los teléfonos solo se validan para NANP.                                                                                               | Dominios con sufijos poco comunes o datos fuera de EE. UU. |
| Un redirect a otro dominio (dominio viejo → nuevo) no es identidad; `www`, esquema y parámetros sí se unifican.                                                                                   | Negocios que cambiaron de dominio (fase 08 lee el sitio)   |
| El JSON-LD y los handles sociales de páginas rastreadas no son features todavía (los perfiles se guardan como contactos).                                                                         | Fase 08                                                    |
| El parser de direcciones es conservador: un formato raro queda en `street` y coincide menos (más revisión, no merges erróneos).                                                                   | Fuentes con direcciones libres                             |
| Una decisión recalcula los clusters de todo el proyecto y la comparación carga todas las features en memoria (30 719 registros en 15.4 s).                                                        | Proyectos de millones de registros                         |
| Un bloque de más de 250 registros se omite: un par que solo comparte ese bloque no se compara.                                                                                                    | Marcas con cientos de sucursales en el mismo ZIP           |
| Recall automático de 0.886 a escala: cerca del 11 % de los duplicados verdaderos espera revisión humana (umbrales conservadores a propósito).                                                     | Volumen de la cola de revisión                             |
| Etapa del lead, notas y etiquetas viven en la raíz; un merge posterior no las mueve.                                                                                                              | Fase 12                                                    |
| La frescura de una ubicación es la fecha en que se observó, no la fecha real de la mudanza.                                                                                                       | Fuentes antiguas importadas tarde                          |
| Un ZIP de 4 dígitos recupera su cero inicial (lo que suele quitar una hoja de cálculo); uno truncado por otra causa quedaría mal.                                                                 | Importaciones con ZIP dañados                              |
| Deshacer también es una decisión: deshacer dos veces vuelve a aplicar lo deshecho.                                                                                                                | Documentado; comportamiento esperado                       |
| La semilla de desarrollo resuelve 6 candidatos sintéticos para ejercitar la cola (29 negocios visibles).                                                                                          | Solo desarrollo                                            |

## 13. Lo que esta fase NO hizo

Sin clasificación latina (07), sin auditoría de sitio (08), sin validación de contactos más allá de
la estructural (09), sin IA local (10) y sin ICP ni scoring (11). Ninguna pantalla puntúa, califica
ni llama «lead» a un negocio.
