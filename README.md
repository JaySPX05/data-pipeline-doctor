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

Corrections matter most. When an engineer says the agent was wrong, the correction is retained, and the next similar failure gets the right answer. The agent's own guesses are never retained, so a wrong guess can't come back as evidence for itself.

**Guardrails: memory beats the model.** The LLM proposes a diagnosis; plain Python rules in `backend/app/agent/guardrails.py` decide how much of it to believe. Every rule that fires is logged with its reason: in the backend log, `python -m app.agent` and the eval output.

| # | Rule | Why |
| --- | --- | --- |
| 1 | KNOWN_ISSUE or FLAKY needs history for this task, or it becomes NEW (confidence ≤ 0.5) | "Known" must be backed by memory |
| 1b | If every past outcome for this failure passed on retry, it's FLAKY | Nobody ever fixed it, so don't page anyone |
| 2 | UPSTREAM_CHANGE needs a change from the 72 h before the failure (change feed, scheduler or memory), or it becomes NEW | A deploy from last week isn't a cause |
| 3 | Confidence above 0.95 needs an exact fingerprint match | Near-certainty only for the same failure |
| 4 | A diagnosis that contradicts the latest correction for this fingerprint is capped at 0.5 and pages, and the correction is cited first | What the team said wins |
| 5 | Only FLAKY at 0.8 or more skips the page | Everything else wakes a human |

## Quick start

The team runs memory on **Hindsight Cloud**. Self-hosted Hindsight needs an LLM budget for its own fact extraction that the free Groq tier can't give (see below).

```bash
cp .env.example .env     # add GROQ_API_KEY (+ optional GEMINI_API_KEY) and the Hindsight Cloud values below
python -m venv .venv && . .venv/bin/activate      # Windows: .\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
python scripts/seed_memory.py                     # the team's past: days 1-14 into Hindsight (112 memories)
cd backend && uvicorn app.main:app --port 8000    # API on :8000, docs at /docs
cd frontend && npm install && npm run dev         # UI on :5173 (another terminal, from the repo root)
```

With Docker instead: `make up-cloud`, then `make seed`, then `make replay`. That starts only the backend and frontend, and memory goes to Cloud.

Replay the rest of the month and watch it learn:

```bash
python scripts/replay.py --from-day 15 --to-day 30 --speed 60x --max-gap 3 --auto-feedback
```

No API keys yet? The backend runs offline: the agent falls back to a rule-based diagnosis and memory calls are skipped (`MEMORY_ENABLED=false`). Frontend devs can use `VITE_USE_MOCKS=true npm run dev`.

