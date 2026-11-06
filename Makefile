.PHONY: up down reset logs test e2e seed replay airflow

up:        ## start everything
	docker compose up --build -d
down:
	docker compose down
reset:     ## wipe ALL state (incidents + Hindsight memory) for a clean demo
	docker compose down -v
	docker compose up --build -d
logs:
	docker compose logs -f backend
test:
	cd backend && pytest -q
e2e:       ## full-loop check against the running stack
	python integrations/e2e/e2e_test.py --base-url http://localhost:8000
seed:      ## P3's script: load days 1-14 into Hindsight
	docker compose exec backend python scripts/seed_memory.py
replay:    ## P3's script: replay days 15-30
	docker compose exec backend python scripts/replay.py --speed 60x
airflow:   ## optional real Airflow with the failure callback
	docker compose -f docker-compose.yml -f docker-compose.airflow.yml up --build -d
