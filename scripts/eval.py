#!/usr/bin/env python3
"""
Runs both the single-shot baseline and the agentic loop over the golden
set (data/claims.json, ground truth embedded at generation time) and
reports citation precision@k for each — this produces the comparison
table that goes in the README.
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from models.agent_loop import run_agentic_loop, run_single_shot_baseline  # noqa: E402


def precision_at_k(predicted_citations: list[str], ground_truth: list[str]) -> float:
    if not predicted_citations:
        return 0.0
    hits = sum(1 for c in predicted_citations if c in ground_truth)
    return hits / len(predicted_citations)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claims", default="data/claims.json")
    parser.add_argument("--mode", choices=["single-shot", "agentic"], required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    claims = json.loads(Path(args.claims).read_text())
    results = []
    # The token-budget counter in DynamoDB is a running total per claim_id
    # (atomic ADD, by design — see agent_loop.py). That means re-running
    # eval against the same claim_id would inherit spend from a prior run
    # and immediately look budget-exhausted. A fresh id per run keeps
    # `make eval` repeatable. Found by actually re-running eval twice.
    run_id = uuid.uuid4().hex[:8]

    for claim in claims:
        if args.mode == "single-shot":
            outcome = run_single_shot_baseline(claim["claim_id"], claim["question"])
            citations = outcome["citations"]
            iterations = 1
            status = "answered"
        else:
            outcome = run_agentic_loop(
                f"{claim['claim_id']}-{args.mode}-{run_id}", claim["question"]
            )
            citations = outcome.get("citations", [])
            iterations = outcome["iterations"]
            status = outcome["status"]

        precision = precision_at_k(citations, claim["ground_truth_clauses"])
        results.append(
            {
                "claim_id": claim["claim_id"],
                "status": status,
                "iterations": iterations,
                "predicted_citations": citations,
                "ground_truth": claim["ground_truth_clauses"],
                "precision": precision,
            }
        )

    avg_precision = sum(r["precision"] for r in results) / len(results) if results else 0.0
    avg_iterations = sum(r["iterations"] for r in results) / len(results) if results else 0.0
    n_answered = sum(1 for r in results if r["status"] == "answered")

    summary = {
        "mode": args.mode,
        "n_claims": len(claims),
        "n_answered": n_answered,
        "citation_precision_at_k": round(avg_precision, 4),
        "avg_iterations": round(avg_iterations, 2),
        "results": results,
    }

    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=2))

    out_path = Path(args.out) if args.out else Path(f"docs/eval-{args.mode}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2))
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
