# PROMPT 16 — REDISEÑO DE LA EXPERIENCIA DE USUARIO: "BUSCAR CLIENTES"

Vas a trabajar sobre un proyecto **existente y funcional**: Lead Intelligence, una app de escritorio
para Windows 11 (Tauri 2 + React/TypeScript + Rust + worker Python). Las fases 01 a 15 están
construidas, probadas y empaquetadas. Esta fase **no agrega motores nuevos**: rediseña la experiencia
para que una persona no técnica entienda el objetivo y lo consiga en minutos.

Antes de escribir código, lee el código real. Todo nombre de comando, tipo o archivo que aparece aquí
debe verificarse en el repositorio antes de usarlo. Si algo no coincide, manda el código, no este
texto.

## 1. El problema

El dueño del producto lo dijo así: _"No entiendo el proyecto. Scrapeas los leads y luego qué haces
con ellos. Hay demasiadas funcionalidades y no se ve el objetivo."_

Hoy la app muestra 16 pantallas en 6 secciones (Resumen; Adquisición: Descubrimiento, Campañas,
Fuentes; Investigación: Negocios, Explorador de leads, Centro de investigación; Inteligencia:
Constructor de ICP, Modelos de scoring, Modelos de IA; Operación: Trabajos, Datasets, Exportaciones;
Sistema: Sistema, Logs, Ajustes). Para obtener una lista de clientes hay que:

1. crear una campaña,
2. crear, guardar y **activar con un motivo** un perfil ICP con ofertas (sin eso el pipeline falla en
   la etapa de scoring con `configuration`),
3. ir a Trabajos ▸ Pipelines y lanzar el pipeline sobre la campaña,
4. abrir el Explorador de leads, elegir el run y filtrar,
5. abrir la vista previa de exportación y exportar.

Nada de eso está explicado en lenguaje normal. Términos como ICP, pipeline, candidato, scoring, gate,
tier, snapshot, dataset o NAICS no significan nada para el usuario.

## 2. El objetivo (no cambia)

Del documento `00_INSTRUCCIONES_DE_USO.md`: _"El producto no busca acumular contactos. Debe
transformar un universo amplio de negocios estadounidenses en una lista pequeña de oportunidades B2B
justificadas mediante evidencia, con alta afinidad al ICP, un problema comercial detectable, capacidad
probable de compra y datos de contacto utilizables."_

Dicho para el usuario: **"Te decimos a quién llamar, su teléfono o correo, y qué ofrecerle."**

## 3. El usuario

- Dueño o vendedor de una agencia pequeña o freelancer que vende servicios a negocios pequeños de
  Estados Unidos, muchos de ellos latinos: páginas web, reservas en línea, SEO local, marketing,
  medición.
- No es técnico. Habla español. No conoce códigos NAICS, abreviaturas de estados ni el vocabulario de
  la app.
- Quiere una lista corta y ordenada para llamar hoy, y poder bajarla a Excel.

## 4. Lo que ya existe y debes reutilizar

No reescribas motores. Compón lo que hay. Verifica cada nombre en `src-tauri/src/commands.rs`,
`src-tauri/src/lib.rs`, `apps/desktop/src/lib/ipc.ts` y `services/research-engine/src/research_engine/domain.py`.

