# PROMPT 10 — IA LOCAL, OLLAMA, LLAMA.CPP Y RTX 4060

Continúa sobre el mismo proyecto. Inspecciona el worker, GPU detection, evidence, website y contact engines. No conviertas el LLM en el orquestador de todo el pipeline y no reemplaces análisis deterministas ya existentes.

## Objetivo

Implementar una capa de inferencia local eficiente y auditable que use la GPU solo en tareas donde aporta valor: extracción semántica, clasificación controlada, síntesis de evidencia y explicación comercial. La aplicación debe funcionar sin IA, degradarse claramente y nunca presentar una salida del modelo como hecho sin evidencia.

Hardware objetivo principal: NVIDIA RTX 4060 con 8 GB de VRAM, sin hardcodear el modelo ni asumir que CUDA siempre está disponible.

## Runtimes

1. Ollama como integración predeterminada mediante su API local.
2. Abstracción de provider para compatibilidad posterior con llama.cpp server + CUDA.
3. LLM Fit se usa como ayuda externa/configurable para evaluar compatibilidad; no lo conviertas en dependencia central ni ejecutes cambios automáticos del sistema.
4. Detecta runtimes/modelos instalados; no descargues modelos sin acción explícita del usuario.

## Perfiles de tareas

Define contratos separados para:

- `evidence_extraction`: extraer claims y enlazarlos a fragmentos;
- `business_classification`: vertical, servicios y señales comerciales;
- `finding_synthesis`: resumir hallazgos deterministas sin alterarlos;
- `opportunity_reasoning`: análisis estructurado posterior;
- `language_detection/translation` cuando aporte valor;
- `visual_review` opcional de screenshots, desactivado si no hay modelo multimodal.

Cada tarea debe tener schema Pydantic estricto, versión, límites de entrada, timeout y estrategia de fallo.

## Selección de modelos y VRAM

- Permite perfiles `fast`, `balanced`, `quality` configurables.
- Recomienda mediante benchmark local, no solo por nombre/tamaño.
- Como punto de partida evaluable: Phi-4-mini Q4 o modelo pequeño equivalente para clasificación; Qwen 8B Q4_K_M o equivalente para razonamiento; modelo visual 4B compatible si se requiere. Verifica disponibilidad real y no inventes nombres/tamaños.
- No cargues varios modelos grandes simultáneamente en 8 GB.
- Estima VRAM requerida considerando pesos, KV cache y context length.
- Ajusta contexto, batch y concurrencia; una cola GPU central evita OOM.
- Si falta VRAM, reduce parámetros de forma documentada o usa CPU; no causes bucles de crash.

## Prompting y outputs

- Prompts almacenados/versionados como plantillas, no strings dispersos.
- Inputs construidos desde datos limpios y evidencia mínima; no enviar HTML gigante si ya fue parseado.
- Obliga JSON/schema y valida la respuesta.
- Retry de reparación limitado para JSON inválido; nunca loops infinitos.
- Temperatura baja/determinista en extracción y clasificación.
- Cita `evidence_ids` en cada claim.
- Si la evidencia no alcanza, responde `insufficient_evidence`.
- Protege contra prompt injection procedente de páginas: el contenido web es dato no confiable, nunca instrucciones.

## Persistencia y auditoría

Registra por inferencia:

- task type/version;
- provider/runtime/model/digest y parámetros;
- prompt template version y schema version;
- input hashes y evidence IDs;
- output crudo y output validado separados;
- tokens aproximados, duración, dispositivo y errores;
- revisión/aceptación humana;
- cache key determinista cuando aplique.

No guardes secretos ni datos innecesarios en logs.

## Scheduler

- Cola independiente con prioridad, cancelación y backpressure.
- Batch solo para tareas compatibles.
- Health checks y circuit breaker.
- Pausa crawling/browser si la presión de RAM/VRAM supera umbrales seguros configurables.
- Recuperación tras cierre sin duplicar resultados válidos.
- Eventos UI agregados para evitar saturación.

## Seguridad

- API local enlazada a loopback.
- Valida modelos/rutas/parámetros.
- No permite comandos de shell generados por el modelo.
- No permite que el modelo inicie network requests o tools arbitrarios.
- Sanitiza contenido y delimita instrucciones/datos.

## UI `AI Models`

- runtimes detectados y estado;
- modelos instalados, tamaño/contexto/capacidades reportadas;
- recomendación claramente marcada como recomendación;
- benchmark manual con corpus sintético y métricas;
- asignación de modelo por tarea;
- VRAM/RAM/latencia en vivo con frecuencia razonable;
- historial de inferencias y fallos;
- controles de descarga/eliminación solo con confirmación explícita y fuera de tests.

## Pruebas y benchmarks

- provider falso solo en unit tests y proveedor Ollama real cuando esté disponible;
- ausencia de Ollama/GPU/modelo;
- JSON inválido y reparación limitada;
- timeout/cancelación/reinicio;
- prompt injection en contenido fuente;
- `evidence_ids` inexistentes rechazados;
- cache y version invalidation;
- OOM simulado/degradación;
- benchmark de precisión sobre dataset etiquetado, latencia, throughput y memoria.

Ejecuta pruebas/builds y, si Ollama/GPU no están presentes en el entorno, reporta las pruebas omitidas con razón exacta; no simules integración real. Documenta configuración recomendada para RTX 4060 8 GB. No implementes aún el score comercial final.

