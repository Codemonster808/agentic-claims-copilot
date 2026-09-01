"""Lambda-shaped handler: sends a budget-exhausted claim to the DLQ."""

import json

import boto3

DLQ_NAME = "claims-agent-dlq"


def handler(event, context):
    endpoint = "http://127.0.0.1:4566"
    sqs = boto3.client("sqs", endpoint_url=endpoint, region_name="us-east-1")
    queue_url = sqs.get_queue_url(QueueName=DLQ_NAME)["QueueUrl"]

    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(
            {
                "claim_id": event["claim_id"],
                "reason": f"token budget exceeded ({event['tokens_spent']} tokens spent)",
            }
        ),
    )
    return {"sent_to_dlq": True, "claim_id": event["claim_id"]}
