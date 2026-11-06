# Pipeline Failure Doctor

> An on-call agent for data engineering teams that remembers how your pipelines break and how they got fixed. Built on Hindsight memory.

![architecture](docs/architecture.png)

## The problem
Data teams re-diagnose failures they have already fixed. The "why" lives in Slack and one senior engineer's head. Monitoring says *that* something failed, not *why it failed last time*.

## Demo (3 min)
[Video link] · Screenshots: `docs/screens/`

## Quick start (3 commands)
```bash
cp .env.example .env        # add your GROQ_API_KEY
docker compose up --build
make seed                   # loads days 1-14 into Hindsight
```
Open http://localhost:5173 · Hindsight UI http://localhost:9999 · API docs http://localhost:8000/docs

Then `make replay` to replay days 15-30 live.

## How we use Hindsight
<!-- P2 writes this section. Must cover: -->
- **Bank**: one per team (`quickbasket-data-platform`) with a mission
- **retain**: failure events, confirmed resolutions, flaky outcomes, upstream changes, corrections (with context, timestamp, document_id, metadata, tags)
- **recall**: 2-3 queries per failure, budget="mid"; results appear as evidence on the card
- **reflect**: nightly Pipeline Health Brief
- **Memory ON/OFF** toggle and learning curve prove the value
- **Fallback** when Hindsight is down

## Architecture
FastAPI backend + Groq (tool-calling) + Hindsight + React dashboard. See `docs/ARCHITECTURE.md`.

## Tech stack
Python 3.11, FastAPI, SQLite, Hindsight, Groq (gpt-oss-120b, qwen3-32b fallback), React, Vite, Tailwind, Recharts, Docker Compose.

## Optional: real Airflow
`make airflow` then trigger the `orders_etl` DAG at http://localhost:8080.

## Team
<!-- names + roles -->

## Tests
`make test` (backend) · `make e2e` (full loop)
