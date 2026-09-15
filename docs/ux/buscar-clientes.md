# Buscar clientes: diseño de la experiencia simple

## Promesa y recorrido

La portada dice: **«Te decimos a quién llamar, su teléfono o correo, y qué ofrecerle»**. La persona
elige su servicio, el tipo de negocio, el lugar y el tamaño de la lista; un solo botón guarda (o
versiona) la búsqueda y «Mi oferta», activa esa oferta y pone en marcha el proceso existente. Al
terminar, abre una lista ordenada y exportable. La app no llama ni contacta a nadie.

1. Elegir una o varias ofertas (o describir otra y marcar los problemas que resuelve).
2. Elegir el tipo de negocio; opcionalmente pedir solo negocios latinos.
3. Elegir estado, escribir ciudad y, opcionalmente, radio.
4. Pedir 25, 50 o 100 clientes y pulsar **Buscar clientes**.
5. Esperar, pausar o continuar. Abrir **Tu lista de clientes** y descargarla.

## Wireframes

```text
┌ Buscar clientes ───────────────────────────────────────────────┐
│ Te decimos a quién llamar, su contacto y qué ofrecerle.       │
│ 1 ¿Qué vendes? [Páginas web] [Más clientes] [Reservas] ...    │
│ 2 ¿A qué negocios? [Tipo de negocio ▾] [ ] Solo latinos       │
│ 3 ¿Dónde? [Estado ▾] [Ciudad] [Radio km, opcional]             │
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

| Etapa interna                          | Texto visible                                                       |
| -------------------------------------- | ------------------------------------------------------------------- |
| `discovery`                            | Buscando negocios… {hechos} encontrados                             |
| `acquisition`                          | Visitando sus páginas web… {hechos} de {total}                      |
| `normalization`, `entity_resolution`   | Quitando duplicados…                                                |
| `latino_evidence`                      | Revisando si son negocios latinos…                                  |
| `website_intelligence`                 | Buscando problemas en sus webs…                                     |
| `contact_intelligence`                 | Juntando teléfonos y correos…                                       |
| `local_ai`                             | Redactando explicaciones… / Saltado: la IA local no está disponible |
| `scoring`, `review_export_eligibility` | Eligiendo los mejores…                                              |

Los números se leen exclusivamente de `progress_done`, `progress_total` y los intentos/eventos del
proceso. Sin total real no se muestra porcentaje ni se extrapola tiempo.

## Cantidad, límites y tiempo

Se buscan **12 negocios por cliente solicitado** porque no todos tendrán evidencia, un problema
compatible y contacto utilizable: 25 → 300, 50 → 600, 100 → 1.200. `max_requests` es el mismo límite
y `max_pages_per_source` es `ceil(candidatos / 100)`, con mínimo 3. Las estimaciones orientativas
mostradas son 8–20, 15–35 y 25–60 minutos; el progreso posterior solo muestra datos reales.

## Navegación y lenguaje

La barra simple contiene **Buscar clientes**, **Mis búsquedas** y **Ajustes**. «Mostrar herramientas
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

«Solo negocios latinos» usa evidencia empresarial publicada; jamás apellidos ni idioma. Puede reducir
mucho la lista. Los textos de IA son sugerencias. Las supresiones se aplican al explorar y exportar;
«No contactar» nunca sale en el archivo. La aplicación no llama, escribe, envía ni completa formularios.
