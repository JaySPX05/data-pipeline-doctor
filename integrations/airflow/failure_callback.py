"""Airflow on_failure_callback that sends every task failure to Pipeline Failure Doctor. Owner: P7.

Usage in a DAG:

    from failure_callback import notify_failure_doctor
    default_args = {"on_failure_callback": notify_failure_doctor}

Set PFD_URL in the Airflow environment (default http://localhost:8000).
"""
import os
import traceback

import requests

PFD_URL = os.environ.get("PFD_URL", "http://localhost:8000")


def notify_failure_doctor(context: dict) -> None:
    ti = context["task_instance"]
    exc = context.get("exception")
    log_tail = "".join(traceback.format_exception(exc)) if exc else "No exception captured"

    payload = {
        "dag_id": ti.dag_id,
        "task_id": ti.task_id,
        "run_id": context.get("run_id") or ti.run_id,
        "try_number": ti.try_number,
        "occurred_at": (ti.end_date or context["logical_date"]).isoformat(),
        "operator": ti.operator,
        "log_tail": log_tail[-20000:],
        "recent_changes": [],
    }
    try:
        resp = requests.post(f"{PFD_URL}/api/failures", json=payload, timeout=60)
        resp.raise_for_status()
        dx = resp.json().get("diagnosis") or {}
        print(f"[PFD] {dx.get('verdict')} ({dx.get('confidence')}): {dx.get('root_cause')}")
    except Exception as err:  # never let the callback break the DAG
        print(f"[PFD] could not reach Pipeline Failure Doctor: {err}")
