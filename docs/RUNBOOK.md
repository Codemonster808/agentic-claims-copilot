# Runbook — aprender el loop agentic (P3)

El más abstracto: déjalo **al final** (después de P1, P4, P2, P5). Complementa `docs/BUILD_GUIDE.md`.

Escala (20 pólizas / 10 claims) ya es aprendible. Itera con `LLM_PROVIDER=fake`. `make eval` + MiniMax es para las cifras del README.

---

## 0. Setup por terminal

```bash
cd /home/lesaint/Documentos/life_plans/agentic-claims-copilot
source env.sh
docker compose up -d
make check-env
python3 scripts/bootstrap.py
python3 src/statemachine.py     # Lambdas + ASL budget/DLQ
python3 scripts/aws_inspect.py sfn  # debe listar claims-agent-loop-gate
```

---

## 1. Flujo paso a paso

### 1.1 Pólizas, claims, índice

```bash
python3 src/data_gen.py --policies 20 --claims 10 --out data --seed 42
ls data/policies | head
python3 src/ingest.py --in data/policies
python3 scripts/aws_inspect.py s3          # claims-docs
VECTOR_BACKEND=chroma python3 src/index_docs.py --in data
```

Cada claim en `data/claims.json` trae las cláusulas-gold. Citation precision se mide contra eso.

### 1.2 Un claim con budget holgado (fake LLM)

```bash
python3 - <<'PY'
import json, uuid, sys
sys.path.insert(0, "src")
from agent_loop import run_agentic_loop
claims = json.load(open("data/claims.json"))
c = claims[0]
cid = f"learn-{uuid.uuid4().hex[:8]}"
print("question:", c.get("question") or c.get("claim_text") or c)
print(run_agentic_loop(cid, c.get("question") or c.get("text") or str(c), token_budget=2000))
PY
python3 scripts/aws_inspect.py ddb
python3 scripts/aws_inspect.py sfn
```

Si el campo no se llama `question`, abre `data/claims.json` y usa la clave real (`text` / `prompt`).

**Qué inspeccionar:** `claims-answers` (respuesta + trace de iteraciones); `claims-token-budget` (contador atómico); executions SUCCEEDED del state machine.

### 1.3 Single-shot vs agentic (eval)

```bash
VECTOR_BACKEND=chroma LLM_PROVIDER=fake python3 src/eval.py --mode single-shot
VECTOR_BACKEND=chroma LLM_PROVIDER=fake python3 src/eval.py --mode agentic
```

El loop **no** fusiona por distancia cruda entre queries (eso empeoró precision). Usa **RRF**. Fake LLM no va a replicar el 0.133→0.167 de MiniMax; el mecanismo sí.

---

## 2. Explorar con AWS CLI

`aws` respeta `AWS_ENDPOINT_URL` (exportado por `env.sh`), sin flags extra.

```bash
# S3 — documentos de la póliza indexados
aws s3 ls s3://claims-docs/ --recursive

# DynamoDB — respuesta + trace de iteraciones, y el contador de budget
aws dynamodb scan --table-name claims-answers --max-items 3
aws dynamodb get-item --table-name claims-token-budget --key '{"claim_id": {"S": "<tu-claim_id>"}}'

# SQS — la DLQ real después de forzar budget_exhausted (sección 2)
QUEUE_URL=$(aws sqs get-queue-url --queue-name claims-agent-dlq --query QueueUrl --output text)
aws sqs get-queue-attributes --queue-url "$QUEUE_URL" --attribute-names All
aws sqs receive-message --queue-url "$QUEUE_URL" --max-number-of-messages 1

# Lambda — las dos funciones que Step Functions invoca dentro del loop
aws lambda get-function --function-name claims-check-budget --query 'Configuration.[State,Runtime]'
aws lambda get-function --function-name claims-send-to-dlq --query 'Configuration.[State,Runtime]'

# Step Functions — el Choice que decide seguir, parar, o mandar a DLQ
SM_ARN=$(aws stepfunctions list-state-machines --query "stateMachines[?name=='claims-agent-loop-gate'].stateMachineArn | [0]" --output text)
EXEC_ARN=$(aws stepfunctions list-executions --state-machine-arn "$SM_ARN" --max-results 1 --query "executions[0].executionArn" --output text)
aws stepfunctions get-execution-history --execution-arn "$EXEC_ARN"
```

