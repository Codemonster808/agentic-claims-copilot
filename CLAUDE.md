# CLAUDE.md — agentic-claims-copilot

Operating constitution for this repo. Not architecture — for that, read
`docs/architecture.md` and `docs/adr/`. This file is what an agent (human
or LLM) needs before touching code: what "correct" means here, the exact
commands, the naming conventions, the rules that aren't obvious from the
code, and what not to touch without asking.

## 1. Domain context

This is an agentic retrieval loop for insurance claims: given a claim
description, find the policy clauses that answer it and cite them — under
a hard token budget, with a dead-letter queue for cases the loop can't
resolve within that budget. "Correct" in this repo means:

- **A claim's terminal status is always one of `answered`, `budget_exhausted`,
  or `failed`** — never left hanging, never a silent timeout. See
  `src/models/agent_loop.py::run_agentic_loop`.
- **`budget_exhausted` never carries a `citations` key.** The loop must not
  fabricate an answer just because it ran out of budget — `tests/integration/test_agent_loop.py::test_budget_exhaustion_sends_to_dlq_not_a_fabricated_answer`
  asserts this directly.
- **Citations are never invented by the LLM.** `generate_answer()` builds
  the citation list from the clause IDs actually retrieved from the vector
  store, before the LLM ever sees them — the LLM is asked to *use* those
  clauses, not to name its own.
- **The per-claim token budget counter is atomic**, never
  read-modify-write. It's a DynamoDB `UpdateExpression: ADD` inside a
  Lambda invoked from Step Functions once per loop iteration — because
  each iteration is a separate process/invocation with no shared memory.
  20 concurrent gate calls on the same `claim_id` must sum to exactly
  `20 * COST_PER_ITERATION` (currently `20 * 300 = 6000`), never less. See
  `docs/adr/0002-atomic-budget-counter.md`.
- **Evidence across loop iterations is fused by rank (RRF), never by raw
  embedding distance.** A prior version merged by min-distance and it
  measurably hurt precision (0.067 vs. 0.133, worse than not looping at
  all) — see `docs/adr/0001-rrf-vs-raw-distance-fusion.md`. Don't
  "simplify" `_reciprocal_rank_fusion` back to a distance sort.
- **Permanent LLM errors never retry; transient ones get bounded retries.**
  A malformed/rejected request (`PermanentLLMError`) goes straight to the
  DLQ. A timeout-shaped error (`TransientLLMError`) gets
  `MAX_TRANSIENT_ATTEMPTS=2` with exponential backoff
  (`TRANSIENT_BACKOFF_BASE_S=0.5`) first. See
  `docs/adr/0003-permanent-vs-transient-llm-errors.md`.

## 2. Exact commands

Every Makefile recipe runs under `set -a && source ./env.sh --quiet && set +a`
first — this loads `.env.example` → `.env` → `~/.config/de-portfolio/.env`
(in that order) and exports `AWS_ENDPOINT_URL`, `LLM_PROVIDER`,
`VECTOR_BACKEND` with safe defaults if nothing else set them.

```bash
source env.sh              # verbose: prints what got loaded (learn mode)
docker compose up -d       # start MiniStack (S3, SQS, Lambda, DynamoDB, Step Functions)
make check-env             # OK/FAIL: is MiniStack reachable
make deploy-gate           # deploy the budget-gate Lambdas + Step Functions state machine
make demo                  # deploy-gate + docker up + bootstrap + 20 policies/10 claims + index
python3 scripts/bootstrap.py   # idempotent: create buckets/queue/tables (scripts/resources.json)
make test                  # unit/ + integration/, LLM_PROVIDER=fake, VECTOR_BACKEND=chroma
make e2e                   # tests/data_quality — full pipeline + quality-report.json/.md
make eval                  # scripts/eval.py --mode single-shot, then --mode agentic
make reindex               # src/transformation/reindex.py — incremental re-embed + trace archive
make inspect               # scripts/aws_inspect.py all — dump MiniStack state
```

Run `pytest features/` (or let `make test` pick it up) for the BDD scenarios in
`features/*.feature` — see §6.

Dependencies: `requirements.in` (direct runtime deps) and `requirements-dev.in`
(lint/type/security tooling, constrained against `requirements.txt` so the two
never disagree) are the source of truth — never hand-edit `requirements.txt`
or `requirements-dev.txt`, they're generated:
```bash
.venv/bin/pip-compile requirements.in --output-file requirements.txt
.venv/bin/pip-compile requirements-dev.in --output-file requirements-dev.txt
```
This is also what makes Dependabot's pip PRs resolvable instead of hand-editing
one pinned line into a conflict with another.

## 3. Naming conventions

**S3 buckets:** `claims-docs` (uploaded policy docs + `_reindex_manifest.json`),
`claims-traces` (nightly Parquet archive of answer traces, written by PySpark
under `s3a://claims-traces/archive/`).

**SQS:** `claims-agent-dlq` — messages carry `{claim_id, reason}` for
budget exhaustion, or `{claim_id, failure_type, reason}` for an LLM-call
failure (`failure_type` is what tells the two DLQ paths apart — see
`docs/quality-report.md`).

