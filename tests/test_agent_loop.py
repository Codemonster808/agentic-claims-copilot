"""Runs against live MiniStack + Chroma with LLM_PROVIDER=fake (free, deterministic)."""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_loop import _reciprocal_rank_fusion, run_agentic_loop  # noqa: E402
from common import aws  # noqa: E402


def test_loop_answers_within_budget():
    claim_id = f"test-loop-{uuid.uuid4()}"
    result = run_agentic_loop(claim_id, "A hailstorm damaged the roof shingles and gutters.")
    assert result["status"] == "answered"
    assert result["iterations"] <= 3
    assert "citations" in result


def test_budget_exhaustion_sends_to_dlq_not_a_fabricated_answer():
    claim_id = f"test-budget-{uuid.uuid4()}"
    # A budget smaller than one iteration's cost forces immediate exhaustion.
    result = run_agentic_loop(claim_id, "Some claim.", token_budget=1)
    assert result["status"] == "budget_exhausted"
    assert "citations" not in result  # never fabricates an answer when out of budget

    sqs = aws.client("sqs")
    queue_url = sqs.get_queue_url(QueueName="claims-agent-dlq")["QueueUrl"]
    # A standard SQS queue doesn't guarantee one receive_message call surfaces
    # every message, especially one accumulated across repeated test runs —
    # poll a few times rather than asserting on a single receive.
    found = False
    for _ in range(5):
        messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=1).get("Messages", [])
        if any(claim_id in m["Body"] for m in messages):
            found = True
            break
    assert found, "budget-exhausted claim was not recorded in the DLQ"


def test_budget_counter_is_atomic_not_read_modify_write():
    """
    20 concurrent Step Functions gate executions on the same claim_id
    must not under-count spend — each execution's CheckBudget Lambda
    does an atomic DynamoDB ADD, not a read-modify-write, so the max
    tokens_spent seen across 20 concurrent gate calls (300 each) must be
    exactly 6000, never less.
    """
    import concurrent.futures

    from statemachine import gate_iteration

    claim_id = f"test-atomic-{uuid.uuid4()}"
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: gate_iteration(claim_id, token_budget=100_000), range(20)))
    max_spent = max(r["tokens_spent"] for r in results)
    assert max_spent == 20 * 300, f"expected the counter to reach exactly {20*300}, got max={max_spent}"


def test_reciprocal_rank_fusion_rewards_consistent_top_ranking():
    list_a = [{"id": "x", "distance": 0.1}, {"id": "y", "distance": 0.2}]
    list_b = [{"id": "x", "distance": 0.9}, {"id": "z", "distance": 0.95}]  # different scale, x still ranked #1
    fused = _reciprocal_rank_fusion([list_a, list_b])
    assert fused[0]["id"] == "x", "item ranked #1 in both queries should win the fusion regardless of raw distance scale"


def test_fusion_not_dominated_by_one_querys_distance_scale():
    """
    The exact regression this fix addresses: a query whose embeddings
    happen to be uniformly closer (e.g. list_b here) must not dominate
    the merge just because its raw distances are smaller.
    """
    list_a = [{"id": "correct", "distance": 0.85}]  # correct answer, but distance looks "bad"
    list_b = [{"id": "wrong1", "distance": 0.10}, {"id": "wrong2", "distance": 0.11}]  # wrong, but "good" distances
    fused = _reciprocal_rank_fusion([list_a, list_b])
    # rank-based: "correct" is rank 1 in its own list (score 1/61), "wrong1" is also rank 1 in its list (1/61) — tie by rank
    assert fused[0]["id"] in ("correct", "wrong1")
    assert fused[0]["distance"] != min(0.10, 0.85), "must not simply be sorted by raw distance"
