# Spec: incremental reindex

## Objetivo de negocio

Re-embeber todas las pólizas cada noche es caro y lento. Solo deben
re-indexarse documentos cuyo contenido cambió.

## Fuentes de entrada

- `data/` / `--policies-dir` con `_policy_clauses.json`.
- Manifiesto `s3://claims-docs/_reindex_manifest.json`:
  `policy_id → sha256(json.dumps(policy, sort_keys=True))`.

## Transformaciones

`src/transformation/reindex.py:reindex_changed_docs` compara hashes,
re-embebe solo `changed`, upsert a `policy_clauses`, guarda el manifiesto
nuevo. También `archive_traces()` vuelca `claims-answers` a
`s3://claims-traces/archive/` (Parquet).

## Salida esperada

`{"policies_checked": N, "policies_reembedded": M}` con M ≤ N.
Segunda corrida sin cambios de archivo → M = 0.

## Casos borde

- Primera corrida (manifiesto ausente): todo es "changed".
- Mismo `policy_id`, JSON reordenado: `sort_keys=True` evita re-embed falso.

## Criterios de aceptación

Cubrido por el camino de `make reindex` y e2e de traces; no hay test
unitario aislado del manifiesto — no inventar uno decorativo sin MiniStack
si el e2e ya archiva traces.
