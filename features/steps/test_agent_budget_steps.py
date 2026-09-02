import sys
import uuid
from pathlib import Path

from pytest_bdd import given, scenarios, then, when

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from models.agent_loop import run_agentic_loop  # noqa: E402
from utils import aws  # noqa: E402

scenarios("../agent-budget.feature")


@given("a claim with a token budget of 1", target_fixture="claim_id")
def claim_with_budget_1():
    return f"test-budget-{uuid.uuid4()}"


@when("the agentic loop runs for that claim", target_fixture="result")
def run_loop(claim_id):
    return run_agentic_loop(claim_id, "Some claim.", token_budget=1)


@then("the status is budget_exhausted")
def status_is_budget_exhausted(result):
    assert result["status"] == "budget_exhausted"


@then("the response has no citations key")
def no_citations_key(result):
    assert "citations" not in result


@then("the claim is recorded in the real DLQ")
def claim_in_dlq(claim_id):
    sqs = aws.client("sqs")
    queue_url = sqs.get_queue_url(QueueName="claims-agent-dlq")["QueueUrl"]
    # A standard SQS queue doesn't guarantee one receive_message call surfaces
    # every message, especially one accumulated across repeated test runs —
    # poll a few times rather than asserting on a single receive.
    found = False
    for _ in range(5):
        messages = sqs.receive_message(
            QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=1
        ).get("Messages", [])
        if any(claim_id in m["Body"] for m in messages):
            found = True
            break
    assert found, "budget-exhausted claim was not recorded in the DLQ"


@given("a fresh claim id", target_fixture="fresh_claim_id")
def fresh_claim_id():
    return f"test-atomic-{uuid.uuid4()}"


@when(
    "the budget gate is called 20 times concurrently for that claim", target_fixture="gate_results"
)
def call_gate_concurrently(fresh_claim_id):
    import concurrent.futures

    from orchestration.statemachine import gate_iteration

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        return list(
            pool.map(lambda _: gate_iteration(fresh_claim_id, token_budget=100_000), range(20))
        )


@then("tokens_spent reaches exactly 6000")
def tokens_spent_is_6000(gate_results):
    max_spent = max(r["tokens_spent"] for r in gate_results)
    assert (
        max_spent == 20 * 300
    ), f"expected the counter to reach exactly {20 * 300}, got max={max_spent}"
