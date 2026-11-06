# AI prompts by role

These prompts are for your coding agent (Claude Code, Codex, Code.in or opencode), run at the root of this repo. Start every session with the shared context prompt, then paste one task prompt at a time. Same content as the "AI prompts" tab in the project plan doc.

## How to use these prompts

- **One task per session.** Paste the shared context prompt, then one task prompt. Start a fresh session for the next task.
- **Plan first.** Every prompt asks the agent to show a plan before it edits anything. Read the plan and correct it; it's much cheaper than fixing wrong code.
- **Paste errors verbatim:** the full stack trace and the command you ran.
- **Commit before big changes,** so `git checkout .` can undo a bad turn.
- **Never paste API keys** into the chat. They live in `.env` only.
- **Fill in the `<...>` placeholders** before sending.

### Shared context prompt (start every session with this)

```text
You are helping me build Pipeline Failure Doctor: an on-call agent for data engineering
teams. When an Airflow task fails, it diagnoses the failure using the team's past
incidents stored in Hindsight memory (retain / recall / reflect), and it gets better
every time an engineer confirms or corrects a diagnosis.

Stack: Python 3.11 + FastAPI + SQLite (backend/), Hindsight via hindsight-client 1.2,
Groq via the OpenAI SDK (openai/gpt-oss-120b; fallbacks Gemini via its OpenAI-compatible API when GEMINI_API_KEY is set, then openai/gpt-oss-20b),
React + Vite + Tailwind v4 + Recharts (frontend/). Demo data is a fictional company,
QuickBasket, with 6 DAGs and 30 days of failures generated from data/scenarios/*.yaml.

Before doing anything, read: README.md, docs/TEAM.md,
backend/app/models/schemas.py, and every file in my folder.

I am <P#, role>. I own: <folders from docs/TEAM.md>.

Rules:
1. Only edit files inside my folders. If you need a change elsewhere, stop and tell me
   exactly what to ask the owner for.
2. Do NOT change backend/app/models/schemas.py or the API shapes without asking me.
3. No new dependencies without asking. If you add one, pin it in requirements.txt
   or package.json.
4. Show me a short plan (files to touch, what changes) and wait for my OK before editing.
5. After editing, run `cd backend && pytest -q` (and `cd frontend && npm run build` if
   you touched the frontend) and fix anything you broke.
6. Never print, log or commit API keys. Keys come from .env via backend/app/config.py.
7. Keep the offline mode working: no GROQ_API_KEY + MEMORY_ENABLED=false must still run.

Reply with a 5-line summary of how the system fits together, then wait for my task.
```

## P1 — Jay: tech lead, agent core + backend/API

Owns `backend/app/agent/` (except `eval.py`), `backend/app/prompts/`, `backend/app/api/`, `backend/app/services/` and `backend/app/db/`. Two areas means two kinds of sessions: keep agent work and API work on separate branches, and let P4's tests and bug lists tell you what to fix.

**Day 1: prove the agent loop works with real Groq**

```text
Task: make the diagnosis agent work end to end against real Groq.
Read backend/app/agent/doctor.py, llm.py, tools.py and prompts/diagnose_system.md.
1. Add backend/app/agent/__main__.py so `python -m app.agent <path-to-log.txt>` runs
   FailureDoctor on a log file with MEMORY_ENABLED=false and prints the Diagnosis JSON.
2. Run it on the logs in data/generated/incidents.jsonl (one per scenario; app/services/demo_data.py loads them).
3. Groq can return 400 tool_use_failed or reply with prose instead of JSON. Make sure
   llm.chat() and extract_json() handle both, and log which model and how many tool steps
   each diagnosis used.
4. Show me the output for 3 different scenarios and tell me where the diagnosis is weak.
```

**Day 2 (agent): make verdicts trustworthy**

