# Buscar clientes: diseño de la experiencia simple

## Promesa y recorrido

La portada dice: **«Te decimos a quién llamar, su teléfono o correo, y qué ofrecerle»**. La persona
elige su servicio, el tipo de negocio, el lugar y el tamaño de la lista; un solo botón guarda (o
versiona) la búsqueda y «Mi oferta», activa esa oferta y pone en marcha el proceso existente. Al
terminar, abre una lista ordenada y exportable. La app no llama ni contacta a nadie.

1. Elegir una o varias ofertas (o describir otra y marcar los problemas que resuelve).
2. Elegir el tipo de negocio; opcionalmente pedir los negocios latinos primero.
3. Elegir estado y ciudad de sus listas y, opcionalmente, radio (40 km si se deja vacío).
4. Pedir 25, 50 o 100 clientes y pulsar **Buscar clientes**.
5. Esperar, pausar o continuar. Abrir **Tu lista de clientes** y descargarla.

## Wireframes

```text
┌ Buscar clientes ───────────────────────────────────────────────┐
│ Te decimos a quién llamar, su contacto y qué ofrecerle.       │
│ 1 ¿Qué vendes? [Páginas web] [Más clientes] [Reservas] ...    │
│ 2 ¿A qué negocios? [Tipo de negocio ▾] [ ] Latinos primero    │
│ 3 ¿Dónde? [Estado ▾] [Ciudad ▾] [Radio km, opcional]           │
│ 4 ¿Cuántos? (25) (50) (100)   Aproximadamente 8–20 minutos    │
│                                      [ Buscar clientes ]       │
└────────────────────────────────────────────────────────────────┘
```

```text
┌ Tu búsqueda ───────────────────────────────────────────────────┐
│ Buscando negocios… 300 encontrados                            │
│ Visitando sus páginas web… 120 de 300                         │
│ Quitando duplicados…                                          │
│ [Pausar] [Continuar] [Cancelar]                               │
│ Falló una comprobación. [Reintentar] [Saltar este paso]        │
│ Saltarla puede dejar menos información para ordenar la lista.  │
└────────────────────────────────────────────────────────────────┘
```

```text
┌ Tu lista de clientes                         [Descargar CSV] ──┐
│ [ ] Con teléfono [ ] Con correo [ ] Llamar primero             │
│ 1. Negocio · Houston, Texas · teléfono · correo · web          │
│    Por qué llamarle: evidencia guardada…                       │
│    Qué ofrecerle: Reservas en línea · LLAMAR PRIMERO           │
│    [Llamé] [Le interesa] [No le interesa] [No contactar]       │
│    [Ver guion] [Ver todo]                                      │
└────────────────────────────────────────────────────────────────┘
```

```text
┌ Mis búsquedas ─────────────────────────────────────────────────┐
│ 15 sep · Páginas web · Techos · Houston, Texas · 25 · Lista   │
│ [Abrir] [Continuar] [Repetir]                                 │
└────────────────────────────────────────────────────────────────┘
```

## Qué vendes → oferta observable

| Texto visible                       | Problemas reales que resuelve                                  |
| ----------------------------------- | -------------------------------------------------------------- |
| Páginas web                         | `no_website`, `website_unreachable`, `poor_mobile_experience`  |
| Conseguir más clientes desde la web | `weak_conversion_path`                                         |
| Reservas y citas en línea           | `no_online_booking`                                            |
| Aparecer en Google                  | `weak_local_seo`, `weak_technical_seo`                         |
| Medición y anuncios                 | `measurement_not_detected`                                     |
| Otro                                | Los que marque la persona, mostrados con las frases anteriores |

El perfil no repite lugar o categoría como condición obligatoria: esos límites ya pertenecen a la
búsqueda. Con `require_observable_problem`, un negocio solo pasa a la lista priorizada cuando la
evidencia muestra un problema que una oferta seleccionada resuelve. Si ninguno encaja, el texto es:
«No encontramos un problema que tus servicios resuelvan; prueba otra oferta o amplía la búsqueda».

### La afirmación «sin web»

`no_website` se observa cuando el negocio no tiene dominio conocido ni auditoría, pero el detalle
guardado dice literalmente que **ninguna fuente lista una web**. Overture sin URL no demuestra que
el negocio carezca de web. La interfaz debe decir «Ninguna fuente consultada publica una web», nunca
«No tiene web». Una web conocida vuelve ese problema no aplicable.

«Por qué llamarle» usa `top_reason` y los problemas del índice; el inspector conserva componentes,
criterios, decisiones y referencias del scorecard. «Ver guion» usa las afirmaciones y referencias
del informe comercial determinista. Si la IA local solo reformuló ese informe, se muestra
«Sugerencia redactada con IA» y no se presenta como hecho.

