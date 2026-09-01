#!/usr/bin/env python3
"""
Nightly job: (1) re-embeds only policy documents whose content changed
since the last run (tracked via a content-hash manifest in S3, not a
full re-embed every night), and (2) archives claim answer traces from
DynamoDB to S3 Parquet via PySpark for historical analysis.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils import aws  # noqa: E402

MANIFEST_KEY = "_reindex_manifest.json"
DOCS_BUCKET = "claims-docs"
TRACES_BUCKET = "claims-traces"


def _load_manifest(s3) -> dict:
    try:
        obj = s3.get_object(Bucket=DOCS_BUCKET, Key=MANIFEST_KEY)
        return json.loads(obj["Body"].read())
    except s3.exceptions.NoSuchKey:
        return {}


def _save_manifest(s3, manifest: dict) -> None:
    s3.put_object(Bucket=DOCS_BUCKET, Key=MANIFEST_KEY, Body=json.dumps(manifest).encode())


def reindex_changed_docs(policies_dir: str) -> dict:
    """Compares each local policy file's content hash to the manifest —
    only files that changed (or are new) get re-embedded."""
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ingestion.index_docs import chunk_by_clause  # noqa: E402
    from utils.vectors import get_vector_store

    s3 = aws.client("s3")
    manifest = _load_manifest(s3)
    policies = json.loads((Path(policies_dir) / "_policy_clauses.json").read_text())

    changed = []
    new_manifest = {}
    for policy in policies:
        content = json.dumps(policy, sort_keys=True)
        digest = hashlib.sha256(content.encode()).hexdigest()
        new_manifest[policy["policy_id"]] = digest
        if manifest.get(policy["policy_id"]) != digest:
            changed.append(policy)

    if changed:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer("all-MiniLM-L6-v2")
        store = get_vector_store("policy_clauses")

        ids, texts, metadatas = [], [], []
        for policy in changed:
            for chunk in chunk_by_clause(policy):
                ids.append(chunk["id"])
                texts.append(chunk["text"])
                metadatas.append(chunk["metadata"])
        embeddings = model.encode(texts, show_progress_bar=False).tolist()
        store.upsert(ids=ids, embeddings=embeddings, metadatas=metadatas)

    _save_manifest(s3, new_manifest)
    return {"policies_checked": len(policies), "policies_reembedded": len(changed)}


def archive_traces() -> dict:
    """Dumps claims-answers (DynamoDB) to S3 Parquet via PySpark."""
    import os

    from pyspark.sql import SparkSession

    ddb = aws.client("dynamodb")
    items = []
    paginator = ddb.get_paginator("scan")
    for page in paginator.paginate(TableName="claims-answers"):
        for item in page.get("Items", []):
            items.append(
                {
                    "claim_id": item["claim_id"]["S"],
                    "answer": item.get("answer", {}).get("S", ""),
                    "citations": item.get("citations", {}).get("S", "[]"),
                    "iterations": int(item.get("iterations", {}).get("N", 0)),
                }
            )

    if not items:
        return {"traces_archived": 0}

    endpoint = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
    spark = (
        SparkSession.builder.appName("archive-traces")
        .master("local[2]")
        .config("spark.driver.memory", "1g")
        .config("spark.jars.packages", "org.apache.hadoop:hadoop-aws:3.5.0")
        .config("spark.hadoop.fs.s3a.endpoint", endpoint)
        .config("spark.hadoop.fs.s3a.access.key", os.environ.get("AWS_ACCESS_KEY_ID", "test"))
        .config("spark.hadoop.fs.s3a.secret.key", os.environ.get("AWS_SECRET_ACCESS_KEY", "test"))
        .config("spark.hadoop.fs.s3a.path.style.access", "true")
        .config("spark.hadoop.fs.s3a.connection.ssl.enabled", "false")
        .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
        .getOrCreate()
    )
    try:
        df = spark.createDataFrame(items)
        df.coalesce(1).write.mode("overwrite").parquet(f"s3a://{TRACES_BUCKET}/archive/")
        return {"traces_archived": len(items)}
    finally:
        spark.stop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policies-dir", default="data")
    args = parser.parse_args()

    reembed_stats = reindex_changed_docs(args.policies_dir)
    print(f"reindex: {reembed_stats}")

    archive_stats = archive_traces()
    print(f"archive: {archive_stats}")


if __name__ == "__main__":
    main()
