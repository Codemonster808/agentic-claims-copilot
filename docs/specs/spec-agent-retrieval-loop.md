# Spec — Agentic retrieval loop

## Objetivo de negocio

Responder una pregunta de un claim de seguro citando las cláusulas de
póliza reales que la respaldan, sin fabricar una respuesta cuando la
evidencia recuperada no alcanza — y sin gastar presupuesto de LLM sin
límite intentándolo. Esto reemplaza un flujo manual de horas de un
adjuster cruzando referencias contra el documento de póliza.

## Fuentes de entrada

- `data/claims.json` (o el payload de `POST /ask`): `{claim_id, question,
  token_budget}` — `token_budget` por defecto `DEFAULT_TOKEN_BUDGET = 2000`.
- Colección vectorial `policy_clauses` (ver `docs/data-dictionary.md`),
  poblada por `src/ingestion/index_docs.py` / `src/transformation/reindex.py`.
- `claims-token-budget` (DynamoDB) — estado del contador de gasto por
  `claim_id`, acumulativo entre llamadas.

## Transformaciones

1. **Plan** (`src/models/agent_loop.py::plan_query`) — en la primera
   iteración, la query es la pregunta del claim tal cual. En reintentos, el
   LLM reescribe la query con terminología formal de póliza (coverage,
   exclusion, deductible, limit) para cerrar la brecha de vocabulario entre
   cómo describe el claimant y cómo está redactada la cláusula.
2. **Gate** (`src/orchestration/statemachine.py::gate_iteration`) — antes
   de cada iteración, una ejecución real de Step Functions
   (`asl/agent_loop_gate.json`) incrementa atómicamente
   `claims-token-budget.tokens_spent` en `COST_PER_ITERATION = 300` y
   decide `within_budget`. Si no, la claim termina en `budget_exhausted`
   sin llegar a Plan/Tool/Observe de esa iteración.
3. **Tool** (`store.query`) — busca `top_k=5` en `policy_clauses` para la
   query de esa iteración.
4. **Observe** — compara la distancia top-1 de esta iteración contra la
   mejor vista hasta ahora (comparación válida solo dentro de la misma
   query). Si no mejora y ya hubo al menos una iteración previa, el loop
   se detiene.
5. **Fusión de evidencia** (`_reciprocal_rank_fusion`, `RRF_K = 60`) —
   combina los resultados de *todas* las iteraciones por el rank de cada
   ítem dentro de su propia query, nunca por distancia cruda entre
   queries distintas (ver `docs/adr/0001-rrf-vs-raw-distance-fusion.md`).
6. **Answer** — el LLM genera la respuesta usando únicamente las 3
   cláusulas mejor fusionadas; las citas vienen de esas cláusulas, nunca
   de texto libre del LLM.
7. **Retry LLM** (`_call_llm_with_retry`) — `PermanentLLMError` va directo
   al DLQ; `TransientLLMError` reintenta hasta `MAX_TRANSIENT_ATTEMPTS = 2`
   veces con backoff exponencial desde `TRANSIENT_BACKOFF_BASE_S = 0.5`s.

Límite duro: `MAX_ITERATIONS = 3`.

## Salida esperada

**Esquema** (retorno de `run_agentic_loop`, y persistido en
`claims-answers` cuando `status == "answered"`):

```json
{
  "status": "answered | budget_exhausted | failed",
  "iterations": "int",
  "trace": "[{iteration, query, top1_distance, improved}]",
  "answer": "string (solo si answered)",
  "citations": "[clause_id] (solo si answered)",
  "tokens_spent": "int (solo si budget_exhausted)",
  "failure_type": "permanent | transient_retries_exhausted (solo si failed)"
}
```

**Granularidad:** una fila/respuesta por `claim_id`.

**SLA de frescura:** no aplica frescura de datos aquí (es un flujo
sincrónico por request); el límite operacional es `MAX_ITERATIONS = 3`
iteraciones y el timeout de 15s por ejecución de Step Functions
(`gate_iteration(..., timeout_s=15)`).

## Casos borde

- `token_budget` menor al costo de una iteración (`300`) → `budget_exhausted`
  en la primera iteración, sin `citations`.
- 20 llamadas concurrentes al gate sobre el mismo `claim_id` → el contador
  debe reflejar exactamente `20 * 300 = 6000`, nunca menos (condición de
  carrera evitada por `ADD` atómico, no read-modify-write).
- Iteración que no mejora el top-1 distance tras la primera → el loop se
  detiene temprano en vez de agotar las 3 iteraciones sin motivo.
- Error permanente del LLM en cualquier iteración → DLQ inmediato,
  `failure_type=permanent`, sin reintentos.
- Error transitorio del LLM → hasta 2 intentos con backoff; si el segundo
  también falla, DLQ con `failure_type=transient_retries_exhausted`.
- Vector store sin resultados (`retrieved` vacío) → `top1_distance =
  float("inf")`, tratado como "no mejoró".

## Criterios de aceptación

| # | Criterio | Escenario BDD |
|---|---|---|
| 1 | Un claim con budget agotado nunca lleva `citations` en su resultado, y su mensaje llega a `claims-agent-dlq` | `features/agent-budget.feature`: "Budget exhausted before the first iteration completes" |
| 2 | 20 incrementos concurrentes del contador de budget sobre el mismo `claim_id` suman exactamente 6000 | `features/agent-budget.feature`: "Twenty concurrent gate calls never lose an increment" |
| 3 | La fusión RRF premia el ítem consistentemente mejor rankeado, no el de menor distancia cruda | `features/evidence-fusion.feature`: "RRF rewards the item ranked first in every query" |
| 4 | La fusión RRF no se deja dominar por la escala de distancia de una sola query | `features/evidence-fusion.feature`: "A query with uniformly smaller distances must not dominate the merge" |

Medido también (no re-verificado en cada corrida de BDD, ver README): 0.167
vs. 0.133 precision@3 con MiniMax M3 real, 2.7 iteraciones promedio por
claim resuelto.
