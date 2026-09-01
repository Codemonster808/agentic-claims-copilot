# Build Guide — agentic-claims-copilot

Estimated total: ~24 hours across 2-3 weeks of evenings.

## Glossary

- **Embedding**: turning a chunk of text into a list of numbers that captures its meaning, so similar text has similar numbers.
- **Vector store**: a database optimized for finding the most similar embeddings to a query (here: Pinecone, or Chroma locally).
- **Agentic loop**: instead of asking the model once, you ask it to plan a step, act, look at the result, and decide whether to try again.
- **Token budget**: a hard limit on how much the loop is allowed to "think" before it must stop and admit it can't answer.

## 0. Before you start (30 min)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ~/.config/de-portfolio/.env .env   # MINIMAX_API_KEY, PINECONE_API_KEY
```

Decide your vector backend for local dev: `export VECTOR_BACKEND=chroma` (no external account needed) or `=pinecone` (uses your real `PINECONE_API_KEY`). Decide your LLM backend: `export LLM_PROVIDER=fake` for free/offline development (steps 3-6 below), `=minimax` only when you're ready to run `make eval` in step 7 — MiniMax M3 costs real money per call (~$0.30/M input tokens), so keep it off until the loop is built and tested against the fake provider.

## 1. Get the environment running (1 h) → checkpoint: `make check-env`

```bash
docker compose up -d
python3 scripts/bootstrap.py
make check-env
```

## 2. Generate synthetic data (2 h) → checkpoint: `make check-data`

Write 20 synthetic insurance policy documents (plain text, a few paragraphs each, with clearly identifiable clauses like "Clause 4.2: water damage exclusion"). Write 10 synthetic claims, each referencing 1-2 real clauses and 1-2 unrelated distractor topics.

```bash
python3 src/ingestion/data_gen.py --policies 20 --claims 10 --out data/
make check-data   # "OK: 20 policies, 10 claims, all claims reference at least 1 real clause"
```

## 3. Build embedding + retrieval (3 h) → checkpoint: `make check-retrieval`

Write `src/embed.py` (chunk policies, embed, upsert to the vector store) and `src/retrieve.py` (query top-k for a given text).

```bash
make check-retrieval   # for each claim, asserts the correct clause is in the top-5 results at least 80% of the time
```

## 4. Build the golden eval set (2 h) → checkpoint: `make check-eval-set`

For each of the 10 claims, hand-label the correct clause ID(s). This is the file everything else gets measured against — do not skip labeling it carefully.

```bash
make check-eval-set   # "OK: 10/10 claims labeled with >=1 clause each"
```

## 5. Build the single-shot baseline (2 h) → checkpoint: `pytest tests/test_baseline.py`

Before building the loop, measure the single-shot approach (one retrieval, one answer, no retry) against the golden set. This produces the 0.53-style baseline number.

```bash
python3 scripts/eval.py --mode single-shot
pytest tests/test_baseline.py
```

## 6. Build the agentic loop (6-8 h) → checkpoint: `make check-loop`

Build one Lambda at a time:
1. `Plan` — given the claim and prior attempts, propose a retrieval query.
2. `Tool` — run the query against the vector store.
3. `Observe` — score whether the retrieved evidence is sufficient (a simple heuristic is fine: does a labeled clause appear in the top-k?), and increment the token counter in DynamoDB.
4. Wire them into a Step Functions state machine with a `Choice` state: sufficient → emit answer; insufficient + budget left → back to `Plan`; budget exhausted → SQS DLQ.

```bash
make check-loop   # runs all 10 claims through the full loop, asserts no claim exceeds the token budget without landing in DLQ
```

**Troubleshooting**
- Loop never terminates in local testing → check the `Observe` step is actually reading the updated counter, not a stale value from the state input.
- Everything lands in DLQ → your "sufficient evidence" heuristic is too strict; check it against a known-good retrieval manually first.

## 7. Measure it (3 h) → checkpoint: `make eval`

```bash
make eval   # runs both single-shot and agentic over the golden set, writes docs/eval-report.md
```

This is where the 0.53 → 0.81-style comparison table gets generated — copy it into the README.

## 8. Write the impact model (1 h)

Fill `docs/impact-model.md`, including exactly how the baseline number was measured (dataset, method) so it survives a follow-up question.

## 9. Ship the README (2 h)

Save one full trace to `docs/trace-example.json`. Fill both metric tables.

## Troubleshooting index

| Symptom | Likely cause | Fix |
|---|---|---|
| Citation precision doesn't improve over baseline | `Observe` step's sufficiency check is too lenient, loop exits after 1 iteration anyway | tighten the threshold, re-run `make eval` |
| DynamoDB token counter races under retries | multiple Lambda invocations read-then-write the same key | use `UpdateItem` with `ADD` instead of read-modify-write |

## Total estimated effort: ~24 hours (2-3 weeks of evenings)