## Etapas y texto

| Etapa interna                        | Texto visible                                                       |
| ------------------------------------ | ------------------------------------------------------------------- |
| `discovery`                          | Buscando negocios… {hechos} encontrados                             |
| `acquisition`                        | Visitando sus páginas web… {hechos} de {total}                      |
| `normalization`, `entity_resolution` | Quitando duplicados…                                                |
| `latino_evidence`                    | Revisando si son negocios latinos…                                  |
| `website_intelligence`               | Buscando problemas en sus webs…                                     |
| `contact_intelligence`               | Juntando teléfonos y correos…                                       |
| `local_ai`                           | Redactando explicaciones… / Saltado: la IA local no está disponible |
| `scoring`                            | Eligiendo los mejores…                                              |
| `review_export_eligibility`          | Preparando tu lista…                                                |

«Saltado» también aparece, sin números, cuando ningún perfil de IA tiene modelo: la etapa recorre
igualmente cada tarea y las omite, y «Redactando explicaciones… 288 / 288» afirmaba un trabajo que no
se hizo.

Los números se leen exclusivamente de `progress_done`, `progress_total` y los intentos/eventos del
proceso. Sin total real no se muestra porcentaje ni se extrapola tiempo.

## Cantidad, límites y tiempo

Se buscan **12 negocios por cliente solicitado** porque no todos tendrán evidencia, un problema
compatible y contacto utilizable: 25 → 300, 50 → 600, 100 → 1.200. `max_requests` es el mismo límite
y `max_pages_per_source` es `ceil(candidatos / 100)`, con mínimo 3. Las estimaciones orientativas
mostradas son 8–20, 15–35 y 25–60 minutos; el progreso posterior solo muestra datos reales.

La ciudad se elige de una lista, no se escribe: son los 31.830 lugares del Gazetteer 2024 del US
Census Bureau (dominio público), por estado, sin su descriptor legal. Una errata ya no puede dejar la
búsqueda en cero (D-098).

Sin radio se buscan **40 km** alrededor de la ciudad. Los límites municipales se quedan cortos:
Overture (release 2026-08-19) tenía 12 remodeladores con teléfono o correo dentro de Miami, 45 a 25 km
y 64 a 40 km.

## La lista

Al terminar, la pantalla lee el explorador sobre la puntuación del proceso: prioridad o revisar,
fuera de «No contactar», con teléfono o correo utilizable, del estado y los tipos de negocio de la
búsqueda, mejor puntuación primero (evidencia latina primero si se pidió). Cada fila muestra el
negocio, su ciudad, el teléfono (o el correo), por qué llamarle y qué ofrecerle. El explorador aún
no filtra por campaña, así que dos búsquedas del mismo tipo en el mismo estado comparten lista; los
restos de otras campañas del proyecto (USAspending, otros estados) quedan fuera. Con 0 clientes se
dice cuántos negocios de la búsqueda se revisaron y qué probar.

**Descargar la lista** guarda un CSV con **todos** los clientes de la búsqueda, no solo los 100 que
se muestran: negocio, teléfono, correo, ciudad, estado, web, por qué llamarle y puntuación, con las
cabeceras en el idioma de la pantalla. El archivo lo nombra el diálogo de guardado del sistema (la
página nunca ve una ruta) y se escribe con su hash y su manifiesto, como cualquier exportación;
«No contactar» nunca sale. Hasta la fase 16 ninguna columna traía el número: el explorador solo
guarda el tipo de canal (D-097).

## Navegación y lenguaje

La barra simple contiene **Buscar clientes**, **Tu lista de clientes**, **Mis búsquedas** y
**Ajustes**. «Mostrar herramientas
avanzadas» revela intactas las pantallas anteriores. La paleta sigue indexando todas.

| Término interno         | Texto simple                 |
| ----------------------- | ---------------------------- |
| ICP                     | Qué vendes / Mi oferta       |
| pipeline                | búsqueda / proceso           |
| candidato               | negocio encontrado           |
| scoring                 | ordenar / elegir los mejores |
| gate                    | requisito                    |
| tier                    | prioridad                    |
| snapshot                | copia guardada               |
| dataset                 | datos guardados              |
| NAICS                   | tipo de negocio              |
| resolución de entidades | quitar duplicados            |
| raw record              | dato tal como se encontró    |

## Privacidad y seguridad comercial

«Latinos primero» ordena antes los negocios con evidencia empresarial publicada; jamás apellidos ni
idioma. No es un requisito: exigirlo dejó 0 clientes en Miami, donde 13 de 14 negocios no publicaban
esa evidencia (D-096). Los textos de IA son sugerencias. Las supresiones se aplican al explorar y exportar;
«No contactar» nunca sale en el archivo. La aplicación no llama, escribe, envía ni completa formularios.
