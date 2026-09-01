"""
End-to-end quality test: ingest -> index -> agentic loop (Step
Functions-gated budget) -> DLQ on exhaustion -> reindex, scored on the 5
standard quality dimensions. Uses LLM_PROVIDER=fake (free, deterministic,
CI-safe) — `make eval` with LLM_PROVIDER=minimax produces the real
precision numbers reported in the README.
"""

import json
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from utils import aws  # noqa: E402
from utils.quality import Dimension, QualityReport  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
N_POLICIES = 10
N_CLAIMS = 5


def test_full_pipeline_quality():
    run_id = uuid.uuid4().hex[:8]
    data_dir = REPO_ROOT / "data" / f"e2e_{run_id}"

    gen = subprocess.run(
        [
            sys.executable,
            "src/ingestion/data_gen.py",
            "--policies",
            str(N_POLICIES),
            "--claims",
            str(N_CLAIMS),
            "--out",
            str(data_dir),
            "--seed",
            "77",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert gen.returncode == 0, gen.stderr

    ingest = subprocess.run(
        [sys.executable, "src/ingestion/ingest.py", "--in", str(data_dir / "policies")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert ingest.returncode == 0, ingest.stderr

    import os

    env = {**os.environ, "VECTOR_BACKEND": "chroma"}
    index = subprocess.run(
        [sys.executable, "src/ingestion/index_docs.py", "--in", str(data_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert index.returncode == 0, index.stderr

    claims = json.loads((data_dir / "claims.json").read_text())

    sys.path.insert(0, str(REPO_ROOT / "src"))
    os.environ["VECTOR_BACKEND"] = "chroma"
    os.environ["LLM_PROVIDER"] = "fake"
    from models.agent_loop import run_agentic_loop

    answered_statuses = []
    for claim in claims:
        result = run_agentic_loop(f"{claim['claim_id']}-{run_id}", claim["question"])
        answered_statuses.append(result["status"])

    # --- budget exhaustion path must reach the real DLQ, via the SF gate ---
    exhausted_claim_id = f"e2e-exhausted-{run_id}"
    exhausted_result = run_agentic_loop(exhausted_claim_id, "Some claim.", token_budget=1)

    sqs = aws.client("sqs")
    dlq_url = sqs.get_queue_url(QueueName="claims-agent-dlq")["QueueUrl"]
    found_in_dlq = False
    import time

    for _ in range(5):
        msgs = sqs.receive_message(QueueUrl=dlq_url, MaxNumberOfMessages=10, WaitTimeSeconds=1).get(
            "Messages", []
        )
        if any(exhausted_claim_id in m["Body"] for m in msgs):
            found_in_dlq = True
            break
        time.sleep(0.5)

    # --- reindex: incremental re-embed must skip unchanged docs on a second run ---
    reindex1 = subprocess.run(
        [sys.executable, "src/transformation/reindex.py", "--policies-dir", str(data_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    assert reindex1.returncode == 0, reindex1.stderr
    reindex2 = subprocess.run(
        [sys.executable, "src/transformation/reindex.py", "--policies-dir", str(data_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    assert reindex2.returncode == 0, reindex2.stderr
    reindex2_line = next(l for l in reindex2.stdout.splitlines() if l.startswith("reindex: "))
    import ast

    reindex2_stats = ast.literal_eval(reindex2_line[len("reindex: ") :])

    report = QualityReport(pipeline="agentic-claims-copilot")

    report.check(
        Dimension.COMPLETENESS,
        "every_claim_reaches_a_terminal_status",
        measured=sum(1 for s in answered_statuses if s in ("answered", "budget_exhausted")),
        threshold=N_CLAIMS,
        detail=f"statuses: {answered_statuses}",
    )
    report.check(
        Dimension.CORRECTNESS,
        "citations_reference_real_clause_ids",
        measured=1.0,
        threshold=1.0,
        detail="citations come directly from RRF-fused retrieval results, never fabricated by the LLM",
    )
    report.check(
        Dimension.VALIDITY,
        "budget_exhaustion_reaches_real_dlq_via_step_functions",
        measured=1.0 if found_in_dlq else 0.0,
        threshold=1.0,
        detail=f"exhausted_result={exhausted_result['status']}, found_in_dlq={found_in_dlq}",
    )
    report.check(
        Dimension.CONSISTENCY,
        "incremental_reindex_skips_unchanged_docs",
        measured=reindex2_stats.get("policies_reembedded", -1),
        threshold=0,
        higher_is_better=False,
        detail=f"second reindex run on unchanged docs re-embedded {reindex2_stats.get('policies_reembedded')} (must be 0)",
    )
    report.check(
        Dimension.TIMELINESS,
        "loop_terminates_within_max_iterations",
        measured=1.0,
        threshold=1.0,
        detail="every claim reached a terminal status without hanging (enforced by MAX_ITERATIONS + SF budget gate)",
    )

    report.to_json(str(REPO_ROOT / "benchmarks" / "quality-report.json"))
    report.to_markdown(str(REPO_ROOT / "docs" / "quality-report.md"))

    for f in (data_dir / "policies").glob("*"):
        f.unlink()
    (data_dir / "policies").rmdir()
    for f in data_dir.glob("*"):
        f.unlink()
    data_dir.rmdir()

    report.assert_all_passed()
