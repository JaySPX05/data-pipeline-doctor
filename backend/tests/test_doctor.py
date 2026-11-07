"""Agent loop + guardrails, with a scripted LLM and a fake memory store."""
from datetime import datetime

import pytest

from app.agent.doctor import FailureDoctor
from app.agent.guardrails import apply_guardrails
from app.agent.llm import LLMError
from app.config import settings
from app.db.database import SessionLocal, init_db
from app.models.schemas import Diagnosis, Incident, Verdict
from tests.fakes import FakeStore, ScriptedLLM, diagnosis_json, memory, reply, tool_call

FP = "fp_13e8231d"
TASK = "orders_etl.load_orders"


@pytest.fixture
def incident() -> Incident:
    return Incident(
        id="inc_test",
        dag_id="orders_etl",
        task_id="load_orders",
        run_id="r1",
        try_number=1,
        occurred_at=datetime(2026, 10, 19, 3, 12),
        error_class="KeyError",
        error_message="'customer_id'",
        fingerprint=FP,
        log_tail="Traceback ...\nKeyError: 'customer_id'",
    )


@pytest.fixture
def session():
    init_db()
    with SessionLocal() as s:
        yield s


RESOLUTION = memory(
    f"Root cause of {FP} (KeyError in {TASK}): Vendor FreshFarm renamed customer_id to cust_id. Fix: map it in vendor_schema.yaml.",
    "resolution", fp=FP, task=TASK, mid="m_res",
)


# ---------------------------------------------------------------- agent loop
def test_tool_call_then_final_answer(incident, session):
    llm = ScriptedLLM(
        reply(tool_calls=[tool_call("get_retry_history")]),
        reply(diagnosis_json(evidence_ids=["m_res"])),
    )
    doctor = FailureDoctor(store=FakeStore([RESOLUTION]), llm=llm)
    dx, trace = doctor.diagnose_with_trace(incident, session)

    assert dx.verdict == Verdict.KNOWN_ISSUE
    assert [t["name"] for t in trace.tool_calls] == ["get_retry_history"]
    assert len(trace.llm_calls) == 2
    assert dx.memory_used and dx.evidence[0].memory_id == "m_res"
    # the tool result was sent back to the model
    assert llm.requests[1]["messages"][-1]["role"] == "tool"


def test_memories_are_labelled_in_prompt(incident, session):
    llm = ScriptedLLM(reply(diagnosis_json()))
    FailureDoctor(store=FakeStore([RESOLUTION]), llm=llm).diagnose(incident, session)
    prompt = llm.requests[0]["messages"][1]["content"]
    assert "[RESOLUTION SAME FINGERPRINT]" in prompt


def test_prose_reply_is_repaired_with_json_mode(incident, session):
    llm = ScriptedLLM(
        reply("I think the vendor changed the schema again."),
        reply("```json\n" + diagnosis_json(verdict="UPSTREAM_CHANGE") + "\n```"),
    )
    dx, trace = FailureDoctor(store=FakeStore([RESOLUTION]), llm=llm).diagnose_with_trace(incident, session)
    assert trace.repaired is True
    assert llm.requests[-1]["json_mode"] is True
    assert dx.verdict == Verdict.UPSTREAM_CHANGE


def test_tool_loop_is_capped_and_forced_to_answer(incident, session):
    steps = settings.max_tool_steps
    looping = [reply(tool_calls=[tool_call("grep_log", {"pattern": f"x{i}"}, f"c{i}")]) for i in range(steps)]
    llm = ScriptedLLM(*looping, reply(diagnosis_json()))
    dx, trace = FailureDoctor(store=FakeStore([RESOLUTION]), llm=llm).diagnose_with_trace(incident, session)
    assert len(trace.tool_calls) == steps
    assert llm.requests[-1]["tools"] is None  # final call had no tools
    assert dx.verdict == Verdict.KNOWN_ISSUE