```text
Task: harden verdict logic in backend/app/agent/doctor.py.
Add a post-processing step after the LLM returns its JSON:
- KNOWN_ISSUE or FLAKY with no evidence from memory -> downgrade to NEW, confidence <= 0.5
- page_oncall must be false only for FLAKY with confidence >= 0.8; enforce it in code
- if a CORRECTION memory matches this fingerprint, its root cause must win
- clamp confidence to 0.95 unless a memory has the same fingerprint (fp_...)
Write unit tests in backend/tests/test_doctor.py using a fake LLMClient and a fake
MemoryStore (no network), and tell P4 they exist. Plan first, then implement.
```

**Day 2 (backend): complete the API**

```text
Task: finish the incidents API for the frontend.
1. GET /api/incidents: add pagination (limit/offset), a `q` search on task and error, and
   return results newest first. Keep the response a list of Incident (don't change schemas).
2. GET /api/metrics/summary: totals by verdict, MTTR per day from feedback.minutes_to_fix,
   and "pages avoided" (FLAKY diagnoses with page_oncall=false). Define a new response
   model in backend/app/api/metrics.py, not in schemas.py.
3. POST /api/failures must return in < 15 s; log timing for recall, LLM and DB separately.
4. Add OpenAPI examples to every route so http://localhost:8000/docs looks good in
   screenshots. Run pytest -q at the end.
```

**Day 3 (agent): tune the prompt with P4's eval**

```text
Task: tune the agent using P4's eval harness.
Run `python -m app.agent.eval` (P4 owns backend/app/agent/eval.py) and read the
per-scenario table: accuracy memory OFF vs ON, confidence, latency. For each scenario
below 80% accuracy with memory ON, show me 2 failing cases with the recalled memories and
the model's raw reply, and say whether the problem is recall (P2), data (P3) or the
prompt (me). Then propose at most 3 changes to prompts/diagnose_system.md, apply them one
at a time and re-run the eval after each. Stop and revert if accuracy drops.
```

**Any day (backend): work through P4's bug list**

```text
Task: fix today's bug list from P4: <paste the list>.
For each bug, in order of demo risk: find the xfail test P4 wrote for it, make it pass by
fixing code in api/, services/, db/ or agent/, and remove the xfail marker. Keep API
response shapes unchanged. Run pytest -q after each fix. End with a one-line summary per
bug for the evening standup.
```

## P2 — Memory engineer

Owns `backend/app/memory/`. Hindsight Python docs: https://hindsight.vectorize.io/sdks/python

**Day 1: Hindsight running and verified**

```text
Task: confirm our Hindsight integration works against a real server.
Hindsight runs at <http://localhost:8888 or my Cloud URL>. Read
backend/app/memory/hindsight_store.py. Use the installed hindsight-client (1.2.0): inspect
the real method signatures with Python's inspect module instead of guessing them.
1. Write backend/app/memory/smoke.py (run with `python -m app.memory.smoke`) that creates
   a throwaway bank, retains 3 memories with tags (task:orders_etl.load_orders,
   fp:fp_test, kind:resolution), recalls with and without tags, reflects once, prints
   everything with timings, then deletes the bank.
2. Check that tags filtering behaves the way recall_for_incident() assumes. If not, fix
   recall_for_incident() and explain what you changed.
```

**Day 2: make recall precise**

```text
Task: measure and improve recall quality in hindsight_store.recall_for_incident().
The bank is seeded with days 1-14 (scripts/seed_memory.py). Write
backend/app/memory/recall_eval.py: for every event with day >= 15 in
data/generated/incidents.jsonl, build the Incident (use services/demo_data.to_incident), call
recall_for_incident(), and record whether the top 3 results mention the same fingerprint
or the same root cause as truth.root_cause. Print hit@3 per scenario and the average
latency. Then try at most 3 variations (budget low/mid/high, tag scoping on/off, query
wording) and keep the best one. Show me the before/after table.
```

**Day 3: speed, fallbacks and the Health Brief**

