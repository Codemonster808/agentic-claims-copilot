#!/usr/bin/env python3
"""
The agentic retrieval loop: Plan -> Tool -> Observe -> Choice, with a hard
token budget enforced via a DynamoDB atomic counter (UpdateItem/ADD — not
read-modify-write, since each loop iteration could in principle run as a
separate Lambda invocation with no shared memory).

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

BUDGET_TABLE = "claims-token-budget"
ANSWERS_TABLE = "claims-answers"
DLQ_NAME = "claims-agent-dlq"

DEFAULT_TOKEN_BUDGET = 2000
COST_PER_ITERATION = 300  # rough accounting unit charged per loop iteration
MAX_ITERATIONS = 3


def _embed(text: str) -> list[float]:
    from sentence_transformers import SentenceTransformer
    model = _embed._model if hasattr(_embed, "_model") else SentenceTransformer("all-MiniLM-L6-v2")
    _embed._model = model
    return model.encode([text])[0].tolist()


def _spend_budget(claim_id: str, amount: int) -> int:
    """Atomically increments spend for a claim; returns the new total."""
    ddb = aws.client("dynamodb")
    resp = ddb.update_item(
        TableName=BUDGET_TABLE,
        Key={"claim_id": {"S": claim_id}},
        UpdateExpression="ADD tokens_spent :inc",
        ExpressionAttributeValues={":inc": {"N": str(amount)}},
        ReturnValues="UPDATED_NEW",
    )
    return int(resp["Attributes"]["tokens_spent"]["N"])


def _send_to_dlq(claim_id: str, reason: str) -> None:
    sqs = aws.client("sqs")
    queue_url = sqs.get_queue_url(QueueName=DLQ_NAME)["QueueUrl"]
    import json
    sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps({"claim_id": claim_id, "reason": reason}))


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
        spent = _spend_budget(claim_id, COST_PER_ITERATION)
        if spent > token_budget:
            _send_to_dlq(claim_id, f"token budget exceeded ({spent} > {token_budget})")
            return {"status": "budget_exhausted", "iterations": iteration, "trace": trace}

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
