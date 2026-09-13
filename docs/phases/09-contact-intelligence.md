# Fase 09 — Contact intelligence engine

Fecha: 2026-09-13 · Estado: **completada y verificada** (`pnpm check` en verde y build de release) ·
código en `main` con el commit `961eff1`.

## 1. Punto de partida

La fase parte de `main` con la fase 08 (`0702ff2`), en el worktree `proyecto-fase-3-09a5ea`. `contact_points`, `contact_validations` y `decision_makers` existían
desde la fase 03. La fase 06 llenaba `contact_points` con teléfonos, correos y perfiles de las
fuentes y de las páginas rastreadas, pero sin método de validación, sin consolidación entre fuentes y
sin formularios. `decision_makers` nunca se había escrito. No había lista de no contactar ni caché de
DNS.

## 2. Qué se construyó

Un motor que encuentra, consolida, valida y prioriza los canales de contacto que un negocio publica,
sin contactar nunca a nadie:

```text
observaciones de fuentes + páginas rastreadas ─ extracción versionada (contacts.site) ─▶ teléfonos,
  correos, formularios de contacto y reserva, enlaces de reserva, perfiles, personas junto a su rol
  ─ canal = observaciones del mismo valor en un negocio canónico ─▶ comprobaciones (plan de
    numeración, sintaxis, DNS del dominio opcional y en caché, estructura del formulario) · fuentes ·
    frescura · decisión de una persona · lista de no contactar
  ─▶ estado exacto con método e historial · conflictos · cola de personas dudosas · hipótesis aparte
     · subpuntuación de contactabilidad explicable
```

- **Paquete `contacts/`** (Python): `rules.py` (claves de canal, teléfonos con libphonenumber,
  correos, formularios, frescura, precedencia de estados, contactabilidad, extracción de canales y
  personas de una página, patrones de correo e hipótesis, CSV de supresiones), `mail_dns.py` (MX y
  luego A/AAAA con `dnspython`, null MX, timeout de 3 s), `store.py` (extracción, clústeres,
  canales, estados, personas, hipótesis, lecturas, acciones, supresiones y runs) y `engine.py` (job
  reanudable en dos etapas).
- **Migración `0008_contact_intelligence.sql`**: `validation_method` en `contact_points`; triggers
  que impiden que una hipótesis sea principal o tenga un estado validado; `decision_makers`
  reconstruida con relación y estado de revisión; `contact_suppressions` permanente (solo se retira
  una vez, nunca se borra); `mail_domain_checks`; cuatro tablas de consulta nuevas.
- **12 mensajes IPC nuevos** (`contacts.*`, `decision_makers.*`, `suppressions.*`), espejo Rust con
  validación, vista **Contactos** en Research Center, panel **Canales de contacto** en el inspector
  de negocio y runs en Jobs. `protocol_version` y `data_contract_version` siguen en 1: los mensajes
  son nuevos, `ContactPoint` solo gana un campo nullable y `DecisionMaker`, que cambia de forma, no
  viajaba en ningún mensaje anterior.
- **Dependencias nuevas** del worker: `phonenumbers` y `dnspython` (un checkout necesita `uv sync`).

Referencia completa (qué significa exactamente cada estado, reglas, contactabilidad y lista de no
contactar) en [contact-intelligence.md](../contact-intelligence.md). Decisiones D-058 a D-063 en
[decisions.md](../decisions.md).

## 3. Requisitos del prompt y cómo se cumplen