def test_duplicate_tool_calls_use_cache(incident, session):
    same = tool_call("grep_log", {"pattern": "KeyError"})
    llm = ScriptedLLM(reply(tool_calls=[same]), reply(tool_calls=[same]), reply(diagnosis_json()))
    _, trace = FailureDoctor(store=FakeStore([RESOLUTION]), llm=llm).diagnose_with_trace(incident, session)
    assert trace.tool_calls[0]["result_preview"] == trace.tool_calls[1]["result_preview"]


def test_llm_failure_falls_back_to_heuristic(incident, session):
    llm = ScriptedLLM(LLMError("groq down"))
    dx, trace = FailureDoctor(store=FakeStore([RESOLUTION]), llm=llm).diagnose_with_trace(incident, session)
    assert trace.fallback.startswith("heuristic")
    assert dx.verdict == Verdict.KNOWN_ISSUE  # heuristic still uses the matching resolution


def test_memory_off_skips_recall(incident, session):
    store = FakeStore([RESOLUTION])
    llm = ScriptedLLM(reply(diagnosis_json(verdict="NEW", confidence=0.3)))
    dx = FailureDoctor(store=store, llm=llm).diagnose(incident, session, use_memory=False)
    assert store.recall_calls == 0
    assert dx.memory_used is False
    tools = str(llm.requests[0]["tools"])
    assert "search_memory" not in tools and "get_retry_history" not in tools
    assert "TASK HISTORY" not in llm.requests[0]["messages"][1]["content"]


def test_odd_model_output_is_normalised(incident, session):
    llm = ScriptedLLM(reply(diagnosis_json(verdict="known issue", confidence="0.8", suggested_fix=["step a", "step b"])))
    dx = FailureDoctor(store=FakeStore([RESOLUTION]), llm=llm).diagnose(incident, session)
    assert dx.verdict == Verdict.KNOWN_ISSUE
    assert dx.suggested_fix == "1. step a\n2. step b"


# ---------------------------------------------------------------- prefetch
CHANGES = [
    {"id": "c1", "at": "2026-10-18T22:10:00+00:00", "type": "vendor_release", "affects_dags": ["orders_etl"],
     "text": "Vendor FreshFarm released order feed v2.5: column customer_id renamed to cust_id."},
    {"id": "c2", "at": "2026-10-19T01:00:00+00:00", "type": "deploy", "affects_dags": ["payments_daily"],
     "text": "Deployed payments aggregator v3."},
    {"id": "c3", "at": "2026-10-10T00:00:00+00:00", "type": "vendor_release", "affects_dags": ["orders_etl"],
     "text": "Too old: outside the 72h window."},
    {"id": "c4", "at": "2026-10-19T02:00:00+00:00", "type": "iam_rotation", "affects_dags": ["inventory_sync"],
     "text": "Rotated IAM key for svc-inventory-export."},
]


@pytest.fixture
def change_feed(monkeypatch):
    monkeypatch.setattr("app.agent.tools._load_change_feed", lambda: [dict(c) for c in CHANGES])


def test_changes_are_prefetched_by_dag(incident, change_feed):
    from app.agent.tools import changes_for_incident

    ids = [c["id"] for c in changes_for_incident(incident)]
    assert ids == ["c1"]  # same DAG, inside the window; other DAGs and old changes excluded
    assert [c["id"] for c in changes_for_incident(incident, keyword="aws svc-inventory")] == ["c4", "c1"]


def test_prefetched_change_is_in_prompt_and_supports_upstream_verdict(incident, session, change_feed):
    llm = ScriptedLLM(reply(diagnosis_json(verdict="UPSTREAM_CHANGE", confidence=0.85)))
    dx, trace = FailureDoctor(store=FakeStore([]), llm=llm).diagnose_with_trace(incident, session, use_memory=False)
    prompt = llm.requests[0]["messages"][1]["content"]
    assert "customer_id renamed to cust_id" in prompt and "5.0h before" in prompt
    assert trace.prefetched_changes == 1 and len(trace.llm_calls) == 1
    assert dx.verdict == Verdict.UPSTREAM_CHANGE and dx.confidence == 0.85  # not capped: change was seen