```text
Task: make memory demo-safe.
1. Add a 60-second in-process cache for recall_for_incident() keyed by fingerprint, so the
   memory ON/OFF comparison and replays stay fast.
2. If Hindsight is slow (> 5 s) or down, the agent must continue without memory, and the
   Diagnosis must still return. Add a test with a fake client that raises and one that
   sleeps.
3. Improve the reflect() query so the Pipeline Health Brief names tasks, counts and the top
   3 permanent fixes. Show me 2 sample outputs.
4. Draft the "How we use Hindsight" section for README.md (send it to me; P7 owns README).
```

## P3 — Data + simulation

Owns `data/` and `scripts/`. The data decides whether the demo looks real.

**Day 1: write the remaining 6 scenarios**

```text
Task: write the 6 missing failure scenarios listed in data/README.md.
Use data/scenarios/01_vendor_schema_change.yaml as the exact format reference.
For each scenario:
- log_template must look like a REAL log from that tool: Airflow task log header, then the
  real stack trace format (Spark/JVM for OOM and AnalysisException, psycopg2 for Postgres,
  SQLAlchemy IntegrityError for duplicates, Great Expectations validation output for DQ).
- vary noise (hosts, row counts, executor ids, request ids) through vars, but keep the final
  error line stable so all occurrences share ONE fingerprint.
- add a change_event when the cause is upstream; minutes_to_fix should drop over time.
The 09 wildcards: 5 one-off failures, each with a unique error, all truth.verdict NEW.
Then run `python scripts/generate_history.py` and a check that each scenario produces
exactly 1 fingerprint (use backend/app/services/fingerprint.py) and wildcards are all
different. Show me the table: scenario | occurrences | fingerprints.
```

**Day 2: make the change feed realistic, with distractors**

```text
Task: make the agent work for its answer.
1. Extend scripts/generate_history.py to add ~40 DISTRACTOR changes to change_feed.jsonl:
   unrelated deploys, schema changes on other tables, config tweaks, spread over 30 days.
   Use a new data/scenarios/_distractors.yaml so it stays data-driven.
2. Add an optional --llm-vary flag that asks Groq (key from .env, model from settings) to
   rewrite only the INFO/WARNING noise lines of each log, never the error line. Cache the
   results in data/generated/llm_cache.json so reruns are free and deterministic.
3. Print dataset stats at the end: failures per DAG, per verdict, per day.
```

**Day 3: one-command demo reset**

```text
Task: make the demo repeatable.
Check that `make demo` (reset -> generate -> seed days 1-14) followed by `make replay`
works from a clean clone. Add a --scenario filter and a --pause-on <scenario_id> flag to
scripts/replay.py so during the live demo we can stop right before the vendor schema
change failure. Time the full run and tell me how long seeding and replay take.
```

## P4 — QA + eval engineer

Owns `backend/tests/`, `backend/app/agent/eval.py` and `tests/e2e/`. P4 doesn't edit app code. They write failing tests (marked `xfail`) and a daily bug list, and Jay fixes. That keeps Jay's load manageable and gives the team a real accuracy number for the demo.

**Day 1: fingerprint + API test suite**

```text
Task: build a test suite that tells Jay exactly what's broken.
Read backend/app/services/fingerprint.py and backend/app/api/ (Jay owns them, I must not
edit them) and every data/scenarios/*.yaml.
1. Generate 5 sample logs per scenario (import the render logic from
   scripts/generate_history.py) and write parametrized tests in
   backend/tests/test_fingerprint.py: same scenario -> same fingerprint, different
   scenarios -> different fingerprints, correct error_class. Include tricky formats: JVM
   "Caused by:" chains (deepest cause should win), psycopg2 multi-line errors, Great
   Expectations output with no Exception line.
2. Extend backend/tests/test_api.py to cover every endpoint in README.md (offline mode).
Mark known failures with pytest.mark.xfail(reason=...) and write them as a bug list for
Jay: input, expected, actual.
```

