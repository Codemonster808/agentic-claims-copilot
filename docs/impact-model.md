# Impact model — assumptions

| # | Assumption | Value used | Source (fill in before publishing) |
|---|---|---|---|
| 1 | Adjuster minutes spent per claim manually cross-referencing policy docs | TODO | TODO |
| 2 | % of that time saved by a citation-grounded first-pass answer | TODO | TODO |
| 3 | Fully loaded adjuster hourly cost | TODO | TODO |
| 4 | Claims processed per month | TODO | TODO |

## Baseline measurement (real numbers, not projected)

Single-shot baseline: `python3 scripts/eval.py --mode single-shot` with `LLM_PROVIDER=minimax`, over the 10-claim golden set in `data/claims.json` (ground truth clause IDs embedded at generation time by `src/ingestion/data_gen.py`). Result: citation precision@3 = 0.133, recall = 4/10.

Agentic (RRF-fused): same command with `--mode agentic`. Result: precision@3 = 0.167, recall = 5/10, avg 2.7 iterations/claim.

Both are small numbers on a small golden set (n=10) — enough to demonstrate the harness and catch a real regression (see README "what actually happened"), not enough to claim statistical significance. Scaling the golden set to 50+ claims is the natural next step before citing these numbers anywhere more formal than this repo.

## Calculation

```
minutes_saved_per_claim = manual_minutes * time_saved_pct
value_per_month           = claims_per_month * minutes_saved_per_claim / 60 * hourly_cost
value_per_year             = value_per_month * 12
```

## Rule for this file

Never change the README's "Modeled business impact" number without updating this file in the same commit.
