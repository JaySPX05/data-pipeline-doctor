"""Read the generated QuickBasket demo data (P3's data/generated/incidents.jsonl). Owner: P1 (Jay).

One loader for every backend-side reader (agent CLI, memory recall_eval, P4's eval), so a change
to the data format is fixed in one place. Mirrors scripts/_common.failure_payload() so the
agent sees exactly what scripts/replay.py would POST. Also reads the older nested layout
({"event": {...}, "truth": {...}}) from the starter repo.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.config import settings
from app.models.schemas import FailureEvent, Incident
from app.services.fingerprint import fingerprint
from app.services.incident_service import to_utc_naive

_OPERATORS = {
    "aggregate": "SparkSubmitOperator",
    "join_spend": "SparkSubmitOperator",
    "dq_checks": "GreatExpectationsOperator",
    "export_s3": "PythonOperator",
}


def incidents_path() -> Path:
    gen = Path(settings.data_dir) / "generated"
    for name in ("incidents.jsonl", "history.jsonl"):
        if (gen / name).exists():
            return gen / name
    return gen / "incidents.jsonl"


def load_records(from_day: int | None = None, scenario: str | None = None) -> list[dict]:
    """All generated incident records, oldest first, optionally filtered."""
    path = incidents_path()
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run: python scripts/generate_history.py (from the repo root)")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if from_day is not None:
        rows = [r for r in rows if r["day"] >= from_day]
    if scenario:
        rows = [r for r in rows if r["scenario_id"] == scenario or r.get("scenario_name") == scenario]
    return rows


def _fmt_change(change) -> str:
    if isinstance(change, str):
        return change
    when = str(change.get("ts") or change.get("at") or "")[:16].replace("T", " ")
    text = change.get("summary") or change.get("text") or ""
    return f"{when} [{change.get('type', 'change')}] {text}".strip()


def to_event(record: dict) -> FailureEvent:
    """The POST /api/failures body for a record (same as scripts/_common.failure_payload)."""
    if "event" in record:  # older nested layout
        return FailureEvent.model_validate(record["event"])
    return FailureEvent(
        dag_id=record["dag_id"],
        task_id=record["task_id"],
        run_id=record["run_id"],
        try_number=record.get("try_number", 1),
        occurred_at=record.get("failed_at") or record["occurred_at"],
        log_tail=record["log_tail"],
        operator=_OPERATORS.get(record["task_id"], "PythonOperator"),
        recent_changes=[_fmt_change(c) for c in record.get("recent_changes", [])],
    )


def to_incident(record: dict, incident_id: str) -> Incident:
    """An in-memory Incident (not stored), fingerprinted like the API does it."""
    event = to_event(record)
    fp, error_class, message = fingerprint(event.task_id, event.log_tail)
    return Incident(
        id=incident_id,
        dag_id=event.dag_id,
        task_id=event.task_id,
        run_id=event.run_id,
        try_number=event.try_number,
        occurred_at=to_utc_naive(event.occurred_at),  # same normalisation as the API
        error_class=error_class,
        error_message=message,
        fingerprint=fp,
        log_tail=event.log_tail,
        recent_changes=event.recent_changes,
    )


def truth(record: dict) -> dict:
    return record["truth"]


def label(record: dict) -> str:
    name = record.get("scenario_name")
    return f"{record['scenario_id']}{' ' + name if name else ''} (day {record['day']})"


def first_per_scenario(records: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in records:
        if r["scenario_id"] not in seen:
            seen.add(r["scenario_id"])
            out.append(r)
    return out

