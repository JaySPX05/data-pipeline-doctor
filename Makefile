.PHONY: setup backend frontend test data demo up up-cloud down logs e2e eval seed replay reset seed-local replay-local reset-local airflow
 
# ================= Docker stack (default path) =================
 
up:               ## start everything with LOCAL Hindsight (very large image)
	docker compose up --build -d
	
up-cloud:         ## Hindsight Cloud (team default). Needs HINDSIGHT_URL_DOCKER + HINDSIGHT_API_KEY in .env
	docker compose up --build -d --no-deps backend frontend
	
down:             ## stop the stack
	docker compose down
	
logs:             ## follow backend logs
	docker compose logs -f backend
	
seed:             ## load days 1-14 into the memory bank (container WORKDIR is /app/backend)
	docker compose exec backend python /app/scripts/seed_memory.py
	
replay:           ## replay days 15-30; --auto-feedback so the agent learns and the curve rises
	docker compose exec backend python /app/scripts/replay.py --speed 60x --auto-feedback
	
reset:            ## empty bank + empty incidents, then re-seed days 1-14 (local AND Cloud)
	docker compose stop backend
	docker compose run --rm --no-deps backend python /app/scripts/reset_demo.py --seeded
	docker compose start backend
	
airflow:          ## optional real Airflow with the failure callback
	docker compose -f docker-compose.yml -f docker-compose.airflow.yml up --build -d
	
# ================= Proof / evaluation =================
 
e2e:              ## full-loop check. Memory off? run: make e2e E2E_ARGS=--no-memory
	python integrations/e2e/e2e_test.py --base-url http://localhost:8000 $(E2E_ARGS)
	
eval:             ## memory ON vs OFF evaluation, markdown output
	cd backend && python -m app.agent.eval --markdown
	
test:             ## backend unit tests
	cd backend && pytest -q
	
# ================= Local dev without Docker for the backend =================
 
setup:            ## install backend + frontend deps locally
	python -m venv .venv && . .venv/bin/activate && pip install -r backend/requirements.txt
	cd frontend && npm install
	
backend:          ## run the API on :8000 (reload on change)
	cd backend && uvicorn app.main:app --reload --port 8000
	
frontend:         ## run the UI on :5173
	cd frontend && npm run dev
	
data:             ## generate 30 days of failures + change feed
	python scripts/generate_history.py --start 2026-10-01 --seed 42
	
seed-local:       ## same as seed, but run on your machine (backend running locally)
	python scripts/seed_memory.py --until-day 14
	
replay-local:     ## same as replay, but run on your machine
	python scripts/replay.py --from-day 15 --to-day 30 --delay 2 --auto-feedback
	
reset-local:      ## same as reset, but run on your machine (wipes incidents + Hindsight bank)
	python scripts/reset_demo.py --memory
	
demo: data reset  ## full demo prep (Docker stack): generate data, reset + re-seed; then run `make replay`
 