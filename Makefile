.PHONY: demo test eval check-env

check-env:
	python3 scripts/check_env.py

demo:
	docker compose up -d
	python3 scripts/bootstrap.py
	python3 src/data_gen.py --policies 20 --claims 10 --out data
	VECTOR_BACKEND=chroma python3 src/index_docs.py --in data

test:
	LLM_PROVIDER=fake VECTOR_BACKEND=chroma pytest tests/ -v

eval:
	VECTOR_BACKEND=chroma python3 src/eval.py --mode single-shot
	VECTOR_BACKEND=chroma python3 src/eval.py --mode agentic
