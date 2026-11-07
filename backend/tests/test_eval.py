"""Memory ON vs OFF eval: scoring, aggregation and the run loop (offline)."""
from app.agent.doctor import AgentTrace, FailureDoctor
from app.agent.eval import key_words, markdown, run_eval, score, summarise
from app.models.schemas import Diagnosis, Verdict
from app.services import demo_data
from tests.fakes import FakeStore, memory

KAFKA = {
    "scenario_id": "S02", "scenario_name": "kafka_consumer_timeout", "day": 16,
    "failed_at": "2026-10-16T02:00:00", "dag_id": "events_ingest", "task_id": "pull_kafka", "run_id": "r",
    "try_number": 1, "recent_changes": [],
    "log_tail": "kafka.errors.KafkaTimeoutError: Failed to update metadata after 60.0 secs.",
    "truth": {"verdict": "FLAKY", "root_cause": "Transient consumer-group rebalance; broker metadata timed out. Passes on retry."},
}
NEW_ONE = dict(KAFKA, scenario_id="S09a", scenario_name="wildcard", truth={"verdict": "NEW", "root_cause": "something new"})


def dx(verdict, conf=0.9, page=True, root="x"):
    return Diagnosis(verdict=verdict, root_cause=root, suggested_fix="f", confidence=conf, page_oncall=page)


def trace():
    t = AgentTrace(incident_id="i", use_memory=True)
    t.llm_calls.append({"provider": "groq", "model": "m", "ms": 1})
    return t


def test_score_flaky_right_and_wrong():
    right = score(KAFKA, dx(Verdict.FLAKY, page=False, root="consumer-group rebalance, broker metadata timed out"), trace(), True)
    assert right.correct and right.recognised and right.page_correct and right.root_cause_match
    wrong = score(KAFKA, dx(Verdict.NEW, page=True, root="network blip"), trace(), False)
    assert not wrong.correct and wrong.recognised is False and not wrong.page_correct and not wrong.root_cause_match


def test_new_truth_is_not_counted_in_recognised():
    assert score(NEW_ONE, dx(Verdict.NEW), trace(), True).recognised is None


def test_summary_and_markdown():
    rows = [
        score(KAFKA, dx(Verdict.NEW, conf=0.3), trace(), False),
        score(KAFKA, dx(Verdict.FLAKY, page=False, root="consumer-group rebalance broker metadata timed"), trace(), True),
    ]
    s = summarise(rows)
    assert s["overall"]["off"]["accuracy"] == 0.0 and s["overall"]["on"]["accuracy"] == 100.0
    assert s["by_scenario"]["S02"]["on"]["page_correct"] == 100.0
    table = markdown(s)
    assert "| Verdict accuracy | 0% | 100% |" in table and "S02 kafka_consumer_timeout" in table


def test_run_eval_memory_on_vs_off_offline():
    # Offline heuristic + a fake memory bank holding a resolution for this exact fingerprint.
    fp = demo_data.to_incident(KAFKA, "x").fingerprint
    store = FakeStore([memory(f"Root cause of {fp}: transient rebalance, passes on retry", "resolution", fp=fp, task="events_ingest.pull_kafka")])
    llm = type("NoLLM", (), {"available": False})()
    doctor = FailureDoctor(store=store, llm=llm)
    results = run_eval(doctor, [KAFKA], [False, True], log=lambda *_: None)
    off, on = results
    assert off.memory is False and off.verdict == "NEW" and off.evidence == 0
    assert on.memory is True and on.verdict == "KNOWN_ISSUE" and on.evidence == 1  # memory changes the answer
    assert store.recall_calls == 1  # OFF never touches memory


def test_key_words_ignore_noise():
    assert key_words("The column customer_id was renamed") == {"customer_id", "renamed"}
