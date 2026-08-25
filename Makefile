.PHONY: demo test e2e eval check-env deploy-gate

check-env:
	python3 scripts/check_env.py

deploy-gate:
	python3 src/statemachine.py

demo: deploy-gate
	docker compose up -d
	python3 scripts/bootstrap.py
	python3 src/data_gen.py --policies 20 --claims 10 --out data
	python3 src/ingest.py --in data/policies
	VECTOR_BACKEND=chroma python3 src/index_docs.py --in data

test:
	LLM_PROVIDER=fake VECTOR_BACKEND=chroma pytest tests/ -v --ignore=tests/test_e2e.py

e2e:
	LLM_PROVIDER=fake VECTOR_BACKEND=chroma pytest tests/test_e2e.py -v -s

eval:
	VECTOR_BACKEND=chroma python3 src/eval.py --mode single-shot
	VECTOR_BACKEND=chroma python3 src/eval.py --mode agentic

reindex:
	VECTOR_BACKEND=chroma python3 src/reindex.py --policies-dir data
