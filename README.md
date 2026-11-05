# Pipeline Failure Doctor

An on-call agent for data engineering teams. When a pipeline task fails, it diagnoses the failure from the team's own incident history, which it keeps in [Hindsight](https://github.com/vectorize-io/hindsight) memory, and it gets better with every incident the team resolves.

> Day 1 (no memory): "KeyError usually means a missing column. Check your source."
> Day 20 (with memory): "Same `KeyError: 'customer_id'` in `load_orders` as Oct 3 and Oct 11. FreshFarm's feed v2.5 renamed the column again. Map `cust_id -> customer_id` in `vendor_schema.yaml`. Last fix took 4 minutes."

## How it works

```
Airflow callback / replay simulator
        │  POST /api/failures
        ▼
FastAPI backend ── fingerprint the log ── SQLite incident store
        │
        ▼
Diagnosis agent ── recall ──► Hindsight memory ◄── retain (failures, resolutions, corrections, changes)
        │   ▲                                         ▲
        │   └── tools: change feed, retry history, log grep, memory search
        ▼                                             │
Groq LLM (gpt-oss-120b, qwen3-32b fallback)           │
        │                                             │
        ▼                                             │
React dashboard: diagnosis card + evidence ── engineer feedback
```

**How Hindsight memory is used**

| Operation | When | What |
| --- | --- | --- |
| `retain` | Every failure, resolution, correction and upstream change | Facts tagged `dag:`, `task:`, `fp:` (fingerprint), `kind:` |
| `recall` | Before every diagnosis | Past failures of this task/fingerprint, flakiness, recent upstream changes |
| `reflect` | Insights page | Pipeline Health Brief: noisiest tasks, repeat offenders, permanent fixes |

Corrections matter most. When an engineer says the agent was wrong, the correction is retained, and the next similar failure gets the right answer.

## Quick start

```bash
cp .env.example .env              # add your GROQ_API_KEY
docker compose up --build         # Hindsight :8888 (UI :9999), API :8000, UI :5173
```

Without Docker:

```bash
make setup                        # venv + npm install
docker run --rm -it -p 8888:8888 -p 9999:9999 \
  -e HINDSIGHT_API_LLM_PROVIDER=groq -e HINDSIGHT_API_LLM_API_KEY=$GROQ_API_KEY \
  -v $HOME/.hindsight-docker:/home/hindsight/.pg0 ghcr.io/vectorize-io/hindsight:latest
make backend                      # terminal 2
make frontend                     # terminal 3
```

Run the demo:

```bash
make demo      # reset, generate 30 days of data, seed days 1-14 into Hindsight
make replay    # replay days 15-30 live; watch the inbox and the Insights page
```

No API keys yet? The backend runs offline: the agent falls back to a rule-based diagnosis and memory calls are skipped (`MEMORY_ENABLED=false`). Frontend devs can use `VITE_USE_MOCKS=true npm run dev`.

## Repo layout

```
backend/app/
  main.py              FastAPI app + /api/health
  config.py            settings from .env
  models/schemas.py    shared contracts (FROZEN after Day 1 midday)
  api/                 failures, incidents, memory, metrics routes        (P4)
  services/            fingerprinting + incident store                    (P4)
  agent/               doctor.py loop, llm.py (Groq), tools.py            (P1)
  memory/              hindsight_store.py: retain / recall / reflect      (P2)
  prompts/             system prompt                                       (P1)
backend/tests/         pytest, runs fully offline
data/scenarios/        failure scenario YAMLs                              (P3)
scripts/               generate_history, seed_memory, replay, reset_demo   (P3)
frontend/src/          React + Vite + Tailwind: Inbox, Incident, Insights, Memory (P5, P6)
integrations/airflow/  on_failure_callback + example DAG                   (P7)
docs/                  team split, content checklist                       (P7)
```

## API

| Method + path | Purpose |
| --- | --- |
| `POST /api/failures` | Ingest a failure, diagnose it, return the incident |
| `GET /api/incidents` | Inbox (`status`, `verdict`, `dag_id` filters) |
| `GET /api/incidents/{id}` | Incident + diagnosis card |
| `POST /api/incidents/{id}/feedback` | Confirm/correct → retained in Hindsight |
| `POST /api/incidents/{id}/rediagnose?memory=false` | Same incident without memory (before/after) |
| `GET /api/memory/search?q=` | Recall from the bank |
| `POST /api/reflect` | Pipeline Health Brief |
| `GET /api/metrics/learning-curve` | Auto-diagnosed % and accuracy per day |

Interactive docs: http://localhost:8000/docs

## Links

- [Hindsight on GitHub](https://github.com/vectorize-io/hindsight)
- [Hindsight documentation](https://hindsight.vectorize.io/)
- [What is agent memory?](https://vectorize.io/what-is-agent-memory)