| Requisito                                                        | Mecanismo                                                                                                                                                                                                                               |
| ---------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Encontrar y normalizar canales publicados o importados           | Extracción `contacts.site` de enlaces `tel:`/`mailto:`, texto, datos estructurados (LocalBusiness, Organization, ContactPoint, Person), formularios de contacto/cotización/reserva, enlaces a herramientas de reserva y perfiles; E.164 |
| Consolidar con procedencia y confianza                           | Canal = observaciones del mismo valor en el negocio canónico y sus registros fusionados; cada fuente con extracción, raw record, run, conector, URL, contexto, confianza y fecha                                                        |
| No comprar datos, no inventar personas, no breaches              | Solo se leen fuentes y páginas ya adquiridas; personas solo junto a un rol publicado                                                                                                                                                    |
| Sin mensajes, formularios, llamadas ni SMTP                      | Ninguna ruta de código envía nada; los formularios solo se leen del HTML guardado; la única red es una pregunta DNS opcional (D-059); las pruebas bloquean sockets y resolver                                                           |
| Estados `observed … suppressed`, nunca "validado" por sintaxis   | Ocho estados con significado fijo y método (D-058); "bien formado" se muestra como **Solo bien formado**; catch-all siempre `not_checked`                                                                                               |
| Teléfonos EE. UU. e internacionales                              | `phonenumbers`: `+` explícito o país del negocio; sin contexto → `unknown`; extensión, tipo de línea (no determinable en NANP), rango ficticio 555-01xx, fuera del plan → `invalid`                                                     |
| Correos: sintaxis, IDN, desechables, de rol, sin MX              | Señales `role_address`, `free_provider`, `disposable`, `idn`, `domain_matches_website`; MX/implícito → `domain_valid`; NXDOMAIN, null MX o sin correo → `invalid`; timeout nunca invalida ni reemplaza una respuesta                    |
| Formularios detectados sin enviar                                | Campos, método, captcha, propósito, estado HTTP de la página, acción insegura; nunca se solicita la acción                                                                                                                              |
| Decision makers solo con evidencia publicada; nada desde nombres | `Nombre, Rol`, `Rol: Nombre`, "founded/owned by", datos `Person`; relación desde el rol publicado; los nombres nunca alimentan evidencia latina (prueba dedicada)                                                                       |
| Cola de revisión de ambiguos                                     | `historical_role`, `no_role`, `conflicting_roles`, `shared_site`, `name_matches_business`; confirmar, rechazar o restablecer con motivo; un run nunca pisa la decisión                                                                  |
| Correos por patrón = hipótesis, fuera de exportación por defecto | Origen `hypothesis`, `unknown`/`stale`, nunca principal (triggers), nunca confirmada ni contada; `export_channels` las excluye salvo que se pidan; se retiran si el negocio publica la dirección o se rechaza a la persona              |
| Subscore de contactabilidad explicable, separado del lead score  | Fórmula versionada con 5 componentes y compuertas (D-063); cada componente dice qué leyó                                                                                                                                                |
| Lista DNC local (email/teléfono/dominio/negocio) con motivo      | `contact_suppressions` con motivo, fuente, actor y fecha; aplicada a observaciones, consultas y core Rust (D-062)                                                                                                                       |
| Propagada a UI y exportación; importación/exportación seguras    | Estado `suppressed` en todas las lecturas, teléfono de la lista de negocios anulado en SQL, `export_channels` sin suprimidos; CSV con errores por línea y celdas de fórmula neutralizadas (verificadas otra vez en Rust)                |
| Revalidación y frescura                                          | Umbrales por tipo; canal `stale` si la página se volvió a rastrear sin él; "Comprobar de nuevo" fuerza la consulta DNS; estados recalculados en cada lectura                                                                            |
| Documentar qué significa cada estado                             | [contact-intelligence.md](../contact-intelligence.md) sección 3                                                                                                                                                                         |
| UI                                                               | Sección 7                                                                                                                                                                                                                               |
| Sin outreach ni scoring final                                    | Nada de eso existe en esta fase                                                                                                                                                                                                         |

## 4. Estados de validación

| Estado               | Significa exactamente                                                                                                                     |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `observed`           | Visto; ninguna comprobación lo decidió todavía                                                                                            |
| `structurally_valid` | Solo bien formado (plan de numeración, sintaxis, formulario con campos en página 2xx, perfil con usuario)                                 |
| `domain_valid`       | Bien formado y el dominio respondió por DNS que acepta correo; no prueba el buzón                                                         |
| `likely_valid`       | Evidencia independiente coincide (dos tipos de fuente, dominio de la web, sitio propio) o una persona lo confirmó; no garantiza respuesta |
| `invalid`            | Una comprobación lo rechaza o una persona lo marcó                                                                                        |
| `unknown`            | No se puede comprobar (número nacional sin país) o es una hipótesis                                                                       |
| `stale`              | Visto hace demasiado o el nuevo rastreo de su página ya no lo muestra                                                                     |
| `suppressed`         | Lo cubre una entrada viva de la lista de no contactar                                                                                     |

