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

## Hindsight Cloud (what we use)

Local Hindsight runs its own LLM extraction on every retain, which the Groq and Gemini free tiers can't sustain while seeding, so the team uses Hindsight Cloud.

1. In `.env` set `HINDSIGHT_URL=<your Cloud API URL>`, `HINDSIGHT_API_KEY=<your key>`, `HINDSIGHT_BANK_ID=quickbasket-data-platform`, `MEMORY_ENABLED=true`.
2. Seed the agent's past (days 1–14) from the repo root. The scripts read `.env` too: `python scripts/seed_memory.py`
3. Run the backend locally (`cd backend` then `uvicorn app.main:app --port 8000`), or in Docker without the local Hindsight: set `HINDSIGHT_URL_DOCKER=<Cloud URL>` and run `docker compose up --build --no-deps backend frontend`.
4. Check: http://localhost:8000/api/memory/status shows your Cloud URL, and `cd backend` then `python -m app.memory.smoke` passes.

## Results: does memory help?

`cd backend` then `python -m app.agent.eval --markdown` diagnoses failures from the replay window (days 15–30) twice, with and without memory, against a bank seeded with days 1–14, and scores each answer against P3's ground truth. It measures verdict accuracy, whether known failures were recognised, root-cause match, and correct page/don't-page decisions. It is read-only, so runs are repeatable. Raw results go to `data/generated/eval_results.jsonl`.

**With memory on, verdict accuracy goes from 38.9% to 83.3%, and every repeat failure is recognised (46.7% → 100%).** This is a run on Hindsight Cloud on 2026-11-07: 18 failures from the replay window, 2 per scenario, against a bank seeded with days 1–14 (112 memories).

| Metric | Memory OFF | Memory ON |
| --- | --- | --- |
| Verdict accuracy | 38.9% | 83.3% |
| Known failures recognised | 46.7% | 100% |
| Root cause matches the real one | 55.6% | 55.6% |
| Correct page / don't-page decision | 77.8% | 88.9% |
| Average confidence | 0.69 | 0.90 |
| Memories cited per diagnosis | 0 | 2.7 |

| Scenario | Truth | Accuracy OFF | Accuracy ON |
| --- | --- | --- | --- |
| S01 vendor_schema_change | UPSTREAM_CHANGE | 100% | 100% |
| S02 kafka_consumer_timeout | FLAKY | 0% | 100% |
| S03 spark_oom_month_end | KNOWN_ISSUE | 0% | 100% |
| S04 expired_s3_credentials | KNOWN_ISSUE | 0% | 100% |
| S05 upstream_table_dropped_column | UPSTREAM_CHANGE | 100% | 100% |
| S06 duplicate_primary_key | KNOWN_ISSUE | 0% | 100% |
| S07 postgres_pool_exhausted | KNOWN_ISSUE | 0% | 100% |
| S08 dq_check_failed | FLAKY | 0% | 0% |
| S09c wildcard_locale_number_format | NEW | 100% | 0% |
| S09d wildcard_volume_permissions | NEW | 100% | 100% |
| S09e wildcard_redis_failover | NEW | 100% | 100% |

**What the numbers say, honestly:**
- Memory turns every known issue from "NEW, page a human" into the right verdict. The 5 scenarios that went 0% → 100% are the failures the team had already solved.
- Root-cause match didn't move (55.6%). The scorer wants 40% of the ground truth's key words, and the agent often describes the right cause in its own words. Treat it as a lower bound.
- Memory ON is slower (12.4 s vs 6.8 s per diagnosis) because of three recall queries plus Groq free-tier rate limits. Most calls fell back from `gpt-oss-120b` to the next model in the chain.
- **The 3 misses with memory on led to two guardrail fixes** (`backend/app/agent/guardrails.py`, rules 1b and 2):
  - S08: every past `dq_checks` failure had passed on retry, but the model called it KNOWN_ISSUE. Now a "known issue" whose past outcomes were all "passed on retry" is FLAKY, and so doesn't page.
  - S09c: a same-DAG change from 7.7 days earlier counted as evidence for UPSTREAM_CHANGE. Only changes from the 72 h before a failure count now, and an upstream change with none is downgraded to NEW.
  - **Re-run of the missed scenarios after the fixes:** S08 went 0% → 100% with memory on (FLAKY both times, no page), and S09c went 0% → 100% (NEW). S09d and S09e stayed correct. In both S08 runs the model itself still said NEW. The guardrail saw this exact fingerprint pass on retry in memory and corrected the verdict, which is why the rules live in code and not in the prompt.
  - The full table above is from the run before the fixes. Every scenario that missed is now right in a targeted re-run, but the headline stays 83.3% until a full re-run (`python -m app.agent.eval --markdown`) confirms it.

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
backend/app/agent/eval.py  memory ON vs OFF evaluation                   (P1/P4)
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
| `GET /api/pipelines` | The 6 demo DAGs and their tasks (from `data/dags.yaml`) with incident stats per DAG |
| `GET /api/metrics/summary` | KPI tiles: totals by verdict, accuracy, pages avoided, MTTR per day, first vs latest day |

Interactive docs: http://localhost:8000/docs

## Links

- [Hindsight on GitHub](https://github.com/vectorize-io/hindsight)
- [Hindsight documentation](https://hindsight.vectorize.io/)
- [What is agent memory?](https://vectorize.io/what-is-agent-memory)
