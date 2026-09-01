# ADR 0001 — Reciprocal Rank Fusion vs merge por distancia

## Contexto

El loop agentic recupera `top_k=5` en cada iteración. Hay que fusionar
listas cuya escala de distancia no es comparable entre queries.

## Decisión

`_reciprocal_rank_fusion` con `RRF_K=60`: score `1/(k+rank)` por lista.
Un ítem rank 1 en dos queries gana aunque una lista tenga distancias
"mejores" en valor absoluto.

## Alternativas consideradas

- **Min-distance merge**: medido peor (precisión ~0.067 vs ~0.133 con
  RRF) — una query con embeddings uniformemente cercanos domina.
- **Cross-encoder rerank**: mejor calidad potencial, segundo modelo y
  latencia/costo por claim; el budget de tokens ya es el cuello.
- **Unión + dedup sin score**: no hay orden estable para citar.

## Consecuencias

No "simplificar" a sort por `distance`. Regresión: `tests/unit/test_rrf_fusion.py`.
