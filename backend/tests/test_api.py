from fastapi.testclient import TestClient

from app.main import app

EVENT = {
    "dag_id": "orders_etl",
    "task_id": "load_orders",
    "run_id": "scheduled__2026-10-12T03:00:00",
    "try_number": 1,
    "occurred_at": "2026-10-12T03:12:44",
    "log_tail": "Traceback (most recent call last):\n  File \"/opt/airflow/dags/orders/load.py\", line 88\nKeyError: 'customer_id'",
}


def test_full_loop_offline():
    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"

        incident = client.post("/api/failures", json=EVENT).json()
        assert incident["status"] == "diagnosed"
        assert incident["fingerprint"].startswith("fp_")
        assert incident["diagnosis"]["verdict"] == "NEW"  # no memory in offline tests

        before = client.post(f"/api/incidents/{incident['id']}/rediagnose?memory=false").json()
        assert before["memory_used"] is False

        resolved = client.post(
            f"/api/incidents/{incident['id']}/feedback",
            json={"correct": False, "root_cause": "Vendor renamed customer_id to cust_id", "fix": "Update vendor_schema.yaml", "minutes_to_fix": 4, "verdict": "UPSTREAM_CHANGE"},
        ).json()
        assert resolved["status"] == "resolved"

        assert len(client.get("/api/incidents").json()) >= 1
        assert client.get("/api/metrics/learning-curve").json()[0]["incidents"] >= 1


def test_unknown_incident_404():
    with TestClient(app) as client:
        assert client.get("/api/incidents/nope").status_code == 404
