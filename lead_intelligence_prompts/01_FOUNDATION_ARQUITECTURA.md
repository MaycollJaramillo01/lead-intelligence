# PROMPT 01 — FOUNDATION, ARQUITECTURA Y SCAFFOLDING

Vas a iniciar un proyecto completamente nuevo llamado provisionalmente **Lead Intelligence**. No revises, consultes, copies, importes ni reutilices repositorios, proyectos, componentes o decisiones anteriores. Esta será una aplicación de escritorio para Windows 11, no una web SaaS.

## Objetivo del producto

Construir una herramienta local que descubra, investigue, enriquezca, clasifique y priorice empresas de Estados Unidos para prospección B2B de alta calidad. El sistema podrá descubrir muchos negocios, pero su producto final será una lista pequeña de leads con evidencia verificable, contacto útil, necesidad comercial concreta, afinidad al ICP y explicación de por qué conviene contactarlos.

No implementes todavía scraping avanzado, scoring, clasificación de propiedad latina ni análisis completo con IA. Esta fase debe crear una base técnica real, estable y comprobable.

## Stack obligatorio

- Tauri 2 como shell desktop.
- React + TypeScript estricto + Vite.
- Tailwind CSS, shadcn/ui y Radix UI.
- Zustand para estado de interfaz.
- TanStack Table y Apache ECharts instalados solo si se usarán desde esta base o en fases inmediatas.
- Rust como núcleo desktop y administrador de procesos.
- Python 3.12+ como sidecar de investigación, administrado con `uv`.
- Pydantic v2, httpx, selectolax, Playwright async y orjson en el worker.
- SQLite para estado operacional.
- DuckDB + Parquet para analítica y datasets.
- Ollama como runtime inicial de IA local.
- Compatibilidad posterior con llama.cpp + CUDA.
- Nuitka o PyInstaller para distribuir el sidecar.
- Tauri Bundler para el instalador de Windows.

No uses Electron, Next.js, PostgreSQL, Redis, Celery, Docker, Kubernetes, LangChain, Qdrant, Pinecone ni un LLM cloud como dependencia obligatoria.

## Arquitectura requerida

Define una estructura mantenible equivalente a:

```text
apps/desktop/                 React + TypeScript
src-tauri/                    Rust/Tauri
services/research-engine/     Python
packages/shared-types/        contratos compartidos
config/                       configuración no secreta
data/                         bases y datasets locales
logs/                         logs rotativos
tests/                        integración/end-to-end
docs/                         decisiones y operación
```

Puedes ajustarla si justificas una opción más coherente, pero no mezcles responsabilidades.

## Implementación obligatoria

1. Inicializa Tauri, React, TypeScript y Vite con comandos reproducibles.
2. Configura lint, formato, tipado estricto y pruebas básicas para TypeScript, Rust y Python.
3. Implementa IPC tipado React ↔ Rust mediante comandos y eventos.
4. Implementa Rust ↔ Python mediante un protocolo local explícito y versionado. Prefiere JSON Lines por stdin/stdout o localhost con puerto efímero; documenta la decisión.
5. Crea el ciclo de vida real del sidecar: iniciar una sola instancia, health check, timeout, reinicio controlado, apagado limpio y captura de stderr.
6. Define un sobre estándar para mensajes: `request_id`, `protocol_version`, `type`, `timestamp`, `payload`, `error`.
7. Crea configuración por entorno con valores por defecto seguros. Ningún secreto debe incluirse en el repositorio.
8. Implementa logging estructurado con correlación por `request_id` y `job_id`, rotación y niveles configurables. No registrar secretos ni contenido sensible completo.
9. Inicializa SQLite y DuckDB mediante migraciones/versionado, no con tablas creadas ad hoc al abrir pantallas.
10. Agrega detección real de Ollama y consulta de modelos disponibles sin descargar nada automáticamente.
11. Agrega detección real de GPU NVIDIA. Obtén, cuando sea posible, nombre, VRAM total, VRAM libre, versión de driver y disponibilidad CUDA. Usa fallbacks seguros si `nvidia-smi` o CUDA no existen; no hardcodees RTX 4060.
12. Crea manejo de errores centralizado en las tres capas con mensajes técnicos en logs y mensajes claros en UI.
13. Implementa una pantalla temporal de diagnóstico que muestre estados reales de Desktop Core, Python Worker, SQLite, DuckDB, Ollama y GPU.
14. Incluye scripts documentados para instalar dependencias, desarrollar, probar, compilar y limpiar únicamente artefactos generados.

## Reglas de ingeniería

- No uses mocks para presentar servicios como activos.
- No dejes TODO, pseudocódigo ni funciones vacías como implementación final.
- No ocultes fallos con `try/catch` silenciosos.
- Evita `any`, estados globales innecesarios y archivos gigantes.
- Mantén contratos versionados y valida todos los límites entre procesos.
- Aplica timeouts a llamadas de red y procesos.
- Conserva compatibilidad Windows aunque desarrolles en otro sistema.
- No avances a módulos futuros.

## Pruebas y criterios de aceptación

Debes demostrar con comandos reales que:

- frontend compila y pasa typecheck/lint;
- Rust compila y sus pruebas pasan;
- Python instala con lockfile y sus pruebas pasan;
- el sidecar inicia, responde al health check y se apaga sin quedar huérfano;
- IPC entrega una respuesta real del worker a la UI;
- SQLite y DuckDB crean/abren sus archivos y ejecutan una consulta real;
- Ollama se reporta correctamente tanto disponible como ausente;
- la GPU se reporta correctamente o muestra una degradación explícita;
- la app puede abrir sin depender de internet.

## Entrega de esta fase

Al finalizar, entrega:

1. resumen de arquitectura;
2. árbol principal de carpetas;
3. decisiones técnicas registradas;
4. archivos creados/modificados;
5. comandos ejecutados;
6. resultado exacto de pruebas y builds;
7. riesgos o dependencias pendientes.

Corrige cualquier error encontrado antes de concluir. No afirmes que algo funciona sin haberlo ejecutado.

