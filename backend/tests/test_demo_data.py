"""Shared loader for P3's generated data (data/generated/incidents.jsonl)."""
import json

import pytest

from app.services import demo_data

FLAT = {
    "incident_id": "INC-0042",
    "scenario_id": "S01",
    "scenario_name": "vendor_schema_change",
    "day": 19,
    "failed_at": "2026-10-19T08:42:44+05:30",
    "dag_id": "orders_etl",
    "task_id": "load_orders",
    "run_id": "scheduled__2026-10-19T03:00:00+00:00",
    "try_number": 1,
    "log_tail": "Traceback ...\nKeyError: 'customer_id'",
    "recent_changes": [{"ts": "2026-10-18T22:10:00", "type": "vendor_release", "summary": "FreshFarm feed v2.5"}],
    "truth": {"verdict": "UPSTREAM_CHANGE", "root_cause": "renamed column", "fix": "map it", "minutes_to_resolve": 4},
}
NESTED = {
    "scenario_id": "vendor_schema_change",
    "day": 3,
    "event": {
        "dag_id": "orders_etl", "task_id": "load_orders", "run_id": "r", "try_number": 1,
        "occurred_at": "2026-10-03T03:00:00", "log_tail": "KeyError: 'customer_id'", "recent_changes": [],
    },
    "truth": {"verdict": "UPSTREAM_CHANGE"},
}


def test_flat_record_matches_replay_payload():
    ev = demo_data.to_event(FLAT)
    assert ev.occurred_at.isoformat() == "2026-10-19T08:42:44+05:30"
    assert ev.recent_changes == ["2026-10-18 22:10 [vendor_release] FreshFarm feed v2.5"]
    assert ev.operator == "PythonOperator"


def test_incident_is_fingerprinted_and_utc():
    inc = demo_data.to_incident(FLAT, "x1")
    assert inc.fingerprint.startswith("fp_") and inc.error_class == "KeyError"
    assert inc.occurred_at.isoformat() == "2026-10-19T03:12:44"


def test_nested_legacy_record():
    assert demo_data.to_incident(NESTED, "x2").task_id == "load_orders"


def test_load_filter_and_first_per_scenario(tmp_path, monkeypatch):
    gen = tmp_path / "generated"
    gen.mkdir()
    rows = [dict(FLAT, day=d, scenario_id=s) for d, s in [(10, "S01"), (16, "S02"), (19, "S01"), (20, "S02")]]
    (gen / "incidents.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setattr(demo_data.settings, "data_dir", tmp_path)
    late = demo_data.load_records(from_day=15)
    assert [r["day"] for r in late] == [16, 19, 20]
    assert [(r["scenario_id"], r["day"]) for r in demo_data.first_per_scenario(late)] == [("S02", 16), ("S01", 19)]
    assert len(demo_data.load_records(scenario="vendor_schema_change")) == 4  # by name too
    assert demo_data.label(late[0]) == "S02 vendor_schema_change (day 16)"


def test_missing_file_has_a_helpful_error(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_data.settings, "data_dir", tmp_path)
    with pytest.raises(FileNotFoundError, match="generate_history.py"):
        demo_data.load_records()


def test_committed_demo_data_loads():
    records = demo_data.load_records()
    assert len(records) >= 90
    for r in demo_data.first_per_scenario(records):
        demo_data.to_incident(r, "check")  # every scenario converts cleanly
