#!/usr/bin/env python3
"""
Deploys the budget-gate Lambdas + Step Functions state machine, and
provides gate_iteration() — the function src/models/agent_loop.py calls once
per loop iteration to get a real Step-Functions-mediated budget/DLQ
decision (Choice + Retry + Catch), instead of hitting DynamoDB directly.
"""

import json
import sys
import time
import zipfile
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils import aws  # noqa: E402

LAMBDAS_DIR = Path(__file__).resolve().parent / "lambdas"
ASL_DIR = Path(__file__).resolve().parents[2] / "asl"
ROLE_ARN = "arn:aws:iam::000000000000:role/dummy-role"

FUNCTIONS = {
    "claims-check-budget": "check_budget.py",
    "claims-send-to-dlq": "send_to_dlq.py",
}
STATE_MACHINE_NAME = "claims-agent-loop-gate"
ASL_FILE = "agent_loop_gate.json"


def _zip_handler(file_name: str) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.write(LAMBDAS_DIR / file_name, arcname=file_name)
    return buf.getvalue()


def _wait_active(lam, fn_name: str, timeout_s: float = 20) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if lam.get_function(FunctionName=fn_name)["Configuration"]["State"] == "Active":
            return
        time.sleep(0.5)
    raise TimeoutError(f"Lambda {fn_name} did not become Active in time")


def deploy() -> str:
    lam = aws.client("lambda")
    sfn = aws.client("stepfunctions")

    for fn_name, file_name in FUNCTIONS.items():
        zip_bytes = _zip_handler(file_name)
        handler = f"{file_name[:-3]}.handler"
        existing = {f["FunctionName"] for f in lam.list_functions().get("Functions", [])}
        if fn_name in existing:
            lam.update_function_code(FunctionName=fn_name, ZipFile=zip_bytes)
        else:
            lam.create_function(
                FunctionName=fn_name,
                Runtime="python3.12",
                Role=ROLE_ARN,
                Handler=handler,
                Code={"ZipFile": zip_bytes},
            )
        _wait_active(lam, fn_name)

    definition = (ASL_DIR / ASL_FILE).read_text()
    existing_sms = {
        sm["name"]: sm["stateMachineArn"] for sm in sfn.list_state_machines()["stateMachines"]
    }
    if STATE_MACHINE_NAME in existing_sms:
        sfn.update_state_machine(
            stateMachineArn=existing_sms[STATE_MACHINE_NAME], definition=definition
        )
        return existing_sms[STATE_MACHINE_NAME]
    resp = sfn.create_state_machine(
        name=STATE_MACHINE_NAME, definition=definition, roleArn=ROLE_ARN
    )
    return resp["stateMachineArn"]


_state_machine_arn_cache: str | None = None


def gate_iteration(claim_id: str, token_budget: int, timeout_s: float = 15) -> dict:
    """Runs one Step Functions execution of the budget gate. Returns
    {"within_budget": bool, "tokens_spent": int}."""
    global _state_machine_arn_cache
    sfn = aws.client("stepfunctions")

    if _state_machine_arn_cache is None:
        _state_machine_arn_cache = deploy()

    exec_resp = sfn.start_execution(
        stateMachineArn=_state_machine_arn_cache,
        input=json.dumps({"claim_id": claim_id, "token_budget": token_budget}),
    )
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        desc = sfn.describe_execution(executionArn=exec_resp["executionArn"])
        if desc["status"] != "RUNNING":
            break
        time.sleep(0.3)
    else:
        raise TimeoutError("budget gate execution did not finish in time")

    if desc["status"] != "SUCCEEDED":
        raise RuntimeError(f"budget gate execution failed: {desc}")

    output = json.loads(desc.get("output", "{}"))
    return {
        "within_budget": bool(output.get("within_budget", False)),
        "tokens_spent": output.get("tokens_spent", token_budget + 1),
    }


if __name__ == "__main__":
    arn = deploy()
    print(f"deployed state machine: {arn}")
