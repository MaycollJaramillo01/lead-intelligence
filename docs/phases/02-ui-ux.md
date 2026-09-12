# Fase 02 — Sistema UI/UX de escritorio

Fecha: 2026-09-11 · Estado: **completada y verificada en la app real** (Windows 11, 1280×720 a 1920×1080).

## 1. Qué se construyó

El shell completo de la aplicación sobre la base de la fase 01, sin tocar el diagnóstico real ni el
stack: navegación de 16 módulos, barra superior contextual, paleta de comandos, atajos de teclado,
tabla densa con inspector lateral, sistema de estados y pantallas base. Detalle del sistema visual,
el mapa de navegación y los componentes en [ui-system.md](../ui-system.md).

Principio que gobierna la fase: **nada inventado**. Los módulos que aún no existen lo dicen y
nombran la fase que los entrega; las tablas vacías están vacías; los estados salen de comprobaciones
en vivo.

## 2. Sistema visual

- Tokens centralizados en `apps/desktop/src/index.css`: superficies, texto, acción, estado, radio,
  sombra, motion, z-index y densidad. Los componentes usan nombres semánticos, nunca colores crudos.
- **Tema oscuro como experiencia principal y tema claro completo**, con el mismo juego de tokens.
- **Densidad configurable** (cómoda / compacta) mediante variables CSS: altura de fila, padding de
  celda y tamaño de fuente de interfaz. Ningún componente la codifica.
- **Contraste validado por prueba**: un test convierte cada token de oklch a sRGB y exige 4.5:1 en
  pares de texto y 3:1 en foco y bordes de control, en ambos temas (38 comprobaciones).
- `prefers-reduced-motion` respetado globalmente.
- El estado nunca depende solo del color: siempre icono + etiqueta de texto.

## 3. Navegación y teclado

Las 16 rutas viven en un registro único (`src/app/navigation.ts`) del que leen el panel lateral, los
breadcrumbs, la paleta de comandos, los atajos y las páginas "sin configurar". Seis módulos son
reales hoy (Overview, Businesses, Lead Explorer, Jobs, System, Settings); los otros diez declaran su
propósito y la fase en la que llegan.

Atajos en registro único, con prueba que falla si dos comparten combinación y panel de ayuda (`?`)
generado de la misma lista: `Ctrl K` paleta, `Ctrl B` panel lateral, `Ctrl I` inspector, `Alt T`
tema, `Alt D` densidad, `Alt R` recomprobar sistema, `Ctrl ,` ajustes, `Alt 1..6` saltos directos.

## 4. Estado y arquitectura

| Tipo                     | Dónde                           | Regla                                              |
| ------------------------ | ------------------------------- | -------------------------------------------------- |
| Remoto / IPC             | `features/diagnostics/store.ts` | Solo a través del adaptador tipado `lib/ipc.ts`    |
| Preferencias persistidas | `stores/preferences.ts`         | Tema, densidad, idioma, tamaño del panel, columnas |
| UI efímero               | `stores/ui.ts`                  | Paleta, ayuda, inspector, fila inspeccionada       |

Ningún componente llama a `invoke` directamente. Cada módulo enrutado va dentro de su propio error
boundary, además del global.

## 5. Archivos y dependencias

59 rutas cambiadas respecto a la fase 01: 46 archivos nuevos, 11 modificados y 2 eliminados (la
pantalla de diagnóstico provisional y su prueba, sustituidas por `SystemScreen`).

Dependencias añadidas: `react-router`, `@tanstack/react-table` (v8), `@tanstack/react-virtual`,
`cmdk`, `sonner`, `react-resizable-panels` y los primitivos de shadcn/ui (Radix). Se retiraron dos
que el CLI de shadcn arrastró y no queremos: `cn` y `next-themes`.

## 6. Comandos y resultados exactos

```text
pnpm dlx shadcn@latest add dialog command tooltip dropdown-menu select switch tabs …
pnpm --filter @lead/desktop add react-router @tanstack/react-table@^8 @tanstack/react-virtual
pnpm check          # contratos, lint, formato, tipos y las tres suites
pnpm --filter @lead/desktop build
pnpm dev            # ejecución real + capturas
```