**Qué mirar que `aws_inspect.py` no te muestra:** el `ResultPath` real en `get-execution-history` (`$.dlq_result`) — ahí se ve por qué la tarea DLQ no pisa `tokens_spent` al escribir su resultado, que es justo el bug histórico de la tabla de errores.

---

## 3. Romper a propósito — budget 1 → DLQ

Usa un `claim_id` **nuevo** (el contador en DynamoDB es acumulativo):

```bash
python3 - <<'PY'
import uuid, sys
sys.path.insert(0, "src")
from agent_loop import run_agentic_loop
cid = f"broke-{uuid.uuid4().hex[:8]}"
print(run_agentic_loop(cid, "Is water damage covered?", token_budget=1))
PY
python3 scripts/aws_inspect.py sqs
python3 scripts/aws_inspect.py ddb
```

**Esperado:** `status=budget_exhausted`, **sin** `citations` fabricadas, mensaje en `claims-agent-dlq`. Step Functions tomó el branch Choice → SendToDLQ (`ResultPath: $.dlq_result` para no pisar `tokens_spent`).

---

## 4. Errores

| Error | Significado |
|---|---|
| `QueueDoesNotExist` en DLQ | Bootstrap no corrió **o** env.sh ausente |
| Gate timeout | `statemachine.py` no desplegó Lambdas; MiniStack caído |
| Budget exhausted en el primer claim “bueno” | Reusaste el mismo `claim_id`. Siempre uuid nuevo |
| Precision peor con el loop | Estarías fusionando por distancia cross-query. El código ya usa RRF |
| `tokens_spent` falso | Bug viejo de ResultPath en la tarea DLQ — ya corregido |

---

## 5. Ejercicios

**1. Lee el mensaje real en la DLQ, no solo que llegó**

Fuerza `budget_exhausted` (sección 3), luego `aws sqs receive-message --queue-url $QUEUE_URL --max-number-of-messages 1`.

<details><summary>Verificar</summary>

El `Body` trae el `claim_id` y el motivo (`budget_exhausted`), no un mensaje genérico — es lo que un consumer downstream (o un humano en on-call) usaría para decidir si reintentar con más budget o descartar. Compáralo contra el `status` que `run_agentic_loop` devolvió en Python: deberían coincidir, porque ambos vienen del mismo estado de Step Functions.
</details>

**2. Encuentra el `ResultPath` que evita pisar `tokens_spent`**

Corre un claim exitoso y otro que llegue a DLQ, y compara sus dos historiales con `aws stepfunctions get-execution-history` (sección 2) — busca dónde difieren.

<details><summary>Verificar</summary>

En el camino a DLQ, la tarea `claims-send-to-dlq` escribe su resultado en `$.dlq_result` (no en la raíz del output) — así el campo `tokens_spent` que venía acumulando el loop sigue disponible después de esa tarea. Si el ASL escribiera el resultado en la raíz, `tokens_spent` se perdería justo en el paso que más importa auditar. Es la fila `tokens_spent falso` de la tabla de errores, ahora visto en el JSON real en vez de en la descripción.
</details>

**3. Confirma con DynamoDB que el budget es un contador atómico, no un valor leído-modificado-escrito**

Lanza dos claims con el mismo `claim_id` casi al mismo tiempo (dos procesos en paralelo) y revisa `aws dynamodb get-item --table-name claims-token-budget --key '{"claim_id":{"S":"<id>"}}'` al final.

<details><summary>Verificar</summary>

El valor final refleja **ambos** decrementos, no solo uno — confirma que `claims-check-budget` usa una actualización atómica (`UpdateExpression` con `ADD`, no get-then-put), que es justo lo que evita una condición de carrera si dos invocaciones del loop tocan el mismo budget a la vez.
</details>

---

## 6. Quality report

```bash
make e2e
cat docs/quality-report.md
```

El check `budget_exhaustion_reaches_real_dlq_via_step_functions` es el que demuestra que el DLQ no es un print.

---

## 7. Cerrar

```bash
docker compose down
```
