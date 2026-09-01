# Data dictionary — agentic-claims-copilot

Glossary of every dataset this repo reads or writes, with columns/fields,
types, grain, and lineage (which file produces / consumes it). All AWS-shaped
resources (buckets, tables, queues, functions) are declared in
`scripts/resources.json` and created idempotently by `scripts/bootstrap.py`.

No `notebooks/` or `dbt/` directory exists in this repo, deliberately — see
the note at the bottom of this file.

---

## DynamoDB tables

### `claims-answers`

Grain: one row per `claim_id` that reached a terminal `answered` status.

| Column | Type | Description |
|---|---|---|
| `claim_id` | S (partition key) | The claim identifier the loop was run with. |
| `answer` | S | LLM-generated answer text, truncated to 2000 chars on write. |
| `citations` | S (JSON-encoded array of strings) | Clause IDs actually retrieved and fused via RRF — never invented by the LLM. |
| `iterations` | N | Number of loop iterations the claim took (1 to `MAX_ITERATIONS`). |
| `trace` | S (JSON-encoded array of objects) | Per-iteration `{iteration, query, top1_distance, improved}`. |

**Lineage:** written by `src/models/agent_loop.py::_write_answer` (called
from `run_agentic_loop` only on the `answered` path). Read by
`src/serving/api.py::trace` (`GET /trace/{claim_id}`) and archived nightly
to `s3://claims-traces/archive/` (see below) by
`src/transformation/reindex.py::archive_traces`.

### `claims-token-budget`

Grain: one row per `claim_id` that has had at least one budget-gate check.

| Column | Type | Description |
|---|---|---|
| `claim_id` | S (partition key) | Same claim identifier as `claims-answers`. |
| `tokens_spent` | N | Cumulative atomic counter — incremented by `COST_PER_ITERATION` (300) on every gate call, via `UpdateExpression: ADD`, never read-modify-write. |

**Lineage:** written by `src/orchestration/lambdas/check_budget.py::handler`,
invoked once per loop iteration by the `CheckBudget` state in
`asl/agent_loop_gate.json`, itself started by
`src/orchestration/statemachine.py::gate_iteration`. See
`docs/adr/0002-atomic-budget-counter.md` for why this is a DynamoDB atomic
add and not Step Functions state or an in-process counter.

---

## Vector store

### `policy_clauses` (Chroma collection / Pinecone index)

Grain: one vector per policy clause (not per policy, not per chunk-of-N —
retrieval granularity matches citation granularity 1:1).

| Field | Type | Description |
|---|---|---|
| `id` | string | The clause ID, e.g. `POL-003-2.7`. |
| `embedding` | float[384] | `all-MiniLM-L6-v2` sentence-transformer embedding of `f"{title}\n{text}"`. |
| `metadata.policy_id` | string | Owning policy, e.g. `POL-003`. |
| `metadata.clause_id` | string | Same as `id`, duplicated into metadata for filtering. |
| `metadata.title` | string | Clause title, e.g. "Windstorm and Hail". |

**Lineage:** written by `src/ingestion/index_docs.py::chunk_by_clause` +
`main` (full re-embed of everything under `--in`). Incrementally updated by
`src/transformation/reindex.py::reindex_changed_docs`, which only re-embeds
policies whose content hash (see manifest below) changed since the last
run. Read by `src/models/agent_loop.py` (`store.query`, top-k per loop
iteration) and by `src/models/agent_loop.py::run_single_shot_baseline`.
Backend selected by `VECTOR_BACKEND` (`chroma` default, local, persisted at
`.chroma/`; `pinecone` real, costs money — see `CLAUDE.md` §5).

---

## SQS

### `claims-agent-dlq`

Grain: one message per claim that failed to resolve, with two distinct
message shapes distinguished by the presence of `failure_type`:

| Field | Type | Present when |
|---|---|---|
| `claim_id` | string | always |
| `reason` | string | always — human-readable reason |
| `failure_type` | string (`"permanent"` \| `"transient_retries_exhausted"`) | only on the LLM-call-failure path |