**Self-hosted Hindsight** (`make up`, a large image) needs a paid Groq tier or another LLM provider for Hindsight itself. A free Groq key fails every retain with "`service_tier` `auto` is not available" (`HINDSIGHT_API_LLM_GROQ_SERVICE_TIER=on_demand` fixes that error, but the free tier's 8K tokens/min is still far below what a retain needs).

## Hindsight Cloud (what we use)

Local Hindsight runs its own LLM extraction on every retain, which the Groq and Gemini free tiers can't sustain while seeding, so the team uses Hindsight Cloud.

1. Sign up at [ui.hindsight.vectorize.io](https://ui.hindsight.vectorize.io), then **Connect → Create API Key**. In `.env` set:
   ```
   HINDSIGHT_URL=https://api.hindsight.vectorize.io
   HINDSIGHT_URL_DOCKER=https://api.hindsight.vectorize.io   # what the backend container uses
   HINDSIGHT_API_KEY=<your key>
   HINDSIGHT_BANK_ID=quickbasket-data-platform
   MEMORY_ENABLED=true
   ```
2. Seed the agent's past (days 1–14) from the repo root. The scripts read `.env` too: `python scripts/seed_memory.py`
3. Run the backend locally (`cd backend` then `uvicorn app.main:app --port 8000`), or in Docker with `make up-cloud` (`docker compose up --build -d --no-deps backend frontend`; `--no-deps` keeps the local Hindsight image from starting).
4. Check: http://localhost:8000/api/memory/status shows the Cloud URL, and `cd backend` then `python -m app.memory.smoke` passes.
5. `python scripts/reset_demo.py --seeded` wipes and re-seeds the bank. It asks you to type the bank name first (`--dry-run` shows what it would do).

## Deploy (Render, free tier)

`render.yaml` deploys the API (Docker, `deploy/backend.Dockerfile`) and the React UI (static site) together.

1. **Seed a separate bank for the hosted demo**, so visitors' clicks never touch the bank the eval uses:
   `python scripts/seed_memory.py --bank-id quickbasket-demo`
2. On [render.com](https://render.com), sign in with GitHub, then **New → Blueprint** and pick this repo. Render reads `render.yaml` and asks for `GROQ_API_KEY`, `GEMINI_API_KEY` (optional, leave blank to skip) and `HINDSIGHT_API_KEY`. Click **Apply**.
3. When both services are live, open `https://pipeline-failure-doctor-api.onrender.com/api/health`. It should show the LLM chain and `api.hindsight.vectorize.io`. If Render gave the API a different hostname because the name was taken, set the static site's `VITE_API_URL` to it and redeploy the static site.
4. The UI is at `https://pipeline-failure-doctor.onrender.com`.

**Free-tier behaviour:**
- The API sleeps after 15 minutes idle, so the first request after that takes about a minute.
- There's no persistent disk, so the incident inbox resets on every restart. Hindsight memory is in the cloud and is kept.
- Anyone with the URL can trigger diagnoses that use your Groq quota.

**A populated inbox after every restart (optional).** The API copies `data/demo/pfd_demo.sqlite3` in on boot when it has no database. To make that snapshot from a real replay, from the repo root on Windows PowerShell:

```powershell
$env:HINDSIGHT_BANK_ID = "quickbasket-demo"          # this terminal only: replay into the demo bank
Remove-Item data\pfd.sqlite3 -ErrorAction SilentlyContinue
cd backend; uvicorn app.main:app --port 8000          # leave running; open a 2nd terminal at the repo root:
python scripts/replay.py --from-day 15 --to-day 30 --speed 3600x --max-gap 2 --auto-feedback
# stop uvicorn (Ctrl+C), then:
mkdir data\demo; Copy-Item data\pfd.sqlite3 data\demo\pfd_demo.sqlite3
git add data/demo/pfd_demo.sqlite3; git commit -m "Demo snapshot"; git push
```

The replay sends 49 failures, so it uses a good part of a day's free Groq quota. Every diagnosis in the snapshot is a real one the agent made.

## Results: does memory help?

`cd backend` then `python -m app.agent.eval --markdown` diagnoses failures from the replay window (days 15–30) twice, with and without memory, against a bank seeded with days 1–14, and scores each answer against P3's ground truth. It measures verdict accuracy, whether known failures were recognised, root-cause match, and correct page/don't-page decisions. It is read-only, so runs are repeatable. Raw results go to `data/generated/eval_results.jsonl`, with the guardrails that fired and the memories cited for each answer. Misses also print them. `--scenario S09` runs every scenario whose id starts with S09.

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
  - S08: every past `dq_checks` failure had passed on retry, but the model called it KNOWN_ISSUE (and, in a re-run, NEW). Now a failure whose past outcomes all passed on retry is FLAKY, and so doesn't page. For a NEW verdict this needs the exact fingerprint, so a new error on a usually-flaky task still pages.
  - S09c: a same-DAG change from 7.7 days earlier counted as evidence for UPSTREAM_CHANGE. Only changes from the 72 h before a failure count now, and an upstream change with none is downgraded to NEW.
  - **Re-runs of the missed scenarios after the fixes:** S08 was FLAKY with no page in all 6 runs over three re-runs since #23 (0% → 100%), and S09c went 0% → 100% (NEW). S09d and S09e stayed correct. Every S08 run came back at exactly 0.80 confidence, the floor rule 1b sets when it overrides a NEW: the model kept saying NEW, and the guardrail corrected it from memory. That's why the rules live in code and not in the prompt.
  - The full table above is from the run before the fixes. Every scenario that missed is now right in a targeted re-run, but the headline stays 83.3% until a full re-run (`python -m app.agent.eval --markdown`) confirms it.

## Groq free tier

`openai/gpt-oss-120b` on the free plan allows 30 requests/min, 8K tokens/min and 200K tokens/day, per organization (not per key). The agent is tuned for this: changes and task history are prefetched so most diagnoses are a single ~2K-token call, gpt-oss runs with `reasoning_effort=low`, and on a 429 it waits for Groq's `Retry-After` (up to 20 s) before falling back to `openai/gpt-oss-20b`.

- Each teammate should use a key from their **own** Groq account while developing; keys from one org share one quota.
- **Gemini fallback (optional):** set `GEMINI_API_KEY` in `.env` and the agent's chain becomes Groq `gpt-oss-120b` → Gemini (`GEMINI_MODEL`, its own quota) → Groq `gpt-oss-20b`. Check the chain at http://localhost:8000/api/health (browser, `curl http://localhost:8000/api/health`, or on Windows PowerShell `Invoke-RestMethod http://localhost:8000/api/health`). To give Hindsight the same fallback, copy `docker-compose.override.example.yml` to `docker-compose.override.yml`.
- For the demo, consider the pay-as-you-go Developer plan (much higher limits, cents of usage).
- `model_not_found`? A model id was retired: check `LLM_MODEL` / `LLM_FALLBACK_MODEL` in your `.env` against https://console.groq.com/docs/models.

## Tests and CI

- `cd backend && pytest -q`: 140 tests, fully offline (no keys, no Hindsight). They cover fingerprinting on real-looking logs, every guardrail, the LLM fallback chain, the API, the eval scorer and memory-client cleanup.
- `make e2e` (or `python integrations/e2e/e2e_test.py --base-url http://localhost:8000`) checks the full loop against a running stack: failure → diagnosis card → feedback → memory → Health Brief. Add `E2E_ARGS=--no-memory` without Hindsight.
- CI on every PR: backend tests, frontend build, and a `banned-word` check.

## Repo layout

```
backend/app/
  main.py              FastAPI app + /api/health
  config.py            settings from .env
  models/schemas.py    shared contracts (FROZEN after Day 1 midday)
  api/                 failures, incidents, memory, metrics, pipelines routes   (P1)
  services/            fingerprinting, incident store, demo data loader         (P1)
  agent/               doctor.py loop, llm.py (Groq/Gemini chain), tools.py,
                       guardrails.py, eval.py (memory ON vs OFF)                (P1/P4)
  memory/              hindsight_store.py: retain / recall / reflect, retry queue (P2)
  prompts/             system prompt                                             (P1)
backend/tests/         pytest, runs fully offline                               (P4)
data/scenarios/        failure scenario YAMLs; data/generated/ = 30-day history (P3)
scripts/               generate_history, seed_memory, replay, reset_demo         (P3)
frontend/src/pages/    Overview, Inbox, Incident, Pipeline Monitor, Insights, Memory (P5, P6)
integrations/airflow/  on_failure_callback + example DAG                         (P7)
integrations/e2e/      full-loop check against a running stack                   (P7)
docs/                  architecture, team split, content + submission checklists (P7)
```

## API

| Method + path | Purpose |
| --- | --- |
| `POST /api/failures` | Ingest a failure, diagnose it, return the incident |
| `GET /api/health` | LLM chain and memory status |
| `GET /api/incidents` | Inbox, newest first: `status`, `verdict`, `dag_id`, `q` (search), `limit`, `offset`; total in `X-Total-Count`. Unknown `status`/`verdict` values return 422 |
| `GET /api/incidents/{id}` | Incident + diagnosis card |
| `POST /api/incidents/{id}/feedback` | Confirm/correct → retained in Hindsight |
| `POST /api/incidents/{id}/rediagnose?memory=false` | Same incident without memory (before/after) |
| `GET /api/memory/search?q=` | Recall from the bank |
| `GET /api/memory/status` | Hindsight URL, bank, last error, breaker and retry-queue state |
| `POST /api/reflect` | Pipeline Health Brief |
| `GET /api/metrics/learning-curve` | Auto-diagnosed % and accuracy per day |
| `GET /api/pipelines` | The 6 demo DAGs and their tasks (from `data/dags.yaml`) with incident stats per DAG |
| `GET /api/metrics/summary` | KPI tiles: totals by verdict, accuracy, pages avoided, MTTR per day, first vs latest day |

Interactive docs: http://localhost:8000/docs

## Links

- [Hindsight on GitHub](https://github.com/vectorize-io/hindsight)
- [Hindsight documentation](https://hindsight.vectorize.io/)
- [What is agent memory?](https://vectorize.io/what-is-agent-memory)