Precedencia: suprimido → inválido por una persona → inválido por comprobación → hipótesis → obsoleto
→ confirmado por una persona → evidencia independiente → resultado de la comprobación.

## 5. Contactabilidad

```text
puntuación = round(100 × Σ peso × valor / Σ peso)
             disponibilidad 25 · validación 30 · rol 15 · frescura 15 · fuente 15
```

Solo cuentan canales publicados o importados que no sean inválidos, suprimidos ni ficticios; las
hipótesis nunca cuentan. Compuertas: `business_suppressed` (sin puntuación) y `no_usable_channel`.
El negocio sintético de fontanería de la semilla obtiene 86 con sus seis canales contados.

## 6. Lista de no contactar

| Situación                     | Efecto                                                                                                  |
| ----------------------------- | ------------------------------------------------------------------------------------------------------- |
| Añadir (manual, acción o CSV) | Normaliza (dirección en minúsculas, E.164, dominio registrable, id de negocio) y marca al instante      |
| Leer, listar, exportar        | Las consultas vuelven a comprobar entradas vivas; el core Rust rechaza estado y supresión en desacuerdo |
| Lista de negocios             | El teléfono principal suprimido se muestra vacío (guarda en SQL)                                        |
| Retirar                       | Una vez, con quién y por qué; lo que solo cubría esa entrada vuelve a `observed`                        |
| Borrar                        | Imposible: el trigger lo rechaza                                                                        |
| Importar CSV                  | `kind,value[,reason]`, 5 000 filas, 1 000 000 caracteres; cada fila inválida dice su línea              |
| Exportar CSV                  | Celdas que empiezan por `=`, `+`, `-`, `@`, tab o CR con `'` delante; Rust revisa cada celda            |

## 7. UI conectada

| Pantalla                           | Qué hace con datos reales                                                                                                                                                                                                                                                                      |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Research Center ▸ Contactos**    | Pestaña _Canales_: tabla con negocio, tipo, valor, estado (palabras y código), rol, frescura, fuentes, última vez y principal; filtros por tipo, estado, rol y frescura; hipótesis y suprimidos solo si se piden. Último run con pausa, reanudación y cancelación; botón con o sin DNS.        |
| **Personas por revisar**           | Personas ambiguas con motivos, contexto publicado y fuente; confirmar o rechazar con motivo.                                                                                                                                                                                                   |
| **No contactar**                   | Lista con motivo, fuente, actor y fecha; añadir, retirar con motivo, importar CSV con resultado por línea y exportar CSV (área de texto y enlace).                                                                                                                                             |
| **Inspector de negocio ▸ Canales** | Contactabilidad con componentes y fórmula, conflictos, canales por tipo con estado, método, fuentes, fechas, señales y supresión; hipótesis aparte bajo un aviso; personas publicadas; decisiones; acciones (comprobar, confirmar, marcar inválido, quitar revisión, principal, no contactar). |
| **Jobs**                           | Revisiones de contactos junto a los demás motores, con cancelación.                                                                                                                                                                                                                            |

## 8. Archivos

**Nuevos (Python):** `contacts/` (`__init__.py`, `rules.py`, `mail_dns.py`, `store.py`,
`engine.py`), `migrations/sqlite/0008_contact_intelligence.sql`, `tests/test_contacts_rules.py`,
`tests/test_contacts.py`.

