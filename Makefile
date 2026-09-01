SHELL := /bin/bash
.PHONY: demo demo-full test e2e eval check-env deploy-gate inspect reindex

ENV := set -a && source ./env.sh --quiet && set +a

check-env:
	$(ENV) && python3 scripts/check_env.py

inspect:
	$(ENV) && python3 scripts/aws_inspect.py all

deploy-gate:
	$(ENV) && python3 src/orchestration/statemachine.py

demo: deploy-gate
	$(ENV) && docker compose up -d
	$(ENV) && python3 scripts/bootstrap.py
	$(ENV) && python3 src/ingestion/data_gen.py --policies 20 --claims 10 --out data
	$(ENV) && python3 src/ingestion/ingest.py --in data/policies
	$(ENV) && VECTOR_BACKEND=chroma python3 src/ingestion/index_docs.py --in data

demo-full: demo

test:
	$(ENV) && LLM_PROVIDER=fake VECTOR_BACKEND=chroma pytest tests/ features/ -v --ignore=tests/data_quality

e2e:
	$(ENV) && LLM_PROVIDER=fake VECTOR_BACKEND=chroma pytest tests/data_quality -v -s

eval:
	$(ENV) && VECTOR_BACKEND=chroma python3 scripts/eval.py --mode single-shot
	$(ENV) && VECTOR_BACKEND=chroma python3 scripts/eval.py --mode agentic

reindex:
	$(ENV) && VECTOR_BACKEND=chroma python3 src/transformation/reindex.py --policies-dir data
