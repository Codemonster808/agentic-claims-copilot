#!/usr/bin/env python3
"""Chunk + embed the synthetic policy docs into the vector store."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.vectors import get_vector_store  # noqa: E402


def chunk_by_clause(policy: dict) -> list[dict]:
    """One chunk per clause — retrieval granularity matches citation granularity."""
    return [
        {
            "id": clause["clause_id"],
            "text": f"{clause['title']}\n{clause['text']}",
            "metadata": {
                "policy_id": policy["policy_id"],
                "clause_id": clause["clause_id"],
                "title": clause["title"],
            },
        }
        for clause in policy["clauses"]
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="in_dir", required=True)
    args = parser.parse_args()

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("all-MiniLM-L6-v2")
    store = get_vector_store("policy_clauses")

    policies = json.loads((Path(args.in_dir) / "_policy_clauses.json").read_text())

    ids, texts, metadatas = [], [], []
    for policy in policies:
        for chunk in chunk_by_clause(policy):
            ids.append(chunk["id"])
            texts.append(chunk["text"])
            metadatas.append(chunk["metadata"])

    embeddings = model.encode(texts, show_progress_bar=False).tolist()
    store.upsert(ids=ids, embeddings=embeddings, metadatas=metadatas)

    print(f"indexed {len(ids)} clauses from {len(policies)} policies")


if __name__ == "__main__":
    main()