**Modificados (Python):** `pyproject.toml` y `uv.lock` (`phonenumbers`, `dnspython`), `domain.py`
(enumeraciones `SuppressionKind`, `DecisionMakerReview`, `DecisionMakerRelationship`,
`MailDomainResult`; `ContactPoint.validation_method`; `DecisionMaker` reconstruido; registros
`ContactSuppression` y `MailDomainCheck`; contratos IPC de canales, personas, supresiones y runs),
`contracts.py` (12 mensajes), `handlers.py`, `worker.py` (servicio, recuperación, apagado y lookup
nulo en modo offline), `settings.py`, `repository.py` (teléfono suprimido anulado en la lista de
negocios), `resolution/store.py` (método `structure`), `testdata.py` (`seed_contacts`: dos sitios
sintéticos con personas, formularios, un número que cambia y una supresión), `export_contracts.py`
(17 fixtures), `tests/test_worker_process.py` (la semilla de desarrollo pasa de 36 a 38 negocios).

**Rust:** `data.rs` (espejo con validación y prueba de celdas CSV), `commands.rs` (12 comandos),
`worker.rs`, `lib.rs`, `tests/contracts.rs`, `tests/ipc.rs`.

**UI:** `features/contacts/` (`ContactsView.tsx`, `ContactPanel.tsx`, `labels.ts` y prueba),
`features/businesses/BusinessInspector.tsx` y su prueba, `features/research/ResearchCenterScreen.tsx`,
`features/jobs/` (pantalla y prueba), `lib/ipc.ts`, `lib/locales/{en,es}.ts`,
`lib/contracts.test.ts`, `app/router.test.tsx`.

**Contratos:** `packages/shared-types` (esquema, `contracts.ts`, 17 fixtures nuevas; cambian
`business_detail`, `business_cluster_detail` y `match_detail` por `validation_method`).

**Docs:** `contact-intelligence.md` (nuevo), `decisions.md` (D-058…D-063), `architecture.md`,
`data-model.md`, `operations.md` (también el conteo de la semilla, que seguía en 24), `README.md` y
este reporte.

## 9. Comandos ejecutados y resultados exactos

| Comando                                                                               | Resultado                                                                                                                                                                                                                                                                           |
| ------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Dependencias                                                                          | `phonenumbers>=9.0` y `dnspython>=2.7` en `pyproject.toml` y `uv.lock`                                                                                                                                                                                                              |
| `pnpm contracts`                                                                      | exit 0: esquema, `contracts.ts` y 17 fixtures nuevas; tres fixtures existentes cambian por `validation_method`                                                                                                                                                                      |
| `ruff check` · `ruff format` · `mypy`                                                 | primeros intentos: líneas largas, lambda en bucle (B023), orden de imports, S608, asserts compuestos; mypy: atributo `field` que tapaba `dataclasses.field`, tipos de `from_row` y de dnspython (corregidos). Final: sin avisos (79 archivos, mypy 78)                              |
| `pytest tests/test_contacts_rules.py tests/test_contacts.py tests/test_model.py`      | primeros intentos: argumento `base` duplicado en una prueba, teléfono JSON-LD que perdía su código postal al deduplicar, nombre "Jordan Sample. Founded", comparación de `sqlite3.Row` con tupla (corregidos). Final: **240 ✓** en 16.5 s                                           |
| `tsc` (desktop)                                                                       | primer intento: exit 2 (comparación redundante con `""` en `ContactPanel`; corregida). Final: exit 0                                                                                                                                                                                |
| `oxlint` · `vitest` (contactos, negocios, jobs, lib, app)                             | exit 0 · **62 ✓** en 10 archivos (29.5 s)                                                                                                                                                                                                                                           |
| `cargo fmt` · `cargo clippy --all-targets -- -D warnings` · `cargo test`              | exit 0 · exit 0 · **51 ✓** (22 unitarias + 17 contratos + 4 IPC + 8 worker)                                                                                                                                                                                                         |
| Primer `pnpm check`                                                                   | exit 1 en 224 s: contratos, prettier, ruff, mypy, oxlint, clippy y `tsc` en verde; `pytest` 602 ✓, 2 omitidas y 1 fallo: `test_development_seed_creates_openable_synthetic_data` esperaba 36 negocios y la semilla ahora crea 38 (los dos sitios de contactos). Corregida la prueba |
| `pnpm check` (final)                                                                  | **exit 0** en 211 s                                                                                                                                                                                                                                                                 |
| `pnpm test:py`                                                                        | **603 ✓, 2 omitidas** en 152.5 s (prueba en vivo opt-in de fuentes; Ollama instalado pero detenido)                                                                                                                                                                                 |
| `pnpm test:ts`                                                                        | **171 ✓** en 26 archivos                                                                                                                                                                                                                                                            |
| `pnpm test:rust`                                                                      | **51 ✓** (22 unitarias + 17 contratos + 4 IPC + 8 worker)                                                                                                                                                                                                                           |
| `pnpm lint` / `pnpm typecheck`                                                        | prettier, ruff, oxlint, clippy, `tsc` y mypy sin avisos                                                                                                                                                                                                                             |
| `pytest tests/test_contacts.py::test_performance_on_many_contact_pages --durations=3` | 1 ✓ en 2.17 s (sección 11)                                                                                                                                                                                                                                                          |
| `pnpm build:app`                                                                      | **exit 0** en 146 s: Vite build y `cargo build --release` (2 m 15 s) → `lead-intelligence.exe`                                                                                                                                                                                      |