| Paso para el usuario | Qué existe hoy |
| --- | --- |
| Buscar negocios | Campañas (`save_campaign`, `CampaignSaveRequest`). Fuente `overture.places`: Overture Maps, sin clave, trae teléfono, web y correo (probado en Houston: 99 % con teléfono, 54 % con correo, 89 % con web). El formulario ya ofrece tipos de negocio por nombre (`apps/desktop/src/features/campaigns/businessTypes.ts`) y estados por nombre. |
| Todo el proceso | Pipelines (`start_pipeline`, `list_pipelines`, `control_pipeline`, `resolve_dead_letter`) en `services/research-engine/src/research_engine/operations.py`. Etapas: descubrimiento → adquisición → normalización → resolución de entidades → (evidencia latina · inteligencia web · inteligencia de contacto) → IA local → scoring → revisión y aptitud para exportar. Un pipeline interrumpido queda en pausa y se reanuda (D-027). |
| "¿Qué vendes?" | Perfil ICP (`save_icp_profile`, `IcpSaveRequest`, activación con `change_scoring`). `IcpDefinition.offers[]` con `key`, `name`, `description`, `solves: list[ProblemType]`, `capabilities`, `not_for_categories`. `ProblemType` hoy: `no_website`, `website_unreachable`, `weak_conversion_path`, `no_online_booking`, `weak_local_seo`, `weak_technical_seo`, `poor_mobile_experience`, `measurement_not_detected`. La plantilla es `scoring/model.py::icp_template()`; `check_icp` exige al menos una oferta que resuelva algo. El modelo de scoring por defecto ("Lead Intelligence heuristic") se crea activo; tiene `require_observable_problem=True`. |
| La lista | `explore_leads` sobre `lead_index` del último run de scoring (consultas por ventanas, D-082), `get_lead_scorecard`, `get_sales_brief`, `act_on_lead` y `act_on_research` (estados y embudo), lista de no contactar (fase 09). |
| Bajarla a Excel | `preview_lead_export`, `choose_export_destination`, `start_export`, presets versionados (D-087), por ejemplo `sales_ready_concise` (CSV). |
| Proyecto | Si `project_id` es nulo, campañas y perfiles usan `ensure_default_project`. |
| IA local | Opcional. El núcleo arranca Ollama con la app y lo detiene al cerrarse (D-094). Sin Ollama, esa etapa se salta sola. |

Antes de diseñar, verifica y documenta:

- cómo se decide `no_website` con datos de Overture: que la fuente no publique web **no es** lo mismo
  que no tener web; la lista nunca debe afirmar "no tiene web" sin evidencia;
- qué razones y evidencia de `get_lead_scorecard` y `get_sales_brief` sirven para "por qué llamarle";
- qué pasa con `require_observable_problem` cuando la oferta elegida no encaja con ningún problema
  detectado, y cómo explicárselo al usuario sin jerga.

## 5. La experiencia objetivo

Principio: **tres preguntas, un botón, una lista.**

### 5.1 Pantalla principal "Buscar clientes"

Reemplaza a Resumen como ruta por defecto (`DEFAULT_ROUTE` en `apps/desktop/src/app/navigation.ts`).
Una sola pantalla, en pasos visibles, sin asistentes de varias ventanas:

1. **¿Qué vendes?** Tarjetas seleccionables con nombres del usuario, cada una mapeada a una oferta y
   a valores reales de `ProblemType`. Propuesta inicial, que debes validar contra el motor:
   - Páginas web → `no_website`, `website_unreachable`, `poor_mobile_experience`
   - Conseguir más clientes desde la web (formularios, llamadas) → `weak_conversion_path`
   - Reservas y citas en línea → `no_online_booking`
   - Aparecer en Google → `weak_local_seo`, `weak_technical_seo`
   - Medición y anuncios → `measurement_not_detected`
   - Otro: nombre libre y el usuario marca qué problemas resuelve, con frases, no con códigos.
2. **¿A qué negocios?** El desplegable de tipos de negocio que ya existe y, aparte, un interruptor
   "Solo negocios latinos" con una explicación honesta: se usa evidencia publicada, nunca el apellido
   ni el idioma. Si activarlo reduce mucho la lista, dilo.
3. **¿Dónde?** Estado por nombre, ciudad y, opcional, radio en km.
4. **¿Cuántos clientes quieres?** 25, 50 o 100. Traduce eso a límites de la campaña con una regla
   documentada (hacen falta más candidatos que leads finales) y muestra una estimación de tiempo.

Botón **"Buscar clientes"**. Por debajo, en este orden y sin que el usuario lo vea:

- crea o actualiza la campaña con `overture.places` seleccionada;
- crea o actualiza un perfil ICP "Mi oferta" en el proyecto por defecto con las ofertas elegidas, sin
  repetir como `must_have` filtros que la campaña ya aplica, y lo activa con un motivo automático
  ("Configurado desde Buscar clientes");
- lanza el pipeline sobre esa campaña.

Repetir la misma búsqueda no debe crear perfiles o campañas duplicadas sin fin: reutiliza y versiona.

### 5.2 Progreso en lenguaje normal

Mientras corre, una sola vista con mensajes por etapa, conteos reales y tiempo estimado:

| Etapa | Mensaje |
| --- | --- |
| descubrimiento | "Buscando negocios… 300 encontrados" |
| adquisición | "Visitando sus páginas web… 120 de 300" |
| normalización y resolución | "Quitando duplicados…" |
| evidencia latina | "Revisando si son negocios latinos…" |
| inteligencia web | "Buscando problemas en sus webs…" |
| inteligencia de contacto | "Juntando teléfonos y correos…" |
| IA local | "Redactando explicaciones…" (o "Saltado: la IA local no está disponible") |
| scoring | "Eligiendo los mejores…" |

- Pausar, continuar y cancelar.
- Un fallo definitivo se explica sin códigos, con "Reintentar" o "Saltar este paso" y qué implica.
- Si la app se cerró a mitad, al abrir: "Tu búsqueda quedó en pausa. Continuar".
- No inventes progreso: todo número sale de los runs reales.

### 5.3 "Tu lista de clientes"

La pantalla de resultados, ordenada de mejor a peor:

- Nombre, ciudad, **teléfono** (botón copiar), **correo**, web.
- **"Por qué llamarle"**: una o dos frases que salen de la evidencia guardada (problema observado,
  razones del scorecard). Si un texto lo redactó la IA local, se marca como sugerencia.
- **"Qué ofrecerle"**: la oferta que resuelve ese problema.
- Nivel en palabras: "Llamar primero", "Revisar", "Baja prioridad".
- Acciones: "Llamé", "Le interesa", "No le interesa", "No contactar" (usa el embudo y la lista de no
  contactar existentes), "Ver guion" (sales brief) y "Ver todo" (el inspector actual).
- Filtros simples: con teléfono, con correo, solo "Llamar primero".
- Botón **"Descargar lista (Excel/CSV)"**: un clic, diálogo de guardar y listo, con el motor de
  exportación y un preset existente. Los "No contactar" nunca salen.

### 5.4 "Mis búsquedas"

Lista de búsquedas anteriores con fecha, qué, dónde, cuántos clientes y estado. Abrir, continuar o
repetir.

### 5.5 Modo avanzado

- Nada se borra. Todas las pantallas actuales pasan a una sección "Avanzado" de la barra lateral,
  **oculta por defecto**.
- En Ajustes, "Mostrar herramientas avanzadas" (persistido en el store de preferencias).
- La paleta de comandos sigue llegando a todo.

### 5.6 Lenguaje

- Español primero, con paridad en inglés (`apps/desktop/src/lib/locales/es.ts` y `en.ts`).
- En las pantallas simples no aparecen: ICP, pipeline, candidato, scoring, gate, tier, snapshot,
  dataset, NAICS, resolución de entidades, raw record. Documenta el reemplazo de cada término.
- Primera vez: la pantalla principal ya es la búsqueda, con una línea que explica el objetivo. Sin
  tours ni carruseles.

## 6. Reglas que no se negocian

- Los principios del producto siguen: nada inventado; la app nunca llama, envía mensajes, envía
  correos ni rellena formularios; la lista de no contactar se respeta en todas las pantallas y
  exportaciones; lo latino nunca se infiere de nombres o idioma; un texto de IA es sugerencia, no hecho.
- Compón comandos existentes desde el frontend. Agrega un mensaje nuevo al worker solo si la
  composición no es posible o no es atómica, y justifícalo con una decisión en `docs/decisions.md`
  (la siguiente es D-095). Todo comando nuevo se valida en Rust como los demás, con pruebas.
- Si cambias `domain.py`, ejecuta `pnpm contracts` y sube los fixtures regenerados en el mismo commit.
- Sin dependencias pesadas nuevas. Usa los componentes shadcn/Radix que ya están y controles nativos
  cuando basten. El tema oscuro debe leerse bien (las opciones de `<select>` ya tienen colores en
  `apps/desktop/src/index.css`).
- Accesibilidad: todo usable con teclado, etiquetas reales, foco visible; el test
  `apps/desktop/src/test/contrast.test.ts` sigue pasando.
- Rendimiento: la lista abre rápido aunque haya un millón de leads (reutiliza las consultas por
  ventanas del explorador).
- No hardcodees ciudades, tipos ni resultados de prueba en la interfaz.

## 7. Entregables

1. **Primero el diseño**, antes de codificar: `docs/ux/buscar-clientes.md` con el recorrido del
   usuario, wireframes ASCII de cada pantalla, tabla de textos, mapeo "qué vendes" → ofertas y
   problemas, mapeo etapa → mensaje, reglas de cantidad → límites, qué queda en Avanzado y los
   términos reemplazados. Mantenlo actualizado.
