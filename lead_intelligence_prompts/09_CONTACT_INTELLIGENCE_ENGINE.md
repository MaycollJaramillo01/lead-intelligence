# PROMPT 09 — CONTACT INTELLIGENCE, DECISION MAKERS Y VALIDACIÓN

Continúa sobre el proyecto existente. Inspecciona businesses, pages, evidence y website findings. No compres datos, no inventes personas y no envíes mensajes ni formularios.

## Objetivo

Implementar un motor que encuentre, normalice, consolide, valide y priorice **canales de contacto empresariales publicados o aportados legítimamente**, manteniendo procedencia, consentimiento/contexto y nivel de confianza.

## Alcance permitido

- teléfonos empresariales publicados;
- emails empresariales publicados;
- formularios de contacto y booking detectados sin enviarlos;
- perfiles sociales oficiales;
- páginas de equipo/About;
- decision makers declarados públicamente en fuentes empresariales legítimas;
- importaciones del usuario con fuente registrada.

No uses data breaches, credenciales, áreas privadas, scraping de perfiles restringidos ni generación especulativa de emails presentada como hecho.

## Modelo de contacto

Para cada punto de contacto guarda:

```text
contact_point_id
business_id/location_id/person_id opcional
type y normalized_value
raw_value
source/evidence
first_seen_at / last_seen_at
published_context
validation_status
validation_method
validated_at
confidence
role_or_department
is_primary
do_not_contact / suppression_reason
```

Separa contactos de empresa, ubicación y persona. No asumas que un email genérico pertenece al dueño.

## Extracción y consolidación

- Extrae de páginas adquiridas, JSON-LD, `mailto:`, `tel:`, footer/header/contact/About y fuentes estructuradas.
- Normaliza teléfonos a E.164 solo con contexto suficiente.
- Detecta extensiones, toll-free, móvil/fijo cuando sea confiable.
- Normaliza emails sin reglas destructivas.
- Identifica emails de rol (`info@`, `sales@`) y personales publicados.
- Canonicaliza formularios y perfiles sociales.
- Deduplica conservando todas las fuentes.
- Señala conflictos y datos antiguos.

## Validación responsable

Implementa validaciones no intrusivas:

- estructura y librería de numeración para teléfonos;
- dominio/MX para emails con caché y timeout;
- sintaxis, disposable/role/catch-all como señales;
- HTTP status y estructura de formularios sin enviar;
- consistencia entre dominio del email y website;
- frescura y coincidencia entre varias fuentes.

No hagas SMTP probing intrusivo por defecto, no llames números, no envíes emails, no completes formularios y no uses servicios pagos sin configuración/consentimiento.

Estados explícitos: `observed`, `structurally_valid`, `domain_valid`, `likely_valid`, `invalid`, `unknown`, `stale`, `suppressed`. Evita usar “validado” si solo pasó sintaxis.

## Decision makers

- Solo crea personas desde nombres/roles publicados con evidencia.
- Separa nombre, cargo y relación con la empresa.
- Registra contexto y fecha.
- Nunca deduzcas identidad latina por nombre.
- Un email generado por patrón debe marcarse `hypothesis`, mantenerse fuera de exportes predeterminados y jamás considerarse confirmado.

## Contactability score

Calcula un subscore explicable basado en disponibilidad, calidad, validación, relevancia del rol, frescura, fuente y supresiones. No mezcles aún este score con el lead score global.

## Compliance y supresión

- Lista local de exclusión/DNC para email, teléfono, dominio y empresa.
- Motivo, fuente y fecha de supresión.
- Propagación a exportes y vistas.
- Importación/exportación segura de suppressions.
- La plataforma no debe iniciar outreach en esta fase.

## UI

- Panel de contactos con estado exacto, fuente y fecha.
- Acciones de validar/revisar/marcar principal/suprimir.
- Conflictos visibles.
- Filtros por canal, calidad, rol y frescura.
- Cola de revisión de decision makers ambiguos.
- No mostrar hipótesis como contactos confirmados.

## Pruebas obligatorias

- formatos telefónicos de EE. UU. e internacionales;
- email válido, inválido, IDN, disposable, role y dominio sin MX;
- múltiples fuentes/conflictos;
- formulario detectado sin submit;
- persona/rol con evidencia;
- hipótesis de email aislada;
- suppression aplicada a UI/export query;
- timeouts DNS y modo offline;
- revalidación/frescura;
- no realizar llamadas ni envíos durante tests.

Ejecuta pruebas/builds, corrige fallos y documenta exactamente qué significa cada estado de validación. No implementes outreach ni scoring final.

