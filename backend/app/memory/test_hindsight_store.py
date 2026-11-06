"""Tests for the Hindsight wrapper: outages, slowness, breaker, cache, tag scoping. Owner: P2.

No real Hindsight needed. Run from backend/:
    pytest -q app/memory
(pytest.ini only collects tests/ by default; P4 may move this file there.)
"""
from __future__ import annotations

import json
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from pathlib import Path

import pytest

from app.config import Settings
from app.memory.hindsight_store import HEALTH_BRIEF_QUERY, MemoryStore
from app.models.schemas import Feedback, Incident


# ---------------------------------------------------------------- helpers
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def result(text: str, doc: str | None = None, mid: str | None = None, tags=None):
    return SimpleNamespace(
        id=mid or text, text=text, type="world", document_id=doc, tags=tags or [],
        occurred_start=None, mentioned_at=None,
    )


class FakeClient:
    """Stands in for hindsight_client.Hindsight. `on_recall(kwargs)` decides each response."""

    def __init__(self, on_recall=None):
        self.calls: list[tuple[str, dict]] = []
        self.on_recall = on_recall or (lambda kw: SimpleNamespace(results=[]))
        self.create_bank_error: Exception | None = None
        self.reflect_text = "brief"

    def create_bank(self, **kw):
        self.calls.append(("create_bank", kw))
        if self.create_bank_error:
            raise self.create_bank_error

    def retain(self, **kw):
        self.calls.append(("retain", kw))

    def recall(self, **kw):
        self.calls.append(("recall", kw))
        out = self.on_recall(kw)
        if isinstance(out, Exception):
            raise out
        return out

    def reflect(self, **kw):
        self.calls.append(("reflect", kw))
        return SimpleNamespace(text=self.reflect_text)

    def count(self, name: str) -> int:
        return sum(1 for n, _ in self.calls if n == name)

    def recalls(self) -> list[dict]:
        return [kw for n, kw in self.calls if n == "recall"]


def make_store(fake: FakeClient | None = None, clock: Clock | None = None, **kw) -> tuple[MemoryStore, FakeClient, Clock]:
    fake = fake or FakeClient()
    clock = clock or Clock()
    cfg = Settings(memory_enabled=True, hindsight_url="http://fake:8888", hindsight_api_key="", hindsight_bank_id="test-bank")
    tmp = Path(tempfile.mkdtemp())
    kw.setdefault("brief_cache_path", tmp / "brief.json")  # never touch data/generated
    kw.setdefault("outbox_path", tmp / "outbox.jsonl")
    store = MemoryStore(cfg, client_factory=lambda timeout: fake, clock=clock, **kw)
    return store, fake, clock


def incident(id="inc_1", fp="fp_abc") -> Incident:
    return Incident(
        id=id, dag_id="orders_etl", task_id="load_orders", run_id="r1", try_number=1,
        occurred_at=datetime(2026, 10, 20, 3, 15, tzinfo=timezone.utc),
        error_class="KeyError", error_message="'customer_id'", fingerprint=fp, log_tail="...",
    )


# ---------------------------------------------------------------- tag scoping
def test_recall_queries_are_scoped_with_strict_tags():
    store, fake, _ = make_store(FakeClient(lambda kw: SimpleNamespace(results=[result("m")])))
    store.recall_for_incident(incident())
    by_first_tag = {q["tags"][0]: q for q in fake.recalls()}   # queries run in parallel: order varies
    q1, q2, q3 = by_first_tag["task:orders_etl.load_orders"], by_first_tag["dag:orders_etl"], by_first_tag["kind:change"]
    assert q1["tags"] == ["task:orders_etl.load_orders", "fp:fp_abc"] and q1["tags_match"] == "any_strict"
    assert q2["tags"] == ["dag:orders_etl"] and q2["tags_match"] == "any_strict"
    assert q3["tags"] == ["kind:change", "dag:orders_etl"] and q3["tags_match"] == "all_strict"
    assert all(q["query_timestamp"] == "2026-10-20T03:15:00" for q in (q1, q2, q3))


def test_falls_back_to_untagged_search_when_strict_tags_find_nothing():
    def on_recall(kw):
        if kw.get("tags"):
            return SimpleNamespace(results=[])
        return SimpleNamespace(results=[result("found by text", doc="seed_001")])

    store, fake, _ = make_store(FakeClient(on_recall))
    ev = store.recall_for_incident(incident())
    assert [e.text for e in ev] == ["found by text"]
    assert any("tags" not in q for q in fake.recalls())  # the untagged safety-net query ran