**Day 2: agent eval harness + end-to-end smoke test**

```text
Task: build backend/app/agent/eval.py, run with `python -m app.agent.eval`.
For each record in data/generated/incidents.jsonl with day >= 15: build the Incident (use
services/demo_data.to_incident), run FailureDoctor with memory ON and with memory OFF, and
compare the verdict to truth.verdict. Print a table: scenario | accuracy OFF |
accuracy ON | avg confidence | avg latency, and save raw results to
data/generated/eval_results.jsonl so Jay can inspect misses. Add --scenario and --limit
flags so a run takes under 2 minutes on the Groq free tier.
Then write tests/e2e/smoke.sh: against a running stack, reset the demo, generate data, seed
days 1-14, replay days 15-17 with auto-feedback, and assert via the API that at least one
incident is KNOWN_ISSUE or UPSTREAM_CHANGE with evidence, /api/reflect returns text, and
the learning curve has points. Exit non-zero with a clear message on failure.
```

**Day 3: try to break it before the judges do**

```text
Task: write backend/tests/test_edge_cases.py for: a 5 MB log_tail, an empty log, a
non-UTF8 byte in the log, occurred_at without a timezone, feedback posted twice,
rediagnose on an incident with no diagnosis, Hindsight down, Groq returning garbage, and
10 concurrent POST /api/failures (SQLite locking). Run them, mark failures xfail, and give
me the bug list for Jay sorted by how likely each one is to break the live demo. Re-run the
eval and the smoke test after Jay's fixes and report the final numbers for the README.
```

## P5 — Frontend: core flow

Owns `frontend/src/pages/Inbox.jsx`, `frontend/src/pages/Incident.jsx` and `frontend/src/components/`. Work with `VITE_USE_MOCKS=true npm run dev` until the API is live.

**Day 1: inbox and incident page on mocks**

```text
Task: make the incident inbox and incident page demo-quality, using mock data.
Run with VITE_USE_MOCKS=true. Read frontend/src/api/client.js and src/mocks/incidents.js
(their shapes match backend/app/models/schemas.py: do not change the shapes).
Inbox.jsx: add filter chips for verdict and status, a DAG dropdown, relative times
("3 min ago"), and a subtle highlight for rows that arrived in the last 10 seconds
(the replay adds them live). Incident.jsx: put the diagnosis card first, with the
verdict, confidence bar and "Page / Don't page" as the visual hierarchy an on-call
engineer reads at 3 AM. Tailwind only, no new UI libraries. Plan first, then implement.
```

**Day 2: wire to the live API**

```text
Task: switch the inbox and incident pages from mocks to the real API (VITE_USE_MOCKS=false,
backend on :8000 through the Vite proxy). Add loading skeletons, error states with a retry
button, and an empty state. Feedback form: after "Save to memory", show a confirmation
that says the agent learned from this incident and disable the form. Test the full
flow with `python scripts/replay.py --from-day 15 --to-day 17 --delay 3` running.
List any API problems you hit so I can send them to Jay.
```

**Day 3: demo polish**

```text
Task: polish for the demo video at 1280x800.
Evidence items in the diagnosis card: show the date, a type badge
(experience/world/observation) and highlight the matched fingerprint in the text.
Add a collapsible "Agent reasoning" panel showing model, latency and memory_used.
Check keyboard focus, colour contrast, and that nothing overflows with long error
messages. Give me a before/after list of the changes.
```

## P6 — Frontend: memory views

Owns `frontend/src/pages/Insights.jsx`, `frontend/src/pages/Memory.jsx` and a new `frontend/src/components/MemoryCompare.jsx`, which P5 drops into the incident page. These screens are where judges *see* memory, which is 25% of the score.

**Day 1: learning curve and Health Brief on mocks**