**DynamoDB tables**, both keyed by the single partition key `claim_id` (a
string, no composite/sort key in this repo):

- `claims-answers` — `{claim_id, answer, citations (JSON string), iterations, trace (JSON string)}`
- `claims-token-budget` — `{claim_id, tokens_spent}` (atomic counter, see §1)

**Vector store collection:** `policy_clauses` (Chroma persisted at `.chroma/`
locally, or a Pinecone index of the same name when `VECTOR_BACKEND=pinecone`).

**Lambda function names:** `claims-check-budget`, `claims-send-to-dlq`
(deployed from `src/orchestration/lambdas/`, zipped by file name — keep
each handler self-contained with no local package imports, since only the
single `.py` file is zipped).

**Step Functions state machine:** `claims-agent-loop-gate`
(`asl/agent_loop_gate.json`).

**Commits:** imperative mood, capitalized, no type prefix (`Add X`, `Fix Y`,
`Classify Z` — see `git log --oneline`), one logical change per commit.

## 4. Schema and data rules

- `claims-answers.citations` and `.trace` are stored as JSON-encoded
  strings inside DynamoDB string attributes, not native DynamoDB lists/maps
  — decode with `json.loads` on read (see `src/serving/api.py::trace`).
- The reindex manifest (`s3://claims-docs/_reindex_manifest.json`) maps
  `policy_id → sha256(json.dumps(policy, sort_keys=True))`. A policy is
  only re-embedded if its hash changed — don't bypass the manifest check
  to force a full re-embed in code; pass a fresh `--policies-dir` instead.
- Retrieval granularity is one chunk per clause (`chunk_by_clause`) so that
  a citation always maps 1:1 to something a claims adjuster can point to.
  Don't change chunking to span multiple clauses without updating
  `docs/specs/spec-agent-retrieval-loop.md` and the golden set's
  `ground_truth_clauses`.
- A claim's `token_budget` counter in DynamoDB is cumulative per
  `claim_id`. Never reuse a `claim_id` across two logically distinct runs
  (tests and `make eval` both mint a fresh UUID/run-id per invocation for
  this reason) — reusing one makes a claim look budget-exhausted on its
  first "real" iteration.

## 5. What NOT to touch without confirming

- **`.env`** — never commit it. It's where `MINIMAX_API_KEY` and
  `PINECONE_API_KEY` live.
- **`LLM_PROVIDER=minimax`** — costs real money per call (~$0.30/M input
  tokens, ~$1.20/M output, per `src/models/llm/minimax.py`). Only reached by
  `make eval` when explicitly exported. CI and `make test`/`make demo`
  always use `LLM_PROVIDER=fake` — do not change that default.
- **`VECTOR_BACKEND=pinecone`** — uses a real Pinecone index and API
  quota/cost. Default is `chroma` (free, local, gitignored `.chroma/`) —
  do not change that default either.
- **Deleting/recreating S3 buckets, DynamoDB tables, or the SQS DLQ**
  directly against MiniStack — go through `scripts/bootstrap.py` (idempotent
  create-if-missing) instead of hand-rolled `aws s3 rb` / `aws dynamodb
  delete-table` calls, so `scripts/resources.json` stays the source of
  truth.
- **`scripts/bootstrap.py` and `scripts/resources.json`** — these are what
  CI and every other script assume exist; changing a table/bucket/queue
  name here without updating every consumer (`src/utils/aws.py` callers,
  `docs/data-dictionary.md`, this file) will break bootstrap silently
  elsewhere.
- **`MAX_ITERATIONS`, `RRF_K`, `DEFAULT_TOKEN_BUDGET`, `MAX_TRANSIENT_ATTEMPTS`**
  in `src/models/agent_loop.py` — these are cited by name in the README's
  measured numbers and in `docs/specs/spec-agent-retrieval-loop.md`.
  Changing one invalidates the measured precision/recall numbers until the
  eval is rerun with `LLM_PROVIDER=minimax` (costs money — see above).

## 6. Where specs and features live

Before implementing or changing behavior in this repo, read:

- `docs/specs/` — one spec per pipeline/feature (business goal, inputs,
  transformations, expected output/SLA, edge cases, acceptance criteria).
- `docs/adr/` — why the current design was chosen over the alternatives
  that were considered and rejected.
- `features/*.feature` + `features/steps/*.py` — executable BDD scenarios
  (pytest-bdd) that encode the acceptance criteria from the specs above.
  A change that isn't covered by an existing scenario should get a new one
  before the implementation changes.

## 7. PII and synthetic data

Every dataset in this repo — policies, claims, ground-truth clause labels —
is synthetic and deterministic by seed (`--seed 42` default, see
`src/ingestion/data_gen.py`). Rules:

- Never introduce real policyholder data, real claim text, or any other
  real PII into this repo, its tests, or its fixtures.
- Never log a full LLM prompt/response payload in production-shaped code
  paths — traces stored in `claims-answers` already truncate the answer
  text (`result["answer"][:2000]`); don't remove that truncation to "see
  more" during debugging.
- Notebooks aren't part of this repo (deliberately — see
  `docs/data-dictionary.md`). If one shows up later for exploration, it
  must never become something a Makefile target or CI depends on —
  notebooks don't feed productive pipelines here.