def test_dedupes_and_keeps_query_order():
    answers = {"task:orders_etl.load_orders": [result("a"), result("b")],
               "dag:orders_etl": [result("b"), result("c")], "kind:change": [result("d")]}
    store, _, _ = make_store(FakeClient(lambda kw: SimpleNamespace(results=answers[kw["tags"][0]])))
    assert [e.text for e in store.recall_for_incident(incident())] == ["a", "b", "c", "d"]


def test_never_returns_memories_from_the_incident_itself():
    res = [result("own failure", doc="inc_1"), result("own fix", doc="inc_1-resolution"), result("other", doc="inc_9")]
    store, _, _ = make_store(FakeClient(lambda kw: SimpleNamespace(results=res)))
    assert [e.text for e in store.recall_for_incident(incident())] == ["other"]


# ---------------------------------------------------------------- outages + breaker
def test_hindsight_down_returns_no_evidence_and_opens_breaker():
    store, fake, clock = make_store(FakeClient(lambda kw: ConnectionError("refused")))
    assert store.recall_for_incident(incident()) == []
    first = fake.count("recall")
    assert 1 <= first <= 3                     # the 3 queries go out at once
    assert not store.available
    assert store.status()["breaker_open_for_s"] == 30.0

    store.recall_for_incident(incident(fp="fp_other"))
    store.retain_failure(incident())
    assert fake.count("recall") == first and fake.count("retain") == 0  # nothing waits on a dead server

    clock.t += 31                              # cooldown over: try again
    fake.on_recall = lambda kw: SimpleNamespace(results=[result("back")])
    assert store.recall_for_incident(incident())[0].text == "back"
    assert store.available and store.last_error is None


def test_timeout_opens_breaker():
    store, _, _ = make_store(FakeClient(lambda kw: TimeoutError()))
    store.recall_for_incident(incident())
    assert not store.available


def test_http_error_does_not_open_breaker():
    from hindsight_client_api.exceptions import BadRequestException

    err = BadRequestException(status=400, reason="bad tags")  # what the real client raises on HTTP 400
    store, fake, _ = make_store(FakeClient(lambda kw: err))
    store.recall_for_incident(incident())
    assert store.available                      # server is up, it rejected one request
    assert fake.count("recall") == 3
    assert store.failed_calls == 3


def test_slow_hindsight_stops_at_recall_budget():
    clock = Clock()

    def slow(kw):
        clock.t += 3.0                          # every query takes 3 s
        return SimpleNamespace(results=[result(f"m{clock.t}")])

    store, fake, _ = make_store(FakeClient(slow), clock=clock, recall_budget_s=5.0)
    store.parallel_recall = False               # sequential mode: the budget is checked between queries
    ev = store.recall_for_incident(incident())
    assert fake.count("recall") == 2            # 3rd query skipped: 6 s > 5 s budget
    assert len(ev) == 2
    store.recall_for_incident(incident())
    assert fake.count("recall") == 4            # incomplete results are not cached


def test_parallel_recall_waits_for_the_slowest_query_not_the_sum():
    def slow(kw):
        time.sleep(0.3)
        return SimpleNamespace(results=[result(kw["tags"][0])])

    store, fake, _ = make_store(FakeClient(slow))
    t0 = time.perf_counter()
    ev = store.recall_for_incident(incident())
    took = time.perf_counter() - t0
    assert fake.count("recall") == 3 and len(ev) == 3
    assert took < 0.7                           # ~0.3 s in parallel, would be ~0.9 s one after another
    assert [e.text for e in ev] == ["task:orders_etl.load_orders", "dag:orders_etl", "kind:change"]


def test_parallel_recall_stops_at_the_budget_and_does_not_cache():
    def slow(kw):
        time.sleep(0.6)
        return SimpleNamespace(results=[result("late")])

    store, fake, _ = make_store(FakeClient(slow), recall_budget_s=0.2)
    t0 = time.perf_counter()
    assert store.recall_for_incident(incident()) == []   # nothing came back within 0.2 s
    assert time.perf_counter() - t0 < 0.5                 # the diagnosis did not wait for the slow server
    assert store.status()["cached_recalls"] == 0          # incomplete results are not cached


def test_real_client_against_slow_server_times_out_fast():
    """Uses the real hindsight_client against a local server that answers too slowly."""

    class Slow(BaseHTTPRequestHandler):
        def do_POST(self):
            time.sleep(2)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cfg = Settings(memory_enabled=True, hindsight_url=f"http://127.0.0.1:{srv.server_port}", hindsight_bank_id="b")
        store = MemoryStore(cfg, recall_timeout_s=0.5)
        store._bank_ready = True                # only recall is under test
        t0 = time.perf_counter()
        assert store.recall_for_incident(incident()) == []
        assert time.perf_counter() - t0 < 1.5   # one 0.5 s timeout, then the breaker skips the rest
        assert not store.available
        store.recall_client.close()
    finally:
        srv.shutdown()


