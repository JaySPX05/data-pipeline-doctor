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
Groq gpt-oss-120b → Gemini → Groq gpt-oss-20b        │
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

## Groq free tier

`openai/gpt-oss-120b` on the free plan allows 30 requests/min, 8K tokens/min and 200K tokens/day, per organization (not per key). The agent is tuned for this: changes and task history are prefetched so most diagnoses are a single ~2K-token call, gpt-oss runs with `reasoning_effort=low`, and on a 429 it waits for Groq's `Retry-After` (up to 20 s) before falling back to `openai/gpt-oss-20b`.

- Each teammate should use a key from their **own** Groq account while developing; keys from one org share one quota.
- **Gemini fallback (optional):** set `GEMINI_API_KEY` in `.env` and the agent's chain becomes Groq `gpt-oss-120b` → Gemini (`GEMINI_MODEL`, its own quota) → Groq `gpt-oss-20b`. Check the chain at http://localhost:8000/api/health (browser, `curl http://localhost:8000/api/health`, or on Windows PowerShell `Invoke-RestMethod http://localhost:8000/api/health`). To give Hindsight the same fallback, copy `docker-compose.override.example.yml` to `docker-compose.override.yml`.
- For the demo, consider the pay-as-you-go Developer plan (much higher limits, cents of usage).
- `model_not_found`? A model id was retired: check `LLM_MODEL` / `LLM_FALLBACK_MODEL` in your `.env` against https://console.groq.com/docs/models.

## Repo layout

```
backend/app/
  main.py              FastAPI app + /api/health
  config.py            settings from .env
  models/schemas.py    shared contracts (FROZEN after Day 1 midday)
  api/                 failures, incidents, memory, metrics routes        (P1)
  services/            fingerprinting + incident store                    (P1)
  agent/               doctor.py loop, llm.py (Groq), tools.py            (P1)
  memory/              hindsight_store.py: retain / recall / reflect      (P2)
  prompts/             system prompt                                       (P1)
backend/tests/         pytest, runs fully offline                         (P4)
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
| `GET /api/incidents` | Inbox, newest first: `status`, `verdict`, `dag_id`, `q` (search), `limit`, `offset`; total in `X-Total-Count` |
| `GET /api/incidents/{id}` | Incident + diagnosis card |
| `POST /api/incidents/{id}/feedback` | Confirm/correct → retained in Hindsight |
| `POST /api/incidents/{id}/rediagnose?memory=false` | Same incident without memory (before/after) |
| `GET /api/memory/search?q=` | Recall from the bank |
| `POST /api/reflect` | Pipeline Health Brief |
| `GET /api/metrics/learning-curve` | Auto-diagnosed % and accuracy per day |
| `GET /api/metrics/summary` | KPI tiles: totals by verdict, accuracy, pages avoided, MTTR per day, first vs latest day |

Interactive docs: http://localhost:8000/docs

## Links

- [Hindsight on GitHub](https://github.com/vectorize-io/hindsight)
- [Hindsight documentation](https://hindsight.vectorize.io/)
- [What is agent memory?](https://vectorize.io/what-is-agent-memory)
