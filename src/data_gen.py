#!/usr/bin/env python3
"""
Generates synthetic insurance policy documents (each with several clearly
identifiable, numbered clauses) and synthetic claims that reference 1-2
real clauses plus distractor topics. Ground truth (which clause(s) answer
which claim) is embedded at generation time, since we control the data —
this is what the golden eval set is built from.
"""
import argparse
import json
import random
from pathlib import Path

TOPICS = [
    ("water_damage", "Water Damage Exclusion",
     "Losses caused by flooding, sewer backup, or gradual water seepage are excluded from coverage, "
     "except where the policyholder has purchased the separate Flood Rider, in which case losses up to "
     "${amount} are covered."),
    ("theft", "Theft Coverage",
     "Theft of insured property is covered up to the policy limit, provided a police report is filed "
     "within {hours} hours of discovery."),
    ("fire", "Fire and Smoke Damage",
     "Direct physical loss caused by fire or smoke is covered, including damage to the structure and "
     "contents, up to a dwelling coverage limit of ${amount}."),
    ("liability", "Personal Liability",
     "The policy covers bodily injury or property damage liability arising from the insured premises, "
     "up to ${amount} per occurrence."),
    ("windstorm", "Windstorm and Hail",
     "Damage from windstorm or hail is covered, subject to a separate deductible of ${deductible} "
     "specified in the declarations page."),
    ("deductible", "Standard Deductible",
     "A standard deductible of ${deductible} applies to all covered losses unless a higher deductible is "
     "selected at binding."),
    ("temp_housing", "Additional Living Expenses",
     "If the insured dwelling is uninhabitable due to a covered loss, reasonable additional living "
     "expenses are covered for up to {months} months."),
    ("mold", "Mold Exclusion",
     "Damage caused by mold, fungus, or wet rot is excluded unless it results directly from a covered "
     "water damage event within the first {days} days."),
    ("jewelry_limit", "Scheduled Personal Property Limit",
     "Jewelry, watches, and furs are covered up to ${limit} in aggregate unless individually scheduled "
     "on the policy with an appraisal."),
    ("earthquake", "Earthquake Exclusion",
     "Damage caused by earthquake or earth movement is excluded from this policy up to a maximum of "
     "${amount} and requires a separate Earthquake Endorsement."),
]


def gen_policy(rng, policy_id: str, n_clauses: int = 6) -> dict:
    """
    Each clause's text is varied with policy-specific numbers (limits,
    deductibles, day counts) — without this, two policies that both draw
    the "windstorm" topic would have byte-identical clause text, which
    makes their embeddings identical too. That's not a retrieval problem
    to solve; it's a synthetic-data bug that makes citation precision
    unmeasurable, since even perfect retrieval can't disambiguate two
    passages with no distinguishing content. This was caught by running
    the actual retrieval eval, not by reading the generator code.
    """
    chosen = rng.sample(TOPICS, n_clauses)
    clauses = []
    for i, (topic_id, title, text) in enumerate(chosen, start=1):
        clause_num = f"{i}.{rng.randint(1, 9)}"
        varied_text = text.format(**_random_clause_params(rng, topic_id)) if "{" in text else text
        varied_text += f" This provision applies under policy {policy_id}."
        clauses.append({
            "clause_id": f"{policy_id}-{clause_num}",
            "topic": topic_id,
            "title": title,
            "text": varied_text,
        })
    return {"policy_id": policy_id, "clauses": clauses}


def _random_clause_params(rng, topic_id: str) -> dict:
    return {
        "hours": rng.choice([24, 48, 72]),
        "amount": rng.choice([250_000, 300_000, 500_000]),
        "days": rng.choice([10, 14, 21]),
        "limit": rng.choice([1_500, 2_500, 5_000]),
        "months": rng.choice([6, 12, 18]),
        "deductible": rng.choice([500, 1_000, 2_500]),
    }


def render_policy_text(policy: dict) -> str:
    lines = [f"POLICY {policy['policy_id']}", ""]
    for c in policy["clauses"]:
        lines.append(f"Clause {c['clause_id'].split('-')[-1]}: {c['title']}")
        lines.append(c["text"])
        lines.append("")
    return "\n".join(lines)


def gen_claim(rng, claim_id: str, policies: list[dict]) -> dict:
    policy = rng.choice(policies)
    target_clause = rng.choice(policy["clauses"])
    # 30% of claims reference a second clause too (more realistic, harder retrieval)
    second_clause = None
    if rng.random() < 0.3 and len(policy["clauses"]) > 1:
        others = [c for c in policy["clauses"] if c["clause_id"] != target_clause["clause_id"]]
        second_clause = rng.choice(others)

    scenario_templates = {
        "water_damage": "A pipe burst in the kitchen and water seeped into the flooring over several weeks.",
        "theft": "The policyholder's laptop and TV were stolen during a break-in; a police report was filed the next day.",
        "fire": "A kitchen fire damaged the ceiling and destroyed several appliances.",
        "liability": "A visitor slipped on the front steps and is seeking medical cost reimbursement.",
        "windstorm": "A hailstorm damaged the roof shingles and gutters.",
        "deductible": "The policyholder is asking how much they owe out of pocket before coverage kicks in.",
        "temp_housing": "The house is uninhabitable after a covered fire and the family needs a hotel.",
        "mold": "Mold was found in the bathroom wall two months after a leak was fixed.",
        "jewelry_limit": "A diamond ring worth $8,000 was lost while traveling.",
        "earthquake": "A minor earthquake cracked the foundation of the house.",
    }
    question = scenario_templates[target_clause["topic"]]
    ground_truth_clauses = [target_clause["clause_id"]]
    if second_clause:
        question += " " + scenario_templates[second_clause["topic"]]
        ground_truth_clauses.append(second_clause["clause_id"])

    return {
        "claim_id": claim_id,
        "policy_id": policy["policy_id"],
        "question": question,
        "ground_truth_clauses": ground_truth_clauses,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policies", type=int, default=20)
    parser.add_argument("--claims", type=int, default=10)
    parser.add_argument("--out", required=True)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    out_dir = Path(args.out)
    (out_dir / "policies").mkdir(parents=True, exist_ok=True)

    policies = [gen_policy(rng, f"POL-{i:03d}") for i in range(args.policies)]
    for policy in policies:
        (out_dir / "policies" / f"{policy['policy_id']}.txt").write_text(render_policy_text(policy))
    (out_dir / "_policy_clauses.json").write_text(json.dumps(policies, indent=2))

    claims = [gen_claim(rng, f"CLAIM-{i:03d}", policies) for i in range(args.claims)]
    (out_dir / "claims.json").write_text(json.dumps(claims, indent=2))

    print(f"wrote {len(policies)} policies ({sum(len(p['clauses']) for p in policies)} clauses total) to {out_dir}/policies/")
    print(f"wrote {len(claims)} claims (golden labels included) to {out_dir}/claims.json")


if __name__ == "__main__":
    main()
