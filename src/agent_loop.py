#!/usr/bin/env python3
"""
The agentic retrieval loop: Plan -> Tool -> Observe -> Choice, with a hard
token budget gated by a real Step Functions state machine
(asl/agent_loop_gate.json) called once per iteration — Choice routes to
either "keep going" or a Lambda that sends the claim to the DLQ, with
Retry/Catch around the DynamoDB call. The budget counter itself is an
atomic DynamoDB counter (UpdateItem/ADD, inside the gate's Lambda) —
never read-modify-write, since each gate call is a separate Lambda
invocation with no shared memory.

Step Functions here orchestrates the control flow (budget/DLQ decision),
not the ML calls — the same reasoning as
fintech-txn-integrity-pipeline's Spark-outside-Lambda pattern: Plan/Tool
Observe/Answer stay in Python because an embedding model and an LLM
client don't fit in a bare Lambda runtime.

Design choice: evidence from every iteration's query is combined via
reciprocal rank fusion (RRF), not by comparing raw embedding distances
across queries — an earlier version merged by min-distance and it
measurably hurt both precision and recall versus not looping at all,
because different phrasings of the same question produce embeddings
with different absolute-distance scales, so a query that happens to
produce uniformly lower distances would dominate the merge regardless of
relevance. RRF only uses each result's rank within its own query, which
sidesteps that problem. The LLM is called at most twice per claim: once
to reformulate the query on a retry, once to generate the final answer.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import aws  # noqa: E402
from common.llm import get_provider  # noqa: E402
from common.vectors import get_vector_store  # noqa: E402
from statemachine import gate_iteration  # noqa: E402

ANSWERS_TABLE = "claims-answers"

DEFAULT_TOKEN_BUDGET = 2000
MAX_ITERATIONS = 3


def _embed(text: str) -> list[float]:
    from sentence_transformers import SentenceTransformer
    model = _embed._model if hasattr(_embed, "_model") else SentenceTransformer("all-MiniLM-L6-v2")
    _embed._model = model
    return model.encode([text])[0].tolist()


def plan_query(llm, question: str, prior_queries: list[str]) -> str:
    """
    On the first iteration, search with the claim as written. On retries,
    ask the LLM to bridge the vocabulary gap between how a claimant
    describes a scenario ("hailstorm damaged the roof shingles") and how
    a policy clause is actually worded ("windstorm or hail... deductible
    specified in the declarations page") — plain embedding-distance
    retrieval on a small local model can't do this on its own, which is
    exactly the gap the loop's retry step exists to close.
    """
    if not prior_queries:
        return question
    prompt = (
        f"An insurance claim says: \"{question}\"\n"
        f"Previous search queries that did not find a strong match in the policy: {prior_queries}\n"
        "Rewrite this as a short search query using formal insurance-policy terminology "
        "(coverage type, exclusion, deductible, limit) instead of the claimant's own words. "
        "Return only the rewritten query, nothing else."
    )
    response = llm.complete(prompt, max_tokens=64)
    rewritten = response.strip().strip('"')[:200]
    return rewritten or question


def generate_answer(llm, question: str, retrieved: list[dict]) -> dict:
    citations = [r["metadata"]["clause_id"] for r in retrieved]
    context = "\n".join(f"[{r['metadata']['clause_id']}] {r['metadata']['title']}" for r in retrieved)
    prompt = (
        f"Claim: {question}\n\nRelevant policy clauses:\n{context}\n\n"
        "Answer the claim using only these clauses and cite the clause IDs."
    )
    answer_text = llm.complete(prompt, max_tokens=256)
    return {"answer": answer_text, "citations": citations}


RRF_K = 60  # standard constant from the reciprocal rank fusion literature


def _reciprocal_rank_fusion(ranked_lists: list[list[dict]]) -> list[dict]:
    scores: dict[str, float] = {}
    items: dict[str, dict] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            scores[item["id"]] = scores.get(item["id"], 0.0) + 1.0 / (RRF_K + rank)
            items[item["id"]] = item
    return [items[id_] for id_, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)]


def run_agentic_loop(claim_id: str, question: str, token_budget: int = DEFAULT_TOKEN_BUDGET) -> dict:
    """
    Observe compares each new query's own top-1 distance against the
    previous iteration's — a valid per-query comparison even though
    cross-query distance comparison isn't (see module docstring). Once a
    reformulation stops improving, the loop stops and fuses evidence from
    every iteration via RRF before answering.
    """
    llm = get_provider()
    store = get_vector_store("policy_clauses")

    prior_queries: list[str] = []
    all_ranked_lists: list[list[dict]] = []
    trace = []
    best_top1_distance = float("inf")

    for iteration in range(1, MAX_ITERATIONS + 1):
        gate = gate_iteration(claim_id, token_budget)
        if not gate["within_budget"]:
            return {"status": "budget_exhausted", "iterations": iteration, "trace": trace,
                     "tokens_spent": gate["tokens_spent"]}

        query = plan_query(llm, question, prior_queries)
        prior_queries.append(query)

        retrieved = store.query(_embed(query), top_k=5)
        all_ranked_lists.append(retrieved)

        this_top1 = retrieved[0]["distance"] if retrieved else float("inf")
        improved = this_top1 < best_top1_distance
        trace.append({"iteration": iteration, "query": query, "top1_distance": this_top1, "improved": improved})
        best_top1_distance = min(best_top1_distance, this_top1)

        if not improved and iteration > 1:
            break

    fused = _reciprocal_rank_fusion(all_ranked_lists)
    result = generate_answer(llm, question, fused[:3])
    _write_answer(claim_id, result, len(trace), trace)
    return {"status": "answered", "iterations": len(trace), "trace": trace, **result}


def run_single_shot_baseline(claim_id: str, question: str) -> dict:
    """No loop, no retry — one retrieval, one answer. This is the baseline
    the agentic loop is measured against."""
    llm = get_provider()
    store = get_vector_store("policy_clauses")
    retrieved = store.query(_embed(question), top_k=3)
    return generate_answer(llm, question, retrieved)


def _write_answer(claim_id: str, result: dict, iterations: int, trace: list) -> None:
    import json
    ddb = aws.client("dynamodb")
    ddb.put_item(
        TableName=ANSWERS_TABLE,
        Item={
            "claim_id": {"S": claim_id},
            "answer": {"S": result["answer"][:2000]},
            "citations": {"S": json.dumps(result["citations"])},
            "iterations": {"N": str(iterations)},
            "trace": {"S": json.dumps(trace)},
        },
    )
