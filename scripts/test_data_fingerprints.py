"""Safety net for the generated data. Run:  pytest scripts/test_data_fingerprints.py -q
(or without pytest:  python scripts/test_data_fingerprints.py)

The big one: every log of a scenario MUST hash to the same fingerprint, and different scenarios to
different ones. Uses the REAL backend fingerprinter when importable (see _common.fingerprint_log),
so if the LLM/variation changes something the backend does not strip, this fails.
"""
from __future__ import annotations

import json
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _common as C  # noqa: E402
import generate_history as G  # noqa: E402

INC = C.read_jsonl(C.INCIDENTS_PATH)
CHG = {c["id"]: c for c in C.read_jsonl(C.CHANGES_PATH)}


def by_scenario():
    d = defaultdict(list)
    for i in INC:
        d[i["scenario_id"]].append(i)
    return d


def test_same_scenario_same_fingerprint():
    for sid, rows in by_scenario().items():
        assert len({r["fingerprint"] for r in rows}) == 1, f"{sid} produced several fingerprints"


def test_different_scenarios_different_fingerprints():
    fps = {sid: rows[0]["fingerprint"] for sid, rows in by_scenario().items()}
    assert len(set(fps.values())) == len(fps), f"collision in {fps}"


def test_stored_fingerprint_matches_recompute():
    for r in INC:
        assert C.fingerprint_log(r["log_tail"], r["task_id"]) == r["fingerprint"], r["incident_id"]


def test_uses_real_backend_fingerprinter():
    assert C.fingerprint_backend_name().startswith("backend"), "backend/app/services/fingerprint.py not found"


def test_backend_reads_intended_error_class():
    for r in INC:
        got = C.backend_extract_error(r["log_tail"])
        assert got and got[0] == r["error_class"], f"{r['incident_id']}: {got} vs {r['error_class']}"


def test_payloads_match_frozen_schema():
    p = C.failure_payload(INC[0])
    assert {"dag_id", "task_id", "run_id", "try_number", "occurred_at", "log_tail", "recent_changes"} <= set(p)
    assert all(isinstance(x, str) for x in p["recent_changes"])
    f = C.feedback_payload(INC[0], correct=False)
    assert isinstance(f["correct"], bool) and {"root_cause", "fix"} <= set(f)


def test_logs_are_not_identical():
    for sid, rows in by_scenario().items():
        if len(rows) > 3:
            assert len({r["log_tail"] for r in rows}) == len(rows), f"{sid} has duplicate logs"


def test_volatile_fields_are_what_varies():
    rows = by_scenario()["S01"]
    assert len({r["log_tail"].split("rows_read=")[1].split()[0] for r in rows}) > 1


def test_incident_ids_unique_and_sorted():
    ids = [i["incident_id"] for i in INC]
    assert ids == sorted(set(ids))
    times = [i["failed_at"] for i in INC]
    assert times == sorted(times)


def test_demo_requirements():
    s = by_scenario()
    seed = lambda sid: [r for r in s[sid] if r["day"] <= 14]          # noqa: E731
    replay = lambda sid: [r for r in s[sid] if r["day"] >= 15]        # noqa: E731
    assert len(seed("S01")) >= 2 and len(replay("S01")) >= 1          # Memory ON moment
    assert len(replay("S02")) >= 5 and len(seed("S02")) >= 3          # FLAKY + don't page
    assert len(s["S04"]) == 4                                         # Health Brief closing line
    assert len(s["S09c"]) == 1 and s["S09c"][0]["day"] >= 15 and s["S09c"][0]["hold_feedback"]
    assert sum(1 for k in s if k.startswith("S09")) == 5              # 5 wildcards
    assert all(r["truth"]["verdict"] == "NEW" for k in s if k.startswith("S09") for r in s[k])


def test_change_feed_precedes_caused_failures():
    n = 0
    for r in INC:
        cid = r["truth"]["caused_by_change"]
        if cid:
            n += 1
            assert datetime.fromisoformat(CHG[cid]["ts"]) < datetime.fromisoformat(r["failed_at"]), r["incident_id"]
            assert f"{r['dag_id']}.{r['task_id']}" in CHG[cid]["affects"]
    assert n == 14          # S01 x4 + S04 x4 + S05 x3 + S07 x3


def test_verdict_values_valid():
    assert {r["truth"]["verdict"] for r in INC} <= {"KNOWN_ISSUE", "FLAKY", "UPSTREAM_CHANGE", "NEW"}


def test_tasks_exist_in_dags_yaml():
    import yaml
    dags = yaml.safe_load((C.DATA_DIR / "dags.yaml").read_text())["dags"]
    for r in INC:
        assert r["task_id"] in dags[r["dag_id"]], f"{r['dag_id']}.{r['task_id']} not in dags.yaml"


def test_generation_is_deterministic():
    a, ca, _ = G.build(seed=42)
    b, cb, _ = G.build(seed=42)
    assert a == b and ca == cb


def test_memory_items_well_formed():
    for r in INC[:30]:
        for it in C.memory_items(r):
            assert it["content"] and it["context"] and it["document_id"] and it["timestamp"]
            assert {"dag_id", "task_id", "fingerprint"} <= set(it["metadata"])
            assert any(t in ("kind:failure", "kind:resolution", "kind:correction", "kind:change") for t in it["tags"]) and f"fp:{r['fingerprint']}" in it["tags"]


if __name__ == "__main__":          # tiny runner so it works without pytest
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:
                failed += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
    sys.exit(1 if failed else 0)