# ---------------------------------------------------------------- cache
def test_recall_cache_hits_and_is_cleared_by_feedback():
    store, fake, clock = make_store(FakeClient(lambda kw: SimpleNamespace(results=[result("m")])))
    store.recall_for_incident(incident())
    store.recall_for_incident(incident(id="inc_2"))  # same fingerprint -> cache
    assert fake.count("recall") == 3

    store.retain_feedback(incident(), Feedback(correct=False, root_cause="x", fix="y"))
    store.recall_for_incident(incident())
    assert fake.count("recall") == 6            # correction must be visible immediately

    clock.t += 61
    store.recall_for_incident(incident())
    assert fake.count("recall") == 9            # expired after 60 s


# ---------------------------------------------------------------- bank, threads, misc
def test_bank_created_with_new_mission_fields_and_retried_after_failure():
    store, fake, _ = make_store()
    fake.create_bank_error = RuntimeError("boom")
    store.retain_failure(incident())
    assert fake.count("retain") == 0 and not store._bank_ready

    fake.create_bank_error = None
    store.retain_failure(incident())
    # the first memory was queued (not lost) and is resent before the second one
    assert store._bank_ready and fake.count("retain") == 2
    assert store.status()["outbox_pending"] == 0
    kw = [k for n, k in fake.calls if n == "create_bank"][-1]
    assert "reflect_mission" in kw and "retain_mission" in kw and "mission" not in kw


def test_one_client_per_thread():
    made = []
    cfg = Settings(memory_enabled=True, hindsight_url="http://fake", hindsight_bank_id="b")
    store = MemoryStore(cfg, client_factory=lambda timeout: made.append(timeout) or FakeClient())
    store._bank_ready = True
    clients = []                                 # keep references: ids of dead objects can be reused
    for _ in range(3):
        t = threading.Thread(target=lambda: clients.append(store.recall_client))
        t.start()
        t.join()
    assert len({id(c) for c in clients}) == 3
    a, b = store.recall_client, store.recall_client
    assert a is b                                # reused within a thread
    assert made.count(5.0) == 4                  # recall clients get the short timeout


def test_memory_disabled_makes_no_calls():
    made = []
    cfg = Settings(memory_enabled=False, hindsight_url="http://fake", hindsight_bank_id="b")
    store = MemoryStore(cfg, client_factory=lambda t: made.append(t) or FakeClient())
    assert store.recall_for_incident(incident()) == []
    store.retain_failure(incident())
    assert made == []


def test_reflect_uses_health_brief_and_reports_outage():
    store, fake, _ = make_store()
    assert store.reflect() == "brief"
    assert [k for n, k in fake.calls if n == "reflect"][0]["query"] == HEALTH_BRIEF_QUERY

    store2, fake2, _ = make_store(FakeClient())
    fake2.reflect = lambda **kw: (_ for _ in ()).throw(ConnectionError("down"))
    assert store2.reflect().startswith("Memory unavailable")


# ---------------------------------------------------------------- Health Brief fallback
def failing_reflect(exc):
    def reflect(**kw):
        raise exc
    return reflect


def test_health_brief_is_saved_and_served_when_reflect_fails(tmp_path):
    from hindsight_client_api.exceptions import ServiceException

    store, fake, _ = make_store(brief_cache_path=tmp_path / "brief.json")
    fake.reflect_text = "## Most failing pipelines\norders_etl.load_orders: 4 failures"
    assert store.reflect() == fake.reflect_text
    assert store.status()["health_brief_cached_at"]

    fake.reflect = failing_reflect(ServiceException(status=500, reason="Provider quota exhausted"))
    out = store.reflect()
    assert out.startswith("## Most failing pipelines")
    assert "Cached Health Brief from" in out


def test_cached_brief_survives_a_restart(tmp_path):
    path = tmp_path / "brief.json"
    store, fake, _ = make_store(brief_cache_path=path)
    fake.reflect_text = "brief v1"
    store.reflect()

    store2, fake2, _ = make_store(brief_cache_path=path)  # new process, same file
    fake2.reflect = failing_reflect(ConnectionError("down"))
    assert store2.reflect().startswith("brief v1")


def test_custom_reflect_question_never_returns_the_cached_brief(tmp_path):
    store, fake, _ = make_store(brief_cache_path=tmp_path / "brief.json")
    store.reflect()                                   # caches "brief"
    fake.reflect = failing_reflect(ConnectionError("down"))
    assert store.reflect("Which task is flakiest?").startswith("Memory unavailable")


def test_slow_reflect_does_not_switch_off_recall():
    store, fake, _ = make_store(FakeClient(lambda kw: SimpleNamespace(results=[result("m")])))
    fake.reflect = failing_reflect(TimeoutError())
    store.reflect()
    assert store.available                            # breaker still closed
    assert store.recall_for_incident(incident())      # diagnoses still get memory


