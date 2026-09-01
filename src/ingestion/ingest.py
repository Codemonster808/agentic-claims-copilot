#!/usr/bin/env python3
"""Uploads synthetic policy documents to s3://claims-docs/ — the
architecture diagram's `claim doc upload -> S3` step, which data_gen.py
alone doesn't do (it only writes local files for index_docs.py to read)."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils import aws  # noqa: E402

BUCKET = "claims-docs"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="in_dir", default="data/policies")
    args = parser.parse_args()

    s3 = aws.client("s3")
    files = sorted(Path(args.in_dir).glob("*.txt"))
    if not files:
        raise SystemExit(
            f"no policy .txt files found in {args.in_dir} — run src/ingestion/data_gen.py first"
        )

    for f in files:
        s3.upload_file(str(f), BUCKET, f.name)
        print(f"  uploaded {f.name}")

    print(f"uploaded {len(files)} policy documents to s3://{BUCKET}/")


if __name__ == "__main__":
    main()
