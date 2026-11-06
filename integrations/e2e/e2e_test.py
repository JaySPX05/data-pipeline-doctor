#!/usr/bin/env python3
"""End-to-end loop check: failure -> diagnosis card -> feedback -> memory -> reflect.

    python integrations/e2e/e2e_test.py --base-url http://localhost:8000

Payload matches integrations/airflow/failure_callback.py (the real schema).
If a check fails on a field name, compare with backend/app/models/ and edit the
constants below. Exit code 0 = all green.
"""
import argparse
import sys
import time

import requests

FAILURE = {
    "dag_id": "orders_etl",
    "task_id": "load_orders",
    "run_id": "manual__e2e_test",
    "try_number": 1,
    "occurred_at": "2026-11-06T03:12:00+00:00",
    "operator": "PythonOperator",
    "log_tail": (
        "Traceback (most recent call last):\n"
        "  File \"/opt/etl/load_orders.py\", line 88, in transform\n"
        "    cid = df['customer_id']\n"
        "KeyError: 'customer_id'\n"
    ),
    "recent_changes": ["vendor feed v2.3 landed 03:05"],
}
FEEDBACK = {
    "confirmed": True,
    "root_cause": "Vendor renamed customer_id to cust_id",
    "fix": "Updated column mapping in vendor_schema.yaml",
    "minutes_to_resolve": 4,
}
CARD_FIELDS = ("verdict", "root_cause", "confidence")   # add "suggested_fix", "evidence" once names are confirmed
VERDICTS = {"KNOWN_ISSUE", "FLAKY", "UPSTREAM_CHANGE", "NEW"}
GREEN, RED, END = "\033[92m", "\033[91m", "\033[0m"
failures = []


def check(name, ok, detail=""):
    print(f"{GREEN if ok else RED}[{'PASS' if ok else 'FAIL'}]{END} {name} {detail}")
    if not ok:
        failures.append(name)
    return ok


def get_id(d):
    for k in ("incident_id", "id"):
        if isinstance(d, dict) and k in d:
            return d[k]
    inc = d.get("incident") if isinstance(d, dict) else None
    return get_id(inc) if inc else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--timeout", type=int, default=90)
    a = ap.parse_args()
    s, base = requests.Session(), a.base_url.rstrip("/")

    try:
        check("health endpoint", s.get(f"{base}/health", timeout=5).status_code == 200)
    except Exception as e:
        check("health endpoint", False, str(e))
        sys.exit(1)

    # 1. ingest (the response already carries the diagnosis)
    r = s.post(f"{base}/api/failures", json=FAILURE, timeout=a.timeout)
    check("POST /api/failures", r.status_code in (200, 201), f"({r.status_code}) {r.text[:150] if not r.ok else ''}")
    if not r.ok:
        sys.exit(1)
    body = r.json()
    inc_id = get_id(body)
    check("incident id returned", inc_id is not None)
    if inc_id is None:
        sys.exit(1)

    # 2. diagnosis card: from the POST response, else poll the incident
    card = body.get("diagnosis") or body.get("card")
    deadline = time.time() + a.timeout
    while not card and time.time() < deadline:
        d = s.get(f"{base}/api/incidents/{inc_id}", timeout=10).json()
        card = d.get("diagnosis") or d.get("card")
        time.sleep(2)
    check("diagnosis card produced", bool(card))
    if card:
        for f in CARD_FIELDS:
            check(f"card has '{f}'", f in card)
        check("verdict is valid", card.get("verdict") in VERDICTS, f"({card.get('verdict')})")

    # 3. inbox
    r = s.get(f"{base}/api/incidents", timeout=10)
    items = r.json() if r.ok else []
    items = items.get("items", items) if isinstance(items, dict) else items
    check("GET /api/incidents lists it", r.ok and any(get_id(i) == inc_id for i in items))

    # 4. feedback -> retain
    r = s.post(f"{base}/api/incidents/{inc_id}/feedback", json=FEEDBACK, timeout=60)
    check("POST feedback", r.status_code in (200, 201), f"({r.status_code}) {r.text[:150] if not r.ok else ''}")

    # 5. memory should now contain it (retain may be async)
    time.sleep(5)
    r = s.get(f"{base}/api/memory/search", params={"q": "customer_id load_orders"}, timeout=30)
    check("memory search returns hits", r.ok and len(str(r.json())) > 20)

    # 6. memory OFF re-diagnosis
    r = s.post(f"{base}/api/incidents/{inc_id}/rediagnose", params={"memory": "off"}, timeout=a.timeout)
    check("rediagnose?memory=off", r.status_code in (200, 201), f"({r.status_code})")

    # 7. metrics + reflect
    check("learning-curve endpoint", s.get(f"{base}/api/metrics/learning-curve", timeout=10).ok)
    check("reflect (Health Brief)", s.post(f"{base}/api/reflect", timeout=a.timeout).ok)

    print()
    if failures:
        print(f"{RED}{len(failures)} check(s) failed: {', '.join(failures)}{END}")
        sys.exit(1)
    print(f"{GREEN}ALL CHECKS PASSED - Gate 2 loop works.{END}")


if __name__ == "__main__":
    main()
