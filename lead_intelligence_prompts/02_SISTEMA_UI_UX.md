# PROMPT 02 — SISTEMA UI/UX DESKTOP COMPLETO

Continúa sobre el proyecto creado en la fase anterior. Antes de editar, inspecciona la arquitectura, contratos, configuración y pruebas existentes. No reconstruyas el proyecto, no sustituyas el stack y no rompas el diagnóstico real ya implementado.

## Objetivo

Diseñar e implementar la arquitectura visual completa de **Lead Intelligence** como software profesional de investigación comercial para escritorio. No quiero un dashboard SaaS genérico ni una colección de tarjetas decorativas. La experiencia debe priorizar densidad informativa, velocidad, claridad, tablas grandes, inspección lateral, navegación con teclado y observabilidad.

Referencias conceptuales: Linear, Raycast, Arc, VS Code, Attio, Clay y herramientas modernas de inteligencia. No copies ninguna interfaz.

## Dirección visual

- Dark mode como experiencia principal y light mode completo.
- Apariencia sobria, técnica, precisa y profesional.
- Excelente jerarquía tipográfica y contraste WCAG AA.
- Densidad configurable: compacta, cómoda.
- Evita glassmorphism excesivo, gradientes decorativos, tarjetas enormes, bordes en todo, espacios vacíos y estética típica generada por IA.
- Diseña para 1280×720 como mínimo práctico y optimiza 1440p/4K con escalado de Windows.

## Navegación obligatoria

Implementa rutas, layout y estados para:

```text
Overview
Discovery
Campaigns
Businesses
Lead Explorer
Research Center
ICP Builder
Scoring Models
Jobs
Sources
Datasets
AI Models
Exports
Settings
System
Logs
```

Las funciones futuras deben mostrarse como no configuradas o aún no disponibles; nunca con métricas o resultados falsos.

## Componentes de aplicación

1. Sidebar colapsable con secciones claras, indicadores de estado y persistencia local.
2. Barra superior contextual con búsqueda, acciones de pantalla y acceso a command palette.
3. Breadcrumbs útiles, no decorativos.
4. Command palette con navegación y acciones reales existentes.
5. Atajos de teclado con prevención de conflictos y panel de ayuda.
6. Sistema de notificaciones/toasts accesible y no intrusivo.
7. Panel derecho contextual redimensionable para inspeccionar una entidad sin perder la tabla.
8. Split views/redimensionamiento solo donde mejoren investigación y comparación.
9. Tabla base de alta densidad con columnas fijables, orden, selección, resize, visibilidad, filtros y virtualización preparada.
10. Filtros representados como chips editables y resumen legible de consulta.
11. Componente de evidencia con fuente, fecha, confianza y enlace/origen.
12. Componentes de score que indiquen valor, explicación, versión y confianza; no solo colores.
13. Componentes de jobs con progreso determinado/indeterminado, velocidad, errores y cancelación.
14. Empty, loading, skeleton, offline, partial-data, stale-data, error, processing y success states.
15. Error boundary global y boundaries por módulos.

## Estado y arquitectura frontend

- Separa estado remoto/IPC, estado persistente de preferencias y estado efímero de UI.
- Usa Zustand con stores pequeños y selectores; evita un store monolítico.
- Crea adaptadores tipados para consumir IPC. Los componentes no deben invocar comandos Rust arbitrarios.
- Mantén tokens de diseño centralizados: color, tipografía, spacing, radio, sombra, z-index y motion.
- Respeta `prefers-reduced-motion`.
- Persiste tema, densidad, tamaño de sidebar y columnas visibles.
- Implementa internacionalización preparada para inglés/español, aunque esta fase puede incluir solo las cadenas base.

## Pantallas de esta fase

Construye el shell real y prototipos funcionales sin datos inventados:

- `Overview`: estados del sistema y accesos, utilizando diagnósticos reales del Prompt 01.
- `Businesses` y `Lead Explorer`: tabla vacía funcional con columnas definidas, filtros y panel de inspección neutral.
- `Jobs`: lista vacía con estados reales cuando existan.
- `System`: diagnóstico completo existente, mejor presentado.
- `Settings`: tema, densidad, idioma, rutas de datos y preferencias seguras.
- El resto: estructura de página, descripción útil y estado no configurado.

## Accesibilidad y calidad

- Navegación completa por teclado.
- Focus visible consistente.
- Etiquetas accesibles para iconos y controles.
- Contraste validado.
- Tooltips no deben contener información indispensable.
- No uses color como único indicador.
- Evita renderizados innecesarios en tablas y paneles.

## Pruebas y aceptación

- Ejecuta lint, typecheck, tests y build.
- Agrega pruebas de navegación, stores, command palette, cambio de tema, persistencia y principales estados.
- Verifica manualmente resoluciones 1280×720, 1440×900 y 1920×1080.
- Comprueba teclado, foco, zoom 125/150 %, dark/light y ausencia del worker.
- No debe haber datos comerciales simulados presentados como reales.

## Entrega

Documenta sistema visual, mapa de navegación, atajos, componentes reutilizables, archivos modificados, pruebas ejecutadas, resultados y cualquier deuda concreta. Corrige los defectos detectados antes de concluir y no implementes todavía los engines de adquisición o scoring.

