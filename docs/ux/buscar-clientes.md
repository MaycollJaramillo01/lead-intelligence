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
┌ Tu lista de clientes                     [Descargar la lista] ─┐
│ Hay 18 clientes listos                                         │
│ Mostrar solo: [ ] Con teléfono [ ] Con correo                  │
│               [ ] Solo «Llamar primero»                        │
│ Techos Houston · Houston, TX                  (Llamar primero) │
│   (713) 555-0101 [copiar] · hola@techos.com [copiar] · Web: …  │
│   Por qué llamarle: no se puede reservar ni pedir presupuesto  │
│   en línea; su web muestra pocos datos del negocio…            │
│   Qué ofrecerle: Reservas y citas en línea                     │
│   [Llamé] [Le interesa] [No le interesa] [No contactar]        │
│   [Ver guion] [Ver todo]                                       │
└────────────────────────────────────────────────────────────────┘
```

```text
┌ Mis búsquedas ─────────────────────────────────────────────────┐
│ Remodelación · Miami, Florida · Latinos primero                │
│ Vendes: Páginas web, Reservas y citas en línea                 │
│ 20 sept 2026, 17:30 · Terminado · 16 clientes [Abrir] [Repetir]│
│ Techos · Houston, Texas                                        │
│ 27 sept 2026, 10:02 · En pausa              [Continuar]        │
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

**Otro** abre dos preguntas: «¿Cómo se llama tu servicio?» (hasta 80 caracteres) y «¿Qué problemas
de sus clientes resuelve?», con una casilla por problema escrita como situación del negocio:

| Casilla                                                     | Problema                   |
| ----------------------------------------------------------- | -------------------------- |
| Negocios sin página web                                     | `no_website`               |
| Webs que no cargan o se caen                                | `website_unreachable`      |
| Webs sin botón para llamar ni formulario                    | `weak_conversion_path`     |
| No se puede reservar ni pedir presupuesto en línea          | `no_online_booking`        |
| Webs con pocos datos del negocio para las búsquedas locales | `weak_local_seo`           |
| Webs con ajustes básicos de Google por mejorar              | `weak_technical_seo`       |
| Webs difíciles de usar en el móvil                          | `poor_mobile_experience`   |
| Webs que no miden sus visitas                               | `measurement_not_detected` |

«Buscar clientes» espera a tener el nombre y al menos una casilla. Se puede elegir solo «Otro» o
junto a las tarjetas. En la lista, en el archivo y en «Mis búsquedas» el servicio aparece con el
nombre que se le dio, y «Repetir» lo trae de vuelta con sus casillas (D-102).

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

Al terminar, la pantalla lee el explorador sobre la puntuación del proceso: lo que encontró **la
campaña de esa búsqueda** (D-099), prioridad o revisar, fuera de «No contactar», con teléfono o
correo utilizable, mejor puntuación primero (evidencia latina primero si se pidió). Dos búsquedas
del mismo tipo en el mismo estado ya no comparten lista. Con 0 clientes se dice cuántos negocios de
la búsqueda se revisaron y qué probar; si lo vació un filtro, se dice eso.

Cada fila muestra el negocio y su ciudad, la prioridad en palabras («Llamar primero», «Revisar»),
el teléfono y el correo con un botón de copiar, la web, **por qué llamarle** (hasta dos problemas
observados, el que más pesó primero) y **qué ofrecerle**. Los textos de «por qué» dicen lo que el
motor observó, nunca más:

| Problema                   | Por qué llamarle                                                  |
| -------------------------- | ----------------------------------------------------------------- |
| `no_website`               | ninguna fuente consultada publica una web                         |
| `website_unreachable`      | su web no cargó bien cuando la visitamos                          |
| `weak_conversion_path`     | su web no tiene un botón claro para llamar ni un formulario       |
| `no_online_booking`        | no se puede reservar ni pedir presupuesto en línea                |
| `weak_local_seo`           | su web muestra pocos datos del negocio para las búsquedas locales |
| `weak_technical_seo`       | su web tiene ajustes básicos de Google por mejorar                |
| `poor_mobile_experience`   | su web es difícil de usar en el móvil                             |
| `measurement_not_detected` | no vimos herramientas de medición en su web                       |

Filtros: **Con teléfono**, **Con correo** y **Solo «Llamar primero»**; la descarga lleva lo que
dejan. Acciones de cada cliente, sobre el embudo y la lista de no contactar existentes:

