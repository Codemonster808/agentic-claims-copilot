"""
Lambda-shaped handler: the real orchestration piece of the agentic loop.
Atomically increments the per-claim token spend counter and reports
whether the claim is still within budget. Deployed to MiniStack Lambda,
invoked from Step Functions with Retry/Catch (asl/agent_loop_gate.json).

The Plan/Tool/Observe/Answer steps themselves stay in Python
(src/models/agent_loop.py), invoked by the driver (src/orchestration/statemachine.py) between
gate calls — the same reasoning as fintech-txn-integrity-pipeline's
Spark-outside-Lambda pattern: embedding models and LLM clients don't fit
in a bare Lambda runtime any more than a Spark cluster does. Step
Functions' real job here is the budget/DLQ control flow around each
iteration, not hosting the ML calls.
"""

import boto3

BUDGET_TABLE = "claims-token-budget"
COST_PER_ITERATION = 300


def handler(event, context):
    endpoint = "http://127.0.0.1:4566"
    ddb = boto3.client("dynamodb", endpoint_url=endpoint, region_name="us-east-1")

    claim_id = event["claim_id"]
    token_budget = event.get("token_budget", 2000)

    resp = ddb.update_item(
        TableName=BUDGET_TABLE,
        Key={"claim_id": {"S": claim_id}},
        UpdateExpression="ADD tokens_spent :inc",
        ExpressionAttributeValues={":inc": {"N": str(COST_PER_ITERATION)}},
        ReturnValues="UPDATED_NEW",
    )
    spent = int(resp["Attributes"]["tokens_spent"]["N"])

    return {"claim_id": claim_id, "tokens_spent": spent, "within_budget": spent <= token_budget}