```text
Task: make the Insights page tell the story "the agent gets better every day".
Run with VITE_USE_MOCKS=true. In frontend/src/pages/Insights.jsx:
- 3 KPI tiles on top: auto-diagnosed % (latest day vs first day), verdict accuracy,
  and pages avoided, each showing the change since day 1.
- the learning-curve line chart (Recharts v3): auto-diagnosed % and accuracy %, with
  a vertical reference line labelled "memory seeded" at the first replayed day.
- the Health Brief rendered as readable sections, not one text blob.
Tailwind + Recharts only. Plan first.
```

**Day 2: memory OFF vs ON comparison + explorer**

```text
Task: build frontend/src/components/MemoryCompare.jsx: given an incident id, it calls
api.rediagnose(id, false) and shows the memory OFF and ON diagnoses side by side, with
the differences highlighted: verdict change, confidence change (+43 pts), evidence count
(0 vs 3), and specific vs generic fix. It must look dramatic but honest, since this is the
single most important frame in the demo video. Give P5 a 3-line snippet to drop it into
Incident.jsx. Then upgrade Memory.jsx: group recall results by type, show dates, and
link results whose document_id starts with "inc_" to that incident page.
```

**Day 3: demo visuals**

```text
Task: make the memory screens screenshot-ready.
Add a ?present=1 URL flag that hides dev chrome, enlarges fonts by 15% and freezes
auto-refresh, for recording. Make sure the learning curve animates once on load. Then
give me a list of the 6 best screenshots to take for everyone's articles (page, state,
what it shows).
```

## P7 — DevOps, demo + content lead

Owns `docker-compose.yml`, `Makefile`, `README.md`, `.github/`, `docs/` and `integrations/`.

**Day 1: everyone can run the stack**

```text
Task: make `docker compose up --build` work on a fresh clone on Windows (Docker
Desktop + WSL2), macOS (Apple Silicon) and Linux.
Read docker-compose.yml, both Dockerfiles, .env.example and Makefile. Check: line endings
and volume paths on Windows, ARM image availability for ghcr.io/vectorize-io/hindsight,
hot reload in the backend and frontend containers, and that the backend reaches
Hindsight at http://hindsight:8888. Add healthchecks so the backend waits for Hindsight.
Add a "Troubleshooting" section to README.md with the 5 most likely setup errors and
their fixes. Make sure .github/workflows/ci.yml passes.
```

**Day 2: demo rehearsal + Airflow (optional)**

```text
Task: first full demo rehearsal + optional Airflow.
1. From a fresh clone, follow README.md exactly: docker compose up, make demo, make replay.
   Write down every step that failed or was confusing and fix the docs, Makefile or
   compose file I own. Send anything in app code to Jay.
2. Only if Gate 2 has passed: add an optional `airflow` service to docker-compose.yml
   (profile "airflow") that mounts integrations/airflow/ so example_dag.py can be
   triggered from the Airflow UI and shows up in our inbox.
```

**Day 3: demo video + content**

```text
Task: help me produce the demo video and coordinate content.
1. Using the demo script in the project plan (hook, memory OFF, memory ON, live replay,
   correction, Health Brief), write a shot list for a 3-minute recording: for each shot,
   the screen, the exact action, the voice-over line and the duration. Do NOT use the
   word "hackathon" anywhere.
2. Write a recording checklist: reset commands, browser zoom, which tabs to pre-open.
3. Read docs/CONTENT_CHECKLIST.md and draft a short message to the team with each
   person's article angle and the deadline.
```

**For everyone: before running the content guide's article prompts**, run this first so the 7 articles don't overlap:

```text
I am <P#>, and I built <my area> in this repo. Read the files I own (see docs/TEAM.md)
and git log --author="<my name>". When you generate article titles and the draft, focus
on MY part: the decisions I made, the problems I hit, and how my part uses or supports
Hindsight memory. Use real snippets from my files. Never mention the word "hackathon",
including in hashtags.
```
