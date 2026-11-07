"""Edge cases: odd logs, guardrail boundaries, bad API input. Offline (P4 QA).

These pin down behaviour the happy-path tests don't touch, so a prompt or regex change
that quietly breaks one of them shows up in CI instead of on the diagnosis card.
"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.agent.guardrails import apply_guardrails
from app.db.database import Base, engine, init_db
from app.deps import get_store
from app.main import app
from app.models.schemas import Diagnosis, Evidence, Incident, Verdict
from app.services.fingerprint import extract_error, fingerprint

ERR = "KeyError: 'customer_id'"


# --------------------------------------------------------------------------- fingerprinting

def test_empty_log_gets_unknown_error_and_a_stable_fingerprint():
    fp1, cls, msg = fingerprint("load_orders", "")
    fp2, _, _ = fingerprint("load_orders", "   \n\n  ")
    assert cls == "UnknownError" and msg == ""
    assert fp1 == fp2 and fp1.startswith("fp_")


def test_log_without_an_error_line_falls_back_to_the_last_line():
    cls, msg = extract_error("INFO starting\nINFO rows read: 42\nTask exited with return code 1")
    assert cls == "UnknownError"
    assert msg == "Task exited with return code 1"


def test_noise_does_not_change_the_fingerprint():
    a = (
        "[2026-10-19T03:12:44.123+00:00] host worker-7.prod run 3f2b1c9e-1a2b-4c3d-9e8f-0a1b2c3d4e5f\n"
        "/opt/airflow/dags/orders/load.py rows=18234 at 0x7f3a2c\n"
        f"{ERR}"
    )
    b = (
        "[2026-11-02 22:01:09,5] host executor-12.eu run 9a8b7c6d-0000-4111-8222-333344445555\n"
        "/srv/dags/other/load.py rows=7 at 0xdeadbeef\n"
        f"{ERR}"
    )
    assert fingerprint("load_orders", a)[0] == fingerprint("load_orders", b)[0]


def test_windows_line_endings_match_unix():
    unix = f"Traceback (most recent call last):\n  File \"x.py\", line 3\n{ERR}\n"
    assert fingerprint("load_orders", unix.replace("\n", "\r\n"))[0] == fingerprint("load_orders", unix)[0]


def test_same_error_in_a_different_task_is_a_different_failure():
    assert fingerprint("load_orders", ERR)[0] != fingerprint("load_returns", ERR)[0]


def test_numbers_inside_the_message_are_normalised_but_words_are_not():
    oom1 = fingerprint("aggregate", "java.lang.OutOfMemoryError: Java heap space (requested 812 MB)")[0]
    oom2 = fingerprint("aggregate", "java.lang.OutOfMemoryError: Java heap space (requested 4096 MB)")[0]
    meta = fingerprint("aggregate", "java.lang.OutOfMemoryError: Metaspace (requested 812 MB)")[0]
    assert oom1 == oom2
    assert oom1 != meta


def test_deepest_java_cause_wins_over_the_wrapper():
    log = (
        "py4j.protocol.Py4JJavaError: An error occurred while calling o697.save.\n"
        ": org.apache.spark.SparkException: Job aborted.\n"
        "Caused by: java.sql.SQLException: Connection refused"
    )
    cls, msg = extract_error(log)
    assert cls == "SQLException" and msg == "Connection refused"


def test_very_long_messages_are_truncated():
    _, _, msg = fingerprint("t", "ValueError: " + "x" * 5000)
    assert len(msg) == 500


# --------------------------------------------------------------------------- guardrails

FP = "fp_abcd1234"


def incident(recent_changes=None) -> Incident:
    return Incident(
        id="INC-T", dag_id="orders_etl", task_id="load_orders", run_id="r", try_number=1,
        occurred_at=datetime(2026, 10, 19, 3, 12), error_class="KeyError", error_message="'customer_id'",
        fingerprint=FP, log_tail=ERR, recent_changes=recent_changes or [],
    )


def diag(verdict, confidence, page=True, root="r", fix="f") -> Diagnosis:
    return Diagnosis(verdict=verdict, root_cause=root, suggested_fix=fix, confidence=confidence, page_oncall=page)


def ev(text="past failure", tags=(), at=None) -> Evidence:
    return Evidence(text=text, tags=list(tags), occurred_at=at)


FP_EVIDENCE = ev(tags=[f"fp:{FP}", "kind:failure"])
TASK_EVIDENCE = ev(tags=["task:orders_etl.load_orders", "kind:failure"])


def test_known_issue_without_memory_becomes_new_and_pages():
    d = diag(Verdict.KNOWN_ISSUE, 0.9, page=False)
    notes = apply_guardrails(d, incident(), [])
    assert d.verdict == Verdict.NEW and d.confidence == 0.5 and d.page_oncall is True
    assert any("downgraded to NEW" in n for n in notes)


def test_flaky_with_task_history_at_threshold_skips_the_page():
    d = diag(Verdict.FLAKY, 0.8, page=True)
    apply_guardrails(d, incident(), [TASK_EVIDENCE])
    assert d.verdict == Verdict.FLAKY and d.page_oncall is False


def test_flaky_just_below_threshold_still_pages():
    d = diag(Verdict.FLAKY, 0.79, page=False)
    apply_guardrails(d, incident(), [TASK_EVIDENCE])
    assert d.page_oncall is True


def test_unsupported_upstream_change_becomes_new():
    d = diag(Verdict.UPSTREAM_CHANGE, 0.9)
    notes = apply_guardrails(d, incident(), [])
    assert d.verdict == Verdict.NEW and d.confidence == 0.5
    assert any("no recent change" in n for n in notes)


def test_a_change_from_weeks_ago_is_not_a_cause():
    # eval miss S09c: recall surfaced a same-DAG change from 7.7 days earlier
    old = ev("Deployed orders_etl v2.0", ["kind:change", "dag:orders_etl"], at=datetime(2026, 10, 11, 20, 0))
    d = diag(Verdict.UPSTREAM_CHANGE, 0.7)
    apply_guardrails(d, incident(), [old])
    assert d.verdict == Verdict.NEW


def test_a_change_memory_from_the_last_72h_supports_upstream():
    recent = ev("Vendor feed v2.5 released", ["kind:change", "dag:orders_etl"], at=datetime(2026, 10, 19, 1, 0))
    d = diag(Verdict.UPSTREAM_CHANGE, 0.9)
    apply_guardrails(d, incident(), [recent])
    assert d.verdict == Verdict.UPSTREAM_CHANGE and d.confidence == 0.9


def test_a_change_after_the_failure_does_not_count():
    later = ev("Hotfix deployed", ["kind:change", "dag:orders_etl"], at=datetime(2026, 10, 19, 9, 0))
    d = diag(Verdict.UPSTREAM_CHANGE, 0.9)
    apply_guardrails(d, incident(), [later])
    assert d.verdict == Verdict.NEW


RETRY = ev("orders_etl.load_orders failed with KeyError and passed on retry 1.",
           [f"fp:{FP}", "kind:resolution", "outcome:auto_retry"])
FIXED = ev("Root cause: vendor rename. Fix: map cust_id. Took 12 min.", [f"fp:{FP}", "kind:resolution"])


def test_known_issue_that_only_ever_passed_on_retry_is_flaky_and_does_not_page():
    # eval miss S08: 14 past dq_checks outcomes, all "passed on retry", called KNOWN_ISSUE
    d = diag(Verdict.KNOWN_ISSUE, 0.95, page=True)
    notes = apply_guardrails(d, incident(), [FP_EVIDENCE, RETRY, RETRY.model_copy()])
    assert d.verdict == Verdict.FLAKY and d.page_oncall is False
    assert any("passed on retry" in n for n in notes)


def test_retry_outcome_is_recognised_from_text_when_tags_are_lost():
    untagged = ev(f"load_orders ({FP}) failed with KeyError and passed on retry 1. 3rd time this month.")
    d = diag(Verdict.KNOWN_ISSUE, 0.9)
    apply_guardrails(d, incident(), [untagged])
    assert d.verdict == Verdict.FLAKY


def test_known_issue_with_a_real_fix_in_history_stays_known():
    d = diag(Verdict.KNOWN_ISSUE, 0.95)
    apply_guardrails(d, incident(), [RETRY, FIXED])
    assert d.verdict == Verdict.KNOWN_ISSUE


def test_known_issue_with_no_outcomes_yet_stays_known():
    d = diag(Verdict.KNOWN_ISSUE, 0.9)
    apply_guardrails(d, incident(), [FP_EVIDENCE])
    assert d.verdict == Verdict.KNOWN_ISSUE


def test_retry_rule_never_overrides_a_correction():
    corr = ev("Correction: not flaky, the vendor renamed customer_id to cust_id",
              [f"fp:{FP}", "kind:correction"], at=datetime(2026, 10, 9))
    d = diag(Verdict.KNOWN_ISSUE, 0.9, root="vendor renamed customer_id to cust_id")
    apply_guardrails(d, incident(), [RETRY, corr])
    assert d.verdict == Verdict.KNOWN_ISSUE


def test_upstream_change_backed_by_the_change_feed_is_not_capped_below_095():
    d = diag(Verdict.UPSTREAM_CHANGE, 0.9)
    apply_guardrails(d, incident(recent_changes=["FreshFarm feed v2.5 renamed customer_id"]), [])
    assert d.confidence == 0.9


def test_near_certainty_needs_an_exact_fingerprint_match():
    no_fp = diag(Verdict.KNOWN_ISSUE, 0.99)
    apply_guardrails(no_fp, incident(), [TASK_EVIDENCE])
    with_fp = diag(Verdict.KNOWN_ISSUE, 0.99)
    apply_guardrails(with_fp, incident(), [FP_EVIDENCE])
    assert no_fp.confidence == 0.95
    assert with_fp.confidence == 0.99


def test_contradicting_the_latest_correction_caps_confidence_and_cites_it_first():
    old = ev("Correction: real root cause was a null batch, fix by re-running", [f"fp:{FP}", "kind:correction"],
             at=datetime(2026, 10, 2))
    latest = ev("Correction: vendor FreshFarm renamed customer_id to cust_id; map cust_id in vendor_schema.yaml",
                [f"fp:{FP}", "kind:correction"], at=datetime(2026, 10, 9))
    d = diag(Verdict.FLAKY, 0.9, page=False, root="transient network blip", fix="retry the task")
    d.evidence = [FP_EVIDENCE]
    notes = apply_guardrails(d, incident(), [old, latest, FP_EVIDENCE])
    assert d.confidence == 0.5 and d.page_oncall is True
    assert d.evidence[0] is latest
    assert any("contradicts a past correction" in n for n in notes)


def test_agreeing_with_the_correction_keeps_confidence():
    latest = ev("Correction: vendor FreshFarm renamed customer_id to cust_id; map cust_id in vendor_schema.yaml",
                [f"fp:{FP}", "kind:correction"], at=datetime(2026, 10, 9))
    d = diag(Verdict.UPSTREAM_CHANGE, 0.9, root="FreshFarm renamed customer_id to cust_id",
             fix="map cust_id in vendor_schema.yaml")
    apply_guardrails(d, incident(), [latest])
    assert d.confidence == 0.9


def test_correction_for_a_different_fingerprint_is_ignored():
    other = ev("Correction: something unrelated entirely", ["fp:fp_00000000", "kind:correction"])
    d = diag(Verdict.UPSTREAM_CHANGE, 0.9, root="schema change upstream")
    apply_guardrails(d, incident(recent_changes=["x"]), [other])
    assert d.confidence == 0.9


# --------------------------------------------------------------------------- API input

class NullStore:
    enabled = False

    def recall_for_incident(self, incident, limit=8):
        return []

    def search(self, query, limit=20):
        return []

    def retain_failure(self, incident):
        pass

    def retain_feedback(self, incident, feedback, resolved_at=None):
        pass

    def status(self):
        return {"enabled": False}


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    init_db()
    app.dependency_overrides[get_store] = lambda: NullStore()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def failure(**over):
    body = {"dag_id": "orders_etl", "task_id": "load_orders", "run_id": "r",
            "occurred_at": "2026-10-19T03:12:44", "log_tail": f"Traceback\n{ERR}"}
    body.update(over)
    return body


def test_missing_required_fields_are_rejected(client):
    body = failure()
    del body["task_id"]
    assert client.post("/api/failures", json=body).status_code == 422


def test_empty_log_is_accepted_and_diagnosed(client):
    r = client.post("/api/failures", json=failure(log_tail=""))
    assert r.status_code == 200
    assert r.json()["error_class"] == "UnknownError"
    assert r.json()["diagnosis"]["verdict"] in {v.value for v in Verdict}


def test_unicode_log_round_trips(client):
    log = "Traceback\nValueError: café « déjà vu » 漢字 not a number"
    inc = client.post("/api/failures", json=failure(log_tail=log)).json()
    assert client.get(f"/api/incidents/{inc['id']}").json()["log_tail"] == log


def test_unknown_incident_returns_404(client):
    assert client.get("/api/incidents/INC-NOPE").status_code == 404
    body = {"correct": True, "root_cause": "r", "fix": "f"}
    assert client.post("/api/incidents/INC-NOPE/feedback", json=body).status_code == 404


def test_feedback_needs_the_correct_flag(client):
    inc = client.post("/api/failures", json=failure()).json()
    assert client.post(f"/api/incidents/{inc['id']}/feedback", json={"root_cause": "x"}).status_code == 422


def test_feedback_needs_root_cause_and_fix(client):
    inc = client.post("/api/failures", json=failure()).json()
    assert client.post(f"/api/incidents/{inc['id']}/feedback", json={"correct": True}).status_code == 422


def test_bad_filters_are_rejected_not_silently_empty(client):
    client.post("/api/failures", json=failure())
    assert client.get("/api/incidents?verdict=NOT_A_VERDICT").status_code == 422
    assert client.get("/api/incidents?status=closed").status_code == 422
    assert client.get("/api/incidents?verdict=NEW").status_code == 200
    assert client.get("/api/incidents?status=diagnosed").status_code == 200


def test_pagination_past_the_end_is_empty_with_the_real_total(client):
    client.post("/api/failures", json=failure())
    r = client.get("/api/incidents?limit=10&offset=50")
    assert r.status_code == 200 and r.json() == [] and r.headers["x-total-count"] == "1"
