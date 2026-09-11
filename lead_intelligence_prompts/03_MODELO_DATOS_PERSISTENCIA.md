# PROMPT 03 — MODELO DE DATOS, PROCEDENCIA Y PERSISTENCIA

Continúa sobre la misma aplicación. Inspecciona primero contratos, migraciones, IPC y UI existentes. No reconstruyas las fases anteriores ni reemplaces SQLite/DuckDB.

## Objetivo

Diseñar e implementar el modelo de datos central de Lead Intelligence. Debe separar claramente:

- datos operacionales y estado mutable en SQLite;
- datasets analíticos y resultados voluminosos en DuckDB/Parquet;
- archivos/artefactos en almacenamiento local controlado;
- procedencia, evidencia e historial como datos de primera clase.

El modelo debe poder evolucionar sin perder trazabilidad ni mezclar hechos observados con inferencias.

## Principios obligatorios

1. Cada dato adquirido debe registrar fuente, URL o identificador, momento de adquisición, método, extractor y confianza.
2. Un valor normalizado no elimina el valor crudo.
3. Una inferencia de IA nunca sustituye un hecho; se almacena por separado con modelo, versión, prompt/schema, fecha y confianza.
4. Los scores guardan versión del modelo y desglose de factores.
5. Las eliminaciones importantes deben ser recuperables o explícitamente auditadas.
6. Timestamps en UTC y formato consistente.
7. IDs internos estables (UUIDv7/ULID o decisión justificada); no usar nombre/dominio como primary key.
8. Migraciones hacia adelante reproducibles, con respaldo antes de cambios destructivos.

## Entidades mínimas

Modela, relaciona e indexa:

- `projects` / espacios de trabajo locales;
- `campaigns` y criterios de discovery;
- `icp_profiles` y versiones;
- `source_connectors`, configuraciones y límites;
- `source_runs`, consultas y páginas adquiridas;
- `raw_records` inmutables;
- `businesses` canónicos;
- nombres alternativos, ubicaciones, áreas de servicio, categorías y servicios;
- websites, dominios, URLs y páginas;
- phones, emails, forms, social profiles y decision makers;
- `evidence_items` con tipo, claim, valor, fuente, fragmento/hash y confianza;
- `latino_assessments` separados de evidencia;
- website audits, tecnologías, conversion elements y findings;
- contact validations;
- AI inference runs y outputs estructurados;
- scoring model definitions/versiones;
- score runs, component scores y explanations;
- jobs, tasks, attempts, checkpoints, eventos, errores y métricas;
- datasets, snapshots, exports y lineage;
- tags, notes, review decisions y audit log.

## Contratos y estados

Define enums/versiones explícitas para, como mínimo:

```text
business_status
source_run_status
job_status
evidence_type
evidence_strength
latino_status
contact_type
validation_status
website_finding_severity
inference_status
lead_stage
export_status
```

Estados deben tener transiciones válidas; evita strings libres.

## Persistencia

- Implementa repositorios/servicios; la UI no ejecuta SQL.
- Usa transacciones para operaciones relacionadas.
- Configura foreign keys, índices y constraints reales.
- Añade búsqueda textual apropiada en SQLite donde aporte valor.
- Usa DuckDB para consultas analíticas/snapshots y Parquet particionado con esquema explícito.
- Define política de rutas portables en Windows y no uses rutas absolutas hardcodeadas.
- Implementa backup, integrity check y recuperación controlada de una base corrupta o migración fallida.
- Añade paginación keyset/cursor para listas grandes; evita cargar todo en memoria.

## Contratos compartidos

Genera esquemas canónicos en Pydantic y tipos TypeScript compatibles mediante generación o validación automatizada. Documenta cuál capa es la fuente de verdad. Rust debe validar mensajes IPC y no confiar en payloads del frontend/worker.

Incluye un `data_contract_version` y estrategia de compatibilidad.

## Datos de prueba

Crea factories/fixtures sintéticas identificadas claramente como test data. No incluyas personas reales ni presentes ejemplos como resultados del producto.

## Pruebas obligatorias

- migración desde base vacía;
- migraciones repetibles/idempotencia donde corresponda;
- constraints y foreign keys;
- rollback transaccional;
- round-trip Pydantic ↔ JSON ↔ Rust ↔ TypeScript;
- inserción y consulta paginada;
- escritura/lectura DuckDB y Parquet;
- lineage de un valor normalizado hasta el raw record;
- backup + restore en archivo temporal;
- concurrencia básica y bloqueo de SQLite;
- pruebas de rendimiento con dataset sintético razonable.

## Integración UI mínima

Conecta `Businesses` y `Datasets` a consultas reales. Si no hay datos, muestra cero/empty state. Permite abrir una entidad de prueba únicamente en modo test/dev claramente marcado para validar el flujo.

## Entrega

Entrega diagrama lógico o documentación equivalente, diccionario de datos, migraciones, estrategia de procedencia, archivos modificados, pruebas y resultados. Corrige errores antes de cerrar. No implementes aún discovery, scraping ni scoring comercial.