def test_reflect_uses_its_own_longer_timeout():
    made = []
    cfg = Settings(memory_enabled=True, hindsight_url="http://fake", hindsight_bank_id="b")
    store = MemoryStore(cfg, client_factory=lambda t: made.append(t) or FakeClient(),
                        brief_cache_path=Path(tempfile.mkdtemp()) / "b.json")
    store._bank_ready = True
    store.reflect()
    assert 60.0 in made


def test_memory_off_reflect_says_so(tmp_path):
    cfg = Settings(memory_enabled=False, hindsight_url="http://fake", hindsight_bank_id="b")
    path = tmp_path / "brief.json"
    path.write_text(json.dumps({"text": "old brief", "generated_at": "x"}))
    store = MemoryStore(cfg, brief_cache_path=path)
    assert "switched off" in store.reflect()


# ---------------------------------------------------------------- retry queue (outbox)
def test_failed_retain_is_queued_and_resent_first():
    store, fake, clock = make_store()
    fake.retain = lambda **kw: (_ for _ in ()).throw(ConnectionError("down"))
    store.retain_feedback(incident(), Feedback(correct=False, root_cause="vendor rename", fix="map cust_id"))
    assert store.status()["outbox_pending"] == 1          # kept, not lost

    clock.t += 31                                          # breaker closed again, Hindsight back
    sent = []
    fake.retain = lambda **kw: sent.append(kw["document_id"])
    store.retain_failure(incident(id="inc_2"))
    assert sent == ["inc_1-correction", "inc_2"]          # queued one first, then the new one
    assert store.status()["outbox_pending"] == 0


def test_queue_survives_a_restart(tmp_path):
    path = tmp_path / "outbox.jsonl"
    store, fake, _ = make_store(outbox_path=path)
    fake.retain = lambda **kw: (_ for _ in ()).throw(ConnectionError("down"))
    store.retain_failure(incident())

    store2, fake2, _ = make_store(outbox_path=path)        # new process, same file
    assert store2.status()["outbox_pending"] == 1
    assert store2.flush_outbox() == 1 and fake2.count("retain") == 1
    assert not path.exists()


def test_resent_feedback_clears_the_recall_cache():
    store, fake, clock = make_store(FakeClient(lambda kw: SimpleNamespace(results=[result("m")])))
    store.recall_for_incident(incident())                  # cached
    fake.retain = lambda **kw: (_ for _ in ()).throw(TimeoutError())
    store.retain_feedback(incident(), Feedback(correct=False, root_cause="x", fix="y"))
    clock.t += 31
    fake.retain = lambda **kw: None
    recalls = fake.count("recall")
    store.recall_for_incident(incident())                  # re-cached before the correction is in
    assert store.flush_outbox() == 1
    store.recall_for_incident(incident())
    assert fake.count("recall") > recalls + 3              # cache was cleared after the resend


def test_poison_item_is_dropped_after_max_attempts():
    from hindsight_client_api.exceptions import BadRequestException

    store, fake, _ = make_store()
    fake.retain = lambda **kw: (_ for _ in ()).throw(BadRequestException(status=400, reason="bad"))
    store.retain_failure(incident())                       # queued (attempts 0)
    for _ in range(5):
        store.flush_outbox()
    assert store.status()["outbox_pending"] == 0           # gave up instead of blocking the queue


def test_memory_off_queues_nothing(tmp_path):
    cfg = Settings(memory_enabled=False, hindsight_url="http://fake", hindsight_bank_id="b")
    store = MemoryStore(cfg, outbox_path=tmp_path / "o.jsonl", brief_cache_path=tmp_path / "b.json")
    store.retain_failure(incident())
    assert not (tmp_path / "o.jsonl").exists()


# ---------------------------------------------------------------- own observations
def test_rediagnose_skips_observations_of_its_own_fingerprint():
    from app.models.schemas import Diagnosis, Verdict

    obs = result("summary of this incident's own correction", doc=None, tags=["fp:fp_abc", "kind:correction"])
    other = result("resolution from an earlier incident", doc="inc_0-resolution", tags=["fp:fp_abc"])
    store, _, _ = make_store(FakeClient(lambda kw: SimpleNamespace(results=[obs, other])))

    fresh = incident()                                     # first diagnosis: keep everything
    assert [e.text for e in store.recall_for_incident(fresh)] == [obs.text, other.text]

    again = incident()
    again.diagnosis = Diagnosis(verdict=Verdict.NEW, root_cause="?", suggested_fix="?", confidence=0.3)
    assert [e.text for e in store.recall_for_incident(again)] == [other.text]