## 10. Pruebas obligatorias del prompt

| Caso                                                 | Prueba                                                                                                                                                                                                                                                                           |
| ---------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Teléfonos EE. UU. e internacionales                  | `test_us_and_international_numbers_are_normalised_with_their_line_type`, `test_extensions_fictional_numbers_and_missing_context`, `test_numbers_outside_the_numbering_plan_are_invalid`                                                                                          |
| Correos válidos/inválidos/IDN/desechables/rol/sin MX | `test_address_statuses_follow_syntax_and_the_mail_domain_answer`, `test_idn_role_free_and_disposable_addresses_are_signals`, `test_mail_domain_answers_map_to_results` (MX, implícito, null MX, NXDOMAIN, sin correo)                                                            |
| Múltiples fuentes y conflictos                       | `test_published_channels_are_consolidated_across_sources_and_validated`, `test_old_and_disagreeing_numbers_stay_visible_with_their_sources`                                                                                                                                      |
| Formulario detectado sin submit                      | `test_forms_are_read_never_submitted`; en el almacén, el formulario de contacto sintético queda `likely_valid` sin ninguna petición                                                                                                                                              |
| Persona/rol con evidencia                            | `test_people_are_read_only_next_to_a_published_role`, `test_published_people_carry_their_evidence_and_doubtful_ones_wait_for_review`, `test_published_names_never_feed_latino_evidence`                                                                                          |
| Hipótesis aislada                                    | `test_address_patterns_and_hypotheses`, `test_a_hypothesis_is_never_validated_nor_confirmed`, `test_hypotheses_are_kept_apart_never_validated_nor_exported`; en Rust, `contact_payloads_that_claim_too_much_are_rejected`                                                        |
| Supresión aplicada a UI y exportación                | `test_suppressions_reach_every_view_export_and_status_and_can_be_lifted`, `test_suppression_files_import_and_export_safely`, `test_suppression_files_are_read_by_line_and_written_without_formulas`; en Rust, `exported_do_not_contact_files_are_checked_cell_by_cell`           |
| Timeouts DNS y modo offline                          | `test_mail_domains_are_cached_bounded_and_a_timeout_never_replaces_an_answer`, `test_offline_runs_and_timeouts_leave_addresses_structurally_valid`                                                                                                                               |
| Revalidación y frescura                              | `test_freshness_by_type`, `test_a_channel_its_site_no_longer_shows_is_stale`, `test_actions_are_reviewed_written_at_once_and_revalidate`                                                                                                                                         |
| Sin llamadas ni envíos durante pruebas               | fixture autouse `no_network` en `test_contacts.py` (bloquea `getaddrinfo`, `create_connection` y el resolver DNS); las pruebas de reglas usan un resolver falso                                                                                                                  |
| Estados que no dicen más que su evidencia            | `test_statuses_never_say_more_than_their_evidence`, `test_contactability_explains_itself_and_ignores_hypotheses`                                                                                                                                                                 |
| Listas, filtros y runs                               | `test_contact_lists_filter_by_channel_quality_role_and_freshness_and_page_stably`, `test_contact_runs_resume_and_repeat_without_writing_twice`, `test_performance_on_many_contact_pages`; en Rust, `contact_fixtures_round_trip_and_pass_validation` e IPC contra el worker real |
| UI                                                   | `ContactsView.test.tsx` (lista y filtros, revisión con o sin DNS, detalle con hipótesis aparte, acción con motivo, cola de personas, lista de no contactar, error), `BusinessesScreen.test.tsx`, `JobsScreen.test.tsx`, `contracts.test.ts`                                      |

