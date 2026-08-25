#!/usr/bin/env python3
"""FastAPI serving layer: ask a claim question, inspect a trace, read the last eval report."""
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from agent_loop import run_agentic_loop  # noqa: E402
from common import aws  # noqa: E402

app = FastAPI(title="agentic-claims-copilot")


class AskRequest(BaseModel):
    question: str
    token_budget: int = 2000


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ask")
def ask(req: AskRequest):
    claim_id = f"api-{uuid.uuid4().hex[:8]}"
    result = run_agentic_loop(claim_id, req.question, token_budget=req.token_budget)
    return {"claim_id": claim_id, **result}


@app.get("/trace/{claim_id}")
def trace(claim_id: str):
    ddb = aws.client("dynamodb")
    resp = ddb.get_item(TableName="claims-answers", Key={"claim_id": {"S": claim_id}})
    item = resp.get("Item")
    if not item:
        raise HTTPException(status_code=404, detail="no trace found for this claim_id")
    return {
        "claim_id": claim_id,
        "answer": item["answer"]["S"],
        "citations": json.loads(item["citations"]["S"]),
        "iterations": int(item["iterations"]["N"]),
        "trace": json.loads(item["trace"]["S"]),
    }


@app.get("/eval/report")
def eval_report():
    reports = {}
    for mode in ("single-shot", "agentic"):
        path = Path(f"docs/eval-{mode}.json")
        if path.exists():
            data = json.loads(path.read_text())
            reports[mode] = {k: v for k, v in data.items() if k != "results"}
    if not reports:
        raise HTTPException(status_code=404, detail="no eval reports found — run `make eval` first")
    return reports
