# Quality report — agentic-claims-copilot

Generated: 2026-08-25T16:42:46.284990+00:00

**Overall score: 100%** (5/5 checks passed)

| Dimension | Score |
|---|---|
| completeness | 100% |
| correctness | 100% |
| consistency | 100% |
| validity | 100% |
| timeliness | 100% |

## Checks

| Dimension | Check | Measured | Threshold | Status | Detail |
|---|---|---|---|---|---|
| completeness | every_claim_reaches_a_terminal_status | 5 | 5 | PASS | statuses: ['answered', 'answered', 'answered', 'answered', 'answered'] |
| correctness | citations_reference_real_clause_ids | 1.0 | 1.0 | PASS | citations come directly from RRF-fused retrieval results, never fabricated by the LLM |
| validity | budget_exhaustion_reaches_real_dlq_via_step_functions | 1.0 | 1.0 | PASS | exhausted_result=budget_exhausted, found_in_dlq=True |
| consistency | incremental_reindex_skips_unchanged_docs | 0 | 0 | PASS | second reindex run on unchanged docs re-embedded 0 (must be 0) |
| timeliness | loop_terminates_within_max_iterations | 1.0 | 1.0 | PASS | every claim reached a terminal status without hanging (enforced by MAX_ITERATIONS + SF budget gate) |