2. Implementación en `apps/desktop` (una carpeta de feature nueva), navegación, preferencia de modo
   avanzado y textos es/en.
3. Pruebas:
   - vitest: los pasos producen `CampaignSaveRequest`, `IcpSaveRequest`, la activación y
     `PipelineStartRequest` correctos; mapeo de progreso; acciones de la lista; exportación; modo
     avanzado oculto y visible.
   - Rust y Python si agregas mensajes.
   - Todas las pruebas existentes en verde.
4. Decisión D-095 en `docs/decisions.md` y una sección "Cómo se usa en un minuto" en `README.md`.
5. Verificación en la app instalada (sección 9) con capturas en `docs/ux/screens/`.

## 8. Criterios de aceptación

- Con una carpeta de datos nueva, una persona llega a un CSV descargado con al menos 20 clientes con
  teléfono haciendo solo esto: elegir qué vende, tipo de negocio, estado, escribir la ciudad, elegir
  cantidad, pulsar "Buscar clientes", esperar y pulsar "Descargar lista". Sin abrir ninguna pantalla
  avanzada y sin escribir códigos.
- Cada cliente de la lista muestra por qué llamarle, con evidencia, y qué ofrecerle.
- Cerrar la app a mitad de búsqueda y abrirla muestra "Continuar", y continuar termina la búsqueda.
- El modo avanzado está oculto por defecto y, al activarlo, la barra lateral actual aparece intacta.
- `pnpm check` termina con código 0 (contratos, lint TS/Rust/Python, typecheck, pruebas Python/TS/Rust).
- Una búsqueda real (por ejemplo, techos en Houston, Texas) queda documentada con conteos y tiempos.

## 9. Cómo trabajar en esta máquina (Windows 11)

- Git: `git -c safe.directory='*' ...` (el `.git` pertenece a otra cuenta de Windows).
- Python: `uv sync --directory services/research-engine --locked`. Si falla con `os error 1921`,
  repite con `uv --no-cache`.
- Rust: en PowerShell, `$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"` antes de cargo. La
  primera compilación tarda varios minutos.
- Nunca corras suites de pruebas mientras cargo compila: vitest falla por sus timeouts de 5 s. Con la
  máquina cargada usa `vitest run --maxWorkers=2`.
- Textos: busca la clave en `en.ts` antes de agregarla (las claves duplicadas rompen tsc y oxlint).
  Un `.tsx` solo exporta componentes (`only-export-components`); los helpers van en `.ts`.
- jsdom y paneles redimensionables: `el.focus()` y `user.type(el, texto, { skipClick: true })`; para
  textos largos, `user.paste`.
- La carpeta de datos no puede pasar de 140 caracteres (MAX_PATH). Para pruebas usa `%TEMP%\lead-*`.
- Instalador: `pnpm build` (contratos, sidecar con PyInstaller y `tauri build`). Sale en
  `src-tauri/target/release/bundle/nsis/Lead Intelligence_0.1.0_x64-setup.exe`. Instalación silenciosa:
  `Start-Process <setup> -ArgumentList '/S', "/D=$env:LOCALAPPDATA\Lead Intelligence" -Wait`
  (pasa siempre `/D`: NSIS recuerda la última carpeta).
- Verificar la app instalada sin tocar datos reales: `LEAD_DATA_DIR` y `LEAD_LOG_DIR` apuntando a una
  carpeta temporal y `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9333`; manéjala con
  Playwright `connect_over_cdp` desde el venv del worker. El router es HashRouter
  (`location.hash = '#/...'`). Ciérrala con `CloseMainWindow()` para que corran los manejadores de
  salida (así se detiene Ollama). Las listas nativas de `<select>` no salen en las capturas.
- Commits: solo la identidad del dueño del repositorio, sin coautores de IA ni menciones a asistentes
  en archivos o mensajes. Directo a `main`, sin ramas ni pull requests.

## 10. Entrega

Al terminar, entrega un resumen con archivos modificados, comandos ejecutados, pruebas y resultados
exactos, capturas y riesgos pendientes, como exigen las reglas de `00_INSTRUCCIONES_DE_USO.md`.
