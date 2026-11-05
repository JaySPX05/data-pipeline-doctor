.PHONY: setup backend frontend test data seed replay demo reset up

setup:            ## install backend + frontend deps locally
	python -m venv .venv && . .venv/bin/activate && pip install -r backend/requirements.txt
	cd frontend && npm install

backend:          ## run the API on :8000 (reload on change)
	cd backend && uvicorn app.main:app --reload --port 8000

frontend:         ## run the UI on :5173
	cd frontend && npm run dev

test:
	cd backend && pytest -q

data:             ## generate 30 days of failures + change feed
	python scripts/generate_history.py --start 2026-10-01 --seed 42

seed:             ## load days 1-14 into Hindsight (the agent's past)
	python scripts/seed_memory.py --until-day 14

replay:           ## live part of the demo: days 15-30
	python scripts/replay.py --from-day 15 --to-day 30 --delay 2 --auto-feedback

reset:            ## wipe incidents + Hindsight bank
	python scripts/reset_demo.py --memory

demo: reset data seed   ## full demo prep; then start backend and run `make replay`

up:
	docker compose up --build
