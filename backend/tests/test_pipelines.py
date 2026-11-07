"""GET /api/pipelines: DAG inventory from data/dags.yaml + incident stats."""
from fastapi.testclient import TestClient

from app.db.database import Base, engine, init_db
from app.main import app


def test_pipelines_inventory_with_incident_stats():
    Base.metadata.drop_all(engine)
    init_db()
    with TestClient(app) as c:
        empty = c.get("/api/pipelines").json()
        assert empty["company"] == "QuickBasket"
        assert len(empty["pipelines"]) == 6
        orders = next(p for p in empty["pipelines"] if p["dag_id"] == "orders_etl")
        assert "load_orders" in orders["tasks"] and orders["incidents"] == 0 and orders["last_failure_at"] is None

        ev = {"dag_id": "orders_etl", "task_id": "load_orders", "run_id": "r",
              "occurred_at": "2026-10-19T03:12:44", "log_tail": "KeyError: 'customer_id'"}
        inc = c.post("/api/failures", json=ev).json()
        c.post("/api/failures", json={**ev, "occurred_at": "2026-10-20T03:00:00"})
        c.post(f"/api/incidents/{inc['id']}/feedback", json={"correct": True, "root_cause": "r", "fix": "f"})

        orders = next(p for p in c.get("/api/pipelines").json()["pipelines"] if p["dag_id"] == "orders_etl")
        assert orders["incidents"] == 2 and orders["open_incidents"] == 1
        assert orders["last_failure_at"] == "2026-10-20T03:00:00"
        assert orders["failing_tasks"] == {"load_orders": 2}
        assert orders["verdicts"] == {"NEW": 2}