| Verificación                          | Resultado                                     |
| ------------------------------------- | --------------------------------------------- |
| `pnpm check`                          | **exit 0**                                    |
| TypeScript: oxlint, prettier, `tsc`   | sin avisos                                    |
| TypeScript: vitest                    | **107 pruebas, 14 archivos**                  |
| Rust: rustfmt, clippy, `cargo test`   | **27 pruebas**, sin avisos                    |
| Python: ruff, mypy `--strict`, pytest | **60 pruebas, 1 omitida** (Ollama detenido)   |
| Build de producción del frontend      | OK · JS 718,6 kB (219,0 kB gzip), CSS 29,9 kB |

Pruebas nuevas de esta fase: navegación y rutas, paleta de comandos, atajos y conflictos, stores de
preferencias y persistencia, sincronización de tema/densidad/idioma, i18n y fallback, filtros,
tabla (orden, selección, visibilidad de columnas, virtualización con 5.000 filas), componentes de
evidencia/score/trabajos, pantalla System y contraste de tokens.

## 7. Verificación en la app real

Conduje la aplicación con atajos reales enviados a la ventana (Alt+2, Ctrl+K, Alt+T, Alt+D, Alt+5,
Alt+6) y capturé cada estado:

| Comprobación                  | Resultado                                                                                                  |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------- |
| 1280×720, 1440×900, 1920×1080 | Sin desbordes ni cortes; la tabla conserva cabecera fija y scroll propio                                   |
| Oscuro y claro                | Ambos completos ([oscuro](img/02-businesses.png), [claro](img/02-businesses-light.png))                    |
| Densidad cómoda y compacta    | Cambia altura de fila, padding y tipografía en toda la interfaz                                            |
| Paleta de comandos            | Abre con Ctrl+K, navega y marca los módulos sin configurar ([captura](img/02-command-palette.png))         |
| Worker ausente                | Worker en **Error** y subsistemas en **Unknown — no comprobado** ([captura](img/02-no-worker-spanish.png)) |
| Español                       | Panel, breadcrumbs y pantallas traducidos (misma captura)                                                  |

Zoom 125/150 %: no se cambió el escalado de Windows. El caso equivalente sí está cubierto, porque a
150 % en 1920×1080 el viewport lógico es 1280×720, que es la resolución mínima verificada.

## 8. Defectos encontrados y corregidos durante la fase

1. `@tanstack/react-table` instaló la v9, con API distinta: fijado a v8 estable.
2. El estado vacío de la tabla se centraba sobre el ancho total de columnas y quedaba cortado por el
   inspector; ahora se superpone centrado en el área visible.
3. El botón de reinicio del worker mostraba el texto del toast ("Python worker restarted").
4. La fecha del diagnóstico usaba el texto de "datos obsoletos" aunque fueran frescos; ahora
   distingue última comprobación de dato obsoleto (solo tras un fallo).
5. Contraste: el azul primario y el destructivo del tema oscuro no llegaban a 4.5:1 con su texto, y
   los bordes de campos no llegaban a 3:1. Corregidos y cubiertos por la prueba.
6. En tema claro el panel lateral apenas se distinguía del fondo: más separación.
7. El CLI de shadcn escribió en una carpeta literal `@/` y añadió `cn` y `next-themes`: reubicado y
   limpiado.

## 9. Deuda y riesgos

1. **Mensajes de error del núcleo en inglés.** La interfaz está traducida, pero los textos que llegan
   del núcleo Rust (por ejemplo "The Python worker is not available") se muestran tal cual. Se
   resuelve mapeando códigos a cadenas de UI cuando haya más superficie de error.
2. **Bundle único de 719 kB** (219 kB gzip) sin división de código. Para una app local que carga de
   disco es aceptable; revisar si el arranque se nota.
3. **El inspector es neutral**: hasta la fase 03 no hay entidad que inspeccionar, así que muestra su
   estado vacío y, si hay selección, el identificador.
4. **Sin pruebas E2E ni de regresión visual.** La verificación de la interfaz fue manual con
   capturas, apoyada en 107 pruebas de unidad/integración con jsdom.
5. **Vistas guardadas, reordenar columnas y filtros AND/OR anidados** no están: son de la fase 12.
6. **Dos reglas de lint desactivadas** con motivo documentado (`jsx-a11y/prefer-tag-over-role` por el
   patrón ARIA de separador redimensionable y `react/incompatible-library` porque no usamos React
   Compiler).
7. **En desarrollo, StrictMode duplica la comprobación inicial** del sistema; en producción ocurre
   una sola vez.
8. Las filas de la tabla son navegables con flechas, pero aún no hay foco por celda ni selección por
   rango: llegan con datos reales.