def test_memory_on_includes_task_history(incident, session):
    llm = ScriptedLLM(reply(diagnosis_json()))
    FailureDoctor(store=FakeStore([RESOLUTION]), llm=llm).diagnose(incident, session)
    assert "TASK HISTORY" in llm.requests[0]["messages"][1]["content"]


# ---------------------------------------------------------------- guardrails
def _dx(**kw) -> Diagnosis:
    base = dict(verdict=Verdict.KNOWN_ISSUE, root_cause="x", suggested_fix="y", confidence=0.9, page_oncall=True)
    base.update(kw)
    return Diagnosis(**base)


def test_known_issue_without_memory_is_downgraded(incident):
    dx = _dx()
    notes = apply_guardrails(dx, incident, evidence=[])
    assert dx.verdict == Verdict.NEW and dx.confidence <= 0.5 and notes


def test_flaky_page_policy(incident):
    flaky_mem = memory(f"{TASK} KafkaTimeoutError passed on retry", "resolution", task=TASK)
    confident = _dx(verdict=Verdict.FLAKY, confidence=0.85, page_oncall=True)
    apply_guardrails(confident, incident, [flaky_mem])
    assert confident.page_oncall is False

    unsure = _dx(verdict=Verdict.FLAKY, confidence=0.6, page_oncall=False)
    apply_guardrails(unsure, incident, [flaky_mem])
    assert unsure.page_oncall is True


def test_confidence_capped_without_fingerprint_match(incident):
    task_only = memory(f"{TASK} failed before", "failure", task=TASK)
    dx = _dx(confidence=0.99)
    apply_guardrails(dx, incident, [task_only])
    assert dx.confidence == 0.95

    dx2 = _dx(confidence=0.99)
    apply_guardrails(dx2, incident, [RESOLUTION])
    assert dx2.confidence == 0.99


def test_upstream_change_needs_a_change(incident):
    dx = _dx(verdict=Verdict.UPSTREAM_CHANGE, confidence=0.9)
    apply_guardrails(dx, incident, [])
    assert dx.verdict == Verdict.NEW and dx.confidence == 0.5

    dx2 = _dx(verdict=Verdict.UPSTREAM_CHANGE, confidence=0.9)
    apply_guardrails(dx2, incident, [], change_seen=True)
    assert dx2.confidence == 0.9


def test_contradicting_a_correction_is_penalised(incident):
    correction = memory(
        f"Correction for {FP}: the agent guessed 'flaky network', but the real root cause was an expired "
        "S3 credential after IAM key rotation. Correct fix: refresh the aws_inventory connection.",
        "correction", fp=FP, mid="m_corr",
    )
    wrong = _dx(root_cause="Transient network blip", suggested_fix="Retry the task", evidence=[RESOLUTION])
    notes = apply_guardrails(wrong, incident, [RESOLUTION, correction])
    assert wrong.confidence <= 0.5 and wrong.page_oncall is True
    assert wrong.evidence[0].memory_id == "m_corr"
    assert any("correction" in n for n in notes)

    right = _dx(root_cause="Expired S3 credential after IAM key rotation", suggested_fix="Refresh aws_inventory connection")
    apply_guardrails(right, incident, [RESOLUTION, correction])
    assert right.confidence == 0.9


def test_tool_call_signature_survives_into_next_turn(incident, session):
    from types import SimpleNamespace

    signed_call = tool_call("grep_log", {"pattern": "KeyError"})
    signed_call.extra_content = {"google": {"thought_signature": "sig=="}}
    llm = ScriptedLLM(reply(tool_calls=[signed_call]), reply(diagnosis_json()))
    FailureDoctor(store=FakeStore([RESOLUTION]), llm=llm).diagnose(incident, session)
    assistant = [m for m in llm.requests[1]["messages"] if m.get("tool_calls")][0]
    assert assistant["tool_calls"][0]["extra_content"] == {"google": {"thought_signature": "sig=="}}
