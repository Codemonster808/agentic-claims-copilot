# ADR 0003 — Errores LLM permanentes vs transitorios

## Contexto

Un 4xx de API no se arregla reintentando. Un timeout sí. Mandar todo al
DLQ o reintentar todo es igual de malo.

## Decisión

`PermanentLLMError` → DLQ inmediato (`failure_type` en el mensaje).
`TransientLLMError` → `MAX_TRANSIENT_ATTEMPTS=2`, backoff
`TRANSIENT_BACKOFF_BASE_S=0.5`. Fake provider puede inyectar ambos.

## Alternativas consideradas

- **Siempre retry N veces**: quema budget y no saca un JSON inválido.
- **Nunca retry**: un blip de red manda claims recuperables al DLQ.

## Consecuencias

El DLQ distingue `budget_exhausted` (sin `citations`) de fallo de LLM.
No fabricar respuesta por agotar reintentos.
