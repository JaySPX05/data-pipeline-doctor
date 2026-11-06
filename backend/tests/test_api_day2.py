"""Day 2 backend: pagination + search, metrics summary, ingest timing, background retain. Offline."""
import pytest
from fastapi.testclient import TestClient

from app.db.database import Base, SessionLocal, engine, init_db
from app.deps import get_store
from app.main import app
from app.models.schemas import Diagnosis, Verdict
from app.services import incident_service as svc


class RecordingStore:
    """Memory stand-in that records retains (no Hindsight)."""

    enabled = False

    def __init__(self):
        self.failures, self.feedback = [], []

    def recall_for_incident(self, incident, limit=8):
        return []

    def search(self, query, limit=20):
        return []

    def retain_failure(self, incident):
        self.failures.append(incident.id)

    def retain_feedback(self, incident, feedback, resolved_at=None):
        self.feedback.append((incident.id, feedback.correct))

    def status(self):
        return {"enabled": False}


def event(task="load_orders", dag="orders_etl", at="2026-10-19T03:12:44", error="KeyError: 'customer_id'"):
    return {"dag_id": dag, "task_id": task, "run_id": "r", "occurred_at": at, "log_tail": f"Traceback ...\n{error}"}


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    init_db()
    store = RecordingStore()
    app.dependency_overrides[get_store] = lambda: store
    with TestClient(app) as c:
        c.store = store
        yield c
    app.dependency_overrides.clear()


def set_diagnosis(incident_id, verdict, confidence, page=True):
    with SessionLocal() as s:
        svc.save_diagnosis(
            s, incident_id,
            Diagnosis(verdict=verdict, root_cause="r", suggested_fix="f", confidence=confidence, page_oncall=page, latency_ms=100),
        )


def test_ingest_sets_server_timing_and_retains_in_background(client):
    r = client.post("/api/failures", json=event())
    assert r.status_code == 200
    timing = r.headers["server-timing"]
    for part in ("db;dur=", "recall;dur=", "llm;dur=", "total;dur="):
        assert part in timing
    assert client.store.failures == [r.json()["id"]]


def test_timestamps_are_stored_as_utc(client):
    inc = client.post("/api/failures", json=event(at="2026-10-19T08:42:44+05:30")).json()
    assert inc["occurred_at"] == "2026-10-19T03:12:44"


def test_pagination_newest_first_with_total_header(client):
    for day in range(10, 15):
        client.post("/api/failures", json=event(at=f"2026-10-{day}T03:00:00"))
    page1 = client.get("/api/incidents?limit=2")
    page2 = client.get("/api/incidents?limit=2&offset=2")
    assert page1.headers["x-total-count"] == "5"
    days = [i["occurred_at"][:10] for i in page1.json() + page2.json()]
    assert days == ["2026-10-14", "2026-10-13", "2026-10-12", "2026-10-11"]


def test_search_and_filters(client):
    a = client.post("/api/failures", json=event()).json()
    client.post("/api/failures", json=event(task="pull_kafka", dag="events_ingest", error="KafkaTimeoutError: timed out"))
    set_diagnosis(a["id"], Verdict.UPSTREAM_CHANGE, 0.9)

    assert [i["id"] for i in client.get("/api/incidents?q=customer_id").json()] == [a["id"]]
    assert [i["id"] for i in client.get(f"/api/incidents?q={a['fingerprint']}").json()] == [a["id"]]
    assert len(client.get("/api/incidents?q=KAFKA").json()) == 1  # case-insensitive
    assert [i["id"] for i in client.get("/api/incidents?verdict=UPSTREAM_CHANGE").json()] == [a["id"]]
    assert client.get("/api/incidents?verdict=UPSTREAM_CHANGE").headers["x-total-count"] == "1"
    assert len(client.get("/api/incidents?dag_id=events_ingest").json()) == 1


def test_metrics_summary(client):
    day1 = client.post("/api/failures", json=event(at="2026-10-15T03:00:00")).json()
    day2a = client.post("/api/failures", json=event(at="2026-10-16T03:00:00")).json()
    day2b = client.post("/api/failures", json=event(task="pull_kafka", dag="events_ingest", at="2026-10-16T04:00:00")).json()
    set_diagnosis(day1["id"], Verdict.NEW, 0.3)
    set_diagnosis(day2a["id"], Verdict.KNOWN_ISSUE, 0.9)
    set_diagnosis(day2b["id"], Verdict.FLAKY, 0.85, page=False)

    fb = {"root_cause": "r", "fix": "f"}
    client.post(f"/api/incidents/{day1['id']}/feedback", json={**fb, "correct": False, "minutes_to_fix": 45})
    client.post(f"/api/incidents/{day2a['id']}/feedback", json={**fb, "correct": True, "minutes_to_fix": 5})
    client.post(f"/api/incidents/{day2b['id']}/feedback", json={**fb, "correct": True, "minutes_to_fix": 1})
    assert client.store.feedback == [(day1["id"], False), (day2a["id"], True), (day2b["id"], True)]

    s = client.get("/api/metrics/summary").json()
    assert s["total_incidents"] == 3 and s["resolved"] == 3
    assert s["by_verdict"] == {"NEW": 1, "KNOWN_ISSUE": 1, "FLAKY": 1}
    assert s["auto_diagnosed_pct"] == 66.7
    assert s["accuracy_pct"] == 66.7
    assert s["pages_avoided"] == 1
    assert s["mttr_minutes"] == 17.0
    assert s["mttr_by_day"] == [
        {"day": "2026-10-15", "resolved": 1, "mttr_minutes": 45.0},
        {"day": "2026-10-16", "resolved": 2, "mttr_minutes": 3.0},
    ]
    assert s["first_day"] == {"day": "2026-10-15", "auto_diagnosed_pct": 0.0, "accuracy_pct": 0.0, "mttr_minutes": 45.0}
    assert s["latest_day"]["auto_diagnosed_pct"] == 100.0 and s["latest_day"]["mttr_minutes"] == 3.0


def test_metrics_summary_empty(client):
    s = client.get("/api/metrics/summary").json()
    assert s["total_incidents"] == 0 and s["first_day"] is None and s["accuracy_pct"] is None
