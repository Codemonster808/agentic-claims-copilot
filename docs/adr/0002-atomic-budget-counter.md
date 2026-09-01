# ADR 0002 — Contador de budget atómico (DynamoDB ADD)

## Contexto

Cada iteración del loop es un proceso distinto (Step Functions → Lambda).
No hay memoria compartida. El budget es `tokens_spent` vs `token_budget`.

## Decisión

Lambda `check_budget`: `UpdateExpression: ADD tokens_spent :inc` con
`COST_PER_ITERATION=300`. La decisión `within_budget` usa el valor
devuelto por esa escritura, no un Get previo.

## Alternativas consideradas

- **Read-modify-write**: 20 gates concurrentes sub-cuentan (lost update).
  El test de atomicidad exige exactamente 6000.
- **Estado nativo de Step Functions**: el contador viviría en el input
  JSON; retries/parallel branches lo duplican o lo pierden.
- **Contador in-process**: muere entre invocaciones.

## Consecuencias

El budget es por `claim_id` acumulativo: no reutilizar IDs entre corridas
de eval/test. `asl/agent_loop_gate.json` es la fuente de verdad del gate.