## 11. Rendimiento

Medido en esta máquina con `test_performance_on_many_contact_pages`: 150 sitios sintéticos de una
página, cada uno con teléfono, correo de rol y una persona publicada, sobre la semilla sintética del
proyecto.

| Paso                                                                                                                                | Resultado                       |
| ----------------------------------------------------------------------------------------------------------------------------------- | ------------------------------- |
| Prueba completa: insertar 150 candidatos y sus páginas con artefactos, run de extracción y validación, página de 50 canales (≥ 300) | **1.71 s** (preparación 0.13 s) |
| Límites que la prueba exige                                                                                                         | run < 120 s y página < 10 s     |

No se midió cada paso por separado. Las listas deciden todos los canales del proyecto en cada
petición (anotado en `store.list_contacts`); medirlas con decenas de miles de canales queda para las
fases 12 y 14.

## 12. Riesgos y deuda pendiente

| Asunto                                                                                                                                                                                                 | Cuándo importa                                         |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------ |
| **No verificado en la ventana real de la app.** La UI está probada con fixtures generadas por consultas reales y el flujo IPC contra el worker real, pero no se pulsó en la ventana de Tauri.          | Antes de cerrar la fase, si se quiere evidencia visual |
| El enlace «Guardar archivo» de la exportación usa `<a download>`; en WebView2 puede no abrir un diálogo. El CSV también se muestra para copiarlo; el guardado nativo llega con las exportaciones (13). | Operadores que exportan la lista                       |
| `likely_valid` no garantiza respuesta; catch-all nunca se detecta y `domain_valid` no prueba un buzón.                                                                                                 | Siempre: por diseño                                    |
| Números norteamericanos sin tipo de línea; números nacionales sin país quedan `unknown`; en la lista de no contactar se leen como EE. UU.                                                              | Negocios fuera de EE. UU.                              |
| Personas y roles solo en inglés y español; habrá nombres omitidos o mal leídos (para eso existe la cola).                                                                                              | Sitios con redacción poco común                        |
| Lista de proveedores desechables corta (30); una hipótesis usa un solo patrón, el más frecuente.                                                                                                       | Correos poco comunes                                   |
| Las listas deciden todos los canales del proyecto en cada petición.                                                                                                                                    | Proyectos con decenas de miles de canales              |
| Las consultas DNS revelan al resolver del sistema qué dominios se investigan (por eso son opcionales).                                                                                                 | Operación con privacidad estricta                      |
| El origen `user_import` está soportado pero aún no hay importador de contactos del operador.                                                                                                           | Fase 13 o cuando se pida                               |
| La semilla de desarrollo añade dos sitios sintéticos con personas, formularios y una entrada de no contactar (38 negocios en total).                                                                   | Solo desarrollo                                        |

## 13. Lo que esta fase NO hizo

Sin outreach de ningún tipo (ni llamadas, SMS, correos, conversaciones SMTP ni envíos de
formularios), sin compra de datos ni datos filtrados, sin IA local (10) y sin scoring comercial ni
lead score (11). Ninguna pantalla llama «lead» a un negocio ni usa la contactabilidad como
puntuación de oportunidad.