**Lineage:** budget-exhaustion messages (no `failure_type`) are written by
`src/orchestration/lambdas/send_to_dlq.py::handler`, invoked by the
`SendToDLQ` state in `asl/agent_loop_gate.json`. LLM-call-failure messages
(with `failure_type`) are written directly from Python by
`src/models/agent_loop.py::_send_to_dlq`, called from
`_call_llm_with_retry` — this path bypasses Step Functions because the
failure happens inside the LLM call itself, not at the budget-gate Choice.
See `docs/adr/0003-permanent-vs-transient-llm-errors.md`.

---

## S3

### `s3://claims-docs/`

| Key pattern | Format | Description |
|---|---|---|
| `{policy_id}.txt` | plain text | Raw uploaded policy document, one file per policy. |
| `_reindex_manifest.json` | JSON | `{policy_id: sha256(json.dumps(policy, sort_keys=True))}` — tracks which policies have already been embedded, so `reindex_changed_docs` only re-embeds what changed. |

**Lineage:** `.txt` files written by `src/ingestion/ingest.py` (reading
local files produced by `src/ingestion/data_gen.py`). The manifest is
read/written by `src/transformation/reindex.py::_load_manifest` /
`_save_manifest`.

### `s3://claims-traces/archive/`

Grain: one Parquet dataset (coalesced to 1 file per run, overwrite mode) —
a full dump of `claims-answers` at archive time, not an append log.

| Column | Type | Description |
|---|---|---|
| `claim_id` | string | |
| `answer` | string | |
| `citations` | string (JSON-encoded) | |
| `iterations` | int | |

**Lineage:** written by `src/transformation/reindex.py::archive_traces` via
PySpark (`spark.hadoop.fs.s3a.*` config against MiniStack's S3), scanning
`claims-answers` with a DynamoDB paginator. Run nightly alongside the
incremental re-embed (`make reindex` / `src/transformation/reindex.py`
`main()` runs both in sequence).

---

## Local / intermediate files (not in S3, gitignored under `data/`)

| File | Format | Description |
|---|---|---|
| `data/policies/{policy_id}.txt` | plain text | Same rendered text later uploaded to `s3://claims-docs/`. |
| `data/_policy_clauses.json` | JSON | Full structured policy+clause data (`policy_id`, `clauses[].{clause_id, topic, title, text}`) — the source `index_docs.py`/`reindex.py` chunk from. |
| `data/claims.json` | JSON | Golden eval set: `{claim_id, policy_id, question, ground_truth_clauses}` per claim. `ground_truth_clauses` is embedded at generation time since `src/ingestion/data_gen.py` controls the mapping. |

**Lineage:** all three produced by `src/ingestion/data_gen.py --seed <n>`
(default seed 42; e2e test uses seed 77 for isolation). `claims.json` is
what `scripts/eval.py` and `tests/data_quality/test_e2e.py` measure
citation precision against.

---

## Lambda functions (deployed, not a dataset but referenced throughout)

| Name | Handler file | Purpose |
|---|---|---|
| `claims-check-budget` | `src/orchestration/lambdas/check_budget.py` | Atomic `ADD` to `claims-token-budget.tokens_spent`, returns `within_budget`. |
| `claims-send-to-dlq` | `src/orchestration/lambdas/send_to_dlq.py` | Sends a budget-exhausted claim to `claims-agent-dlq`. |

Deployed/updated by `src/orchestration/statemachine.py::deploy`, which also
creates/updates the `claims-agent-loop-gate` Step Functions state machine
from `asl/agent_loop_gate.json`.

---

## Deliberate omission: no `notebooks/`, no `dbt/`

Neither exists in this repo, on purpose. There's no exploratory notebook
work behind any of these datasets and no dbt models — everything above is
produced by a plain Python script or Lambda. Creating empty `notebooks/`
or `dbt/` folders just to resemble a template would be exactly the kind of
decorative artifact this portfolio is built to avoid. If a notebook is
added later for genuine exploration, the rule in `CLAUDE.md` §7 applies:
notebooks never feed a Makefile target or CI, ever.
