# Architecture

```mermaid
flowchart LR
  A[Airflow callback / 30-day simulator] -->|failure| B[FastAPI backend<br/>ingest + fingerprint + SQLite]
  B --> C[Diagnosis agent]
  C <-->|tool calls| D[Agent tools<br/>schema changes, deploys, logs, retries]
  C <-->|prompt + memories| E[Groq LLM]
  C -->|retain| F[(Hindsight memory<br/>one bank per team)]
  F -->|recall| C
  B -->|card| G[React dashboard]
  G -->|feedback| B
```

## Flow
1. Ingest -> 2. Fingerprint -> 3. Recall -> 4. Diagnose (tools) -> 5. Feedback -> retain -> 6. Reflect.

## Ports
| Service | Port |
|---|---|
| Frontend | 5173 |
| Backend (Swagger at /docs) | 8000 |
| Hindsight API / UI | 8888 / 9999 |
| Airflow (optional) | 8080 |
