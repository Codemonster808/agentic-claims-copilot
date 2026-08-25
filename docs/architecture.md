# Architecture

```mermaid
flowchart LR
    DOC[Synthetic policy docs\n+ claim intake] --> S3[(S3)]
    S3 --> EMB[Lambda: chunk + embed]
    EMB --> VEC[(Pinecone / Chroma)]
    SF[Step Functions state machine] --> PLAN[Lambda: Plan\npropose retrieval query]
    PLAN --> TOOL[Lambda: Tool\nquery vector store]
    TOOL --> VEC
    TOOL --> OBSERVE[Lambda: Observe\nscore sufficiency + token spend]
    OBSERVE -->|sufficient| ANSWER[Emit answer + citations]
    OBSERVE -->|insufficient, budget left| PLAN
    OBSERVE -->|budget exhausted| DLQ[(SQS DLQ)]
    ANSWER --> DDB[(DynamoDB\nanswers + traces)]
    NIGHTLY[Nightly PySpark] --> S3
    NIGHTLY --> VEC
    DDB --> API[FastAPI: /ask /trace/id /eval/report]
```

## Data flow notes

- The loop (`Plan → Tool → Observe → Choice`) is a Step Functions `Map`/`Choice` construct — the budget counter lives in DynamoDB so it survives across Lambda invocations.
- `Observe` is the only step that can end the loop early (sufficient evidence) or route to the DLQ (budget exhausted) — it is the safety valve.
- The nightly PySpark job re-embeds only documents that changed, keeping the vector store fresh without a full daily re-index.