| Botón                | Qué guarda                                                                 |
| -------------------- | -------------------------------------------------------------------------- |
| Llamé / Le escribí   | Etapa «contactado» (`attempted`) con el canal; solo si aún no se contactó  |
| Le interesa          | Recorre contactado → conectado → calificado, las etapas que el embudo pide |
| No le interesa       | Etapa `not_interested`                                                     |
| No contactar         | Entrada de negocio en «No contactar», con «Deshacer»; sale de la lista     |
| Ver guion / Ver todo | La ficha del negocio en su sección de oferta, o entera; «Volver» regresa   |

**Descargar la lista** guarda un CSV con **todos** los clientes que dejan los filtros, no solo los
100 que se muestran: negocio, teléfono, correo, prioridad, por qué llamarle, qué ofrecerle, ciudad,
estado, web y puntuación, con cabeceras **y valores** en el idioma de la pantalla
(`ExportColumn.labels`, D-099). El archivo lo nombra el diálogo de guardado del sistema (la página
nunca ve una ruta) y se escribe con su hash y su manifiesto, como cualquier exportación; «No
contactar» nunca sale. Hasta la fase 16 ninguna columna traía el número: el explorador solo guarda
el tipo de canal (D-097).

## Mis búsquedas

Cada búsqueda dice qué tipo de negocio, dónde y si pidió latinos primero (del nombre de su
campaña), qué vendías (las ofertas de la versión de «Mi oferta» con la que se puntuó), cuándo se
pidió, en qué quedó y, si terminó, cuántos clientes dejó: la misma consulta de «Tu lista de
clientes», por su campaña (D-100).

| Estado              | Botones                                             |
| ------------------- | --------------------------------------------------- |
| Terminado           | **Abrir** (su lista) y **Repetir**                  |
| En pausa            | **Continuar** (reanuda el mismo proceso)            |
| En cola o en curso  | **Ver el progreso** (la pantalla «Buscar clientes») |
| Fallido o cancelado | **Repetir**                                         |

**Repetir** abre «Buscar clientes» con el formulario relleno —qué vendes, tipo de negocio, latinos
primero, estado, ciudad, radio y cuántos— y una línea que lo explica; la búsqueda solo empieza al
pulsar «Buscar clientes», así que se puede cambiar algo antes. Una ciudad escrita a mano antes de
la lista del Censo (D-098) se vuelve a elegir. Si la app se cerró a mitad, «Buscar clientes» dice
«Tu búsqueda quedó en pausa» junto a «Continuar».

## Cuando un paso falla

Los fallos pasajeros se reintentan solos (D-084). Cuando un paso agota sus intentos o el motor lo
rechaza, la búsqueda queda «Fallida» y tanto «Buscar clientes» como «Mis búsquedas» lo explican sin
códigos (D-101):

```text
┌ Tu búsqueda se detuvo al buscar problemas en sus webs. ─────────┐
│ No hubo conexión a internet o la fuente no respondió. Suele     │
│ arreglarse volviendo a intentarlo.                              │
│ Si lo saltas, no se revisan sus webs: saldrán menos clientes…   │
│ [Reintentar] [Saltar este paso]                                 │
│ ▸ Detalles técnicos (para soporte)                              │
└─────────────────────────────────────────────────────────────────┘
```

| Paso                                   | ¿Se puede saltar? | Qué implica saltarlo                                     |
| -------------------------------------- | ----------------- | -------------------------------------------------------- |
| Buscar negocios                        | No                | Sin negocios no hay lista                                |
| Visitar sus páginas web                | Sí                | Solo lo que publica la fuente: menos motivos para llamar |
| Quitar duplicados                      | No                | De ahí salen los negocios que se puntúan                 |
| Revisar si son negocios latinos        | Sí                | La lista no se ordena por evidencia latina               |
| Buscar problemas en sus webs           | Sí                | Menos clientes, solo los que tengan otro motivo          |
| Juntar teléfonos y correos             | Sí                | Solo los teléfonos y correos que publica la fuente       |
| Redactar explicaciones                 | Sí                | Solo faltan las explicaciones redactadas                 |
| Elegir los mejores · Preparar tu lista | No                | De ahí salen la puntuación y la lista                    |

Las causas se dicen en palabras (sin conexión, la fuente tardó, pidió esperar, datos ocupados, el
proceso se cerró, sin memoria, ajustes de la búsqueda, algo inesperado) y, si el motor la marca
como pasajera, se añade «Suele arreglarse volviendo a intentarlo». El mensaje del motor queda
plegado en «Detalles técnicos». La etapa que falló dice «Se detuvo aquí» y una saltada, «Saltado».

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
