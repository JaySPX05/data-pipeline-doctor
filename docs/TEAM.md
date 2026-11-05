# Team split and working rules

Jay covers two roles (agent core + backend), so P4 owns testing and evaluation and hands him a bug list each day. Replace P2-P7 with names. Each person owns a folder, so we can work in parallel without merge conflicts.
Ready-to-paste prompts for your coding agent, per role and per day: [AI_PROMPTS.md](AI_PROMPTS.md).

| Person | Role | Owns | Day 1 | Day 2 | Day 3 |
| --- | --- | --- | --- | --- | --- |
| P1 Jay | Tech lead, agent core + backend/API | `backend/app/agent/`, `prompts/`, `api/`, `services/`, `db/` | Freeze schemas; agent loop with Groq + tool calling; check endpoints on real logs | Tools on real data; verdict logic; memory ON/OFF; finish API (filters, metrics summary) | Prompt tuning from P4's eval; fix P4's bug list; demo dry runs; final merge |
| P2 | Memory engineer | `backend/app/memory/` | Hindsight running; bank + mission; retain/recall working | Recall query design; corrections; reflect → Health Brief | Recall quality; fallback when Hindsight is down; README memory section |
| P3 | Data + simulation | `data/`, `scripts/` | Remaining 6 scenario YAMLs (see data/README.md) | 30-day history + change feed; seed + replay tuned | Data realism; reset script tested |
| P4 | QA + eval engineer | `backend/tests/`, `backend/app/agent/eval.py`, `tests/e2e/` | Fingerprint + API test suite; bug list for Jay | Agent eval harness (memory ON vs OFF); e2e smoke test | Edge-case tests; final accuracy numbers for README |
| P5 | Frontend core | `frontend/src/pages/Inbox.jsx`, `Incident.jsx`, `components/` | Inbox + incident page on mocks | Wire to live API; feedback flow | Polish, loading/error states |
| P6 | Frontend memory views | `frontend/src/pages/Insights.jsx`, `Memory.jsx`, `components/MemoryCompare.jsx` | Learning curve + brief on mocks | ON/OFF comparison; memory explorer | Demo visuals; screenshots for articles |
| P7 | DevOps, demo + content | `docker-compose.yml`, `Makefile`, `README.md`, `.github/`, `docs/`, `integrations/` | Compose stack boots for everyone; CI green | Full demo rehearsal from a fresh clone; Airflow callback (optional) | Demo video; content checklist; submission |

## Gates

- **Day 1 midday:** `backend/app/models/schemas.py` frozen. Changes after this need a heads-up in the group chat.
- **Gate 1 (end of Day 1):** skeleton runs end to end on mock data; `docker compose up` works on every laptop.
- **Gate 2 (end of Day 2):** real failure → diagnosis card → feedback → retain → better next diagnosis. **The one that matters.**
- **Gate 3 (Day 3 evening):** code freeze, video recorded, repo + content submitted.

## Rules

- Branch per person: `feat/<area>` (e.g. `feat/memory-recall`). PR into `main`, Jay reviews.
- `cd backend && pytest -q` must pass before every PR.
- Standups at 10:00, 15:00 and 21:00: done / doing / blocked.
- Nobody starts a nice-to-have until Gate 2 passes.
- Never commit `.env` or API keys.
