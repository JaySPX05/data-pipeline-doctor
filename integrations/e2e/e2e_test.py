
#!/usr/bin/env python3
"""End-to-end loop check: failure -> diagnosis card -> feedback -> memory -> reflect.
 
    pip install requests
    python integrations/e2e/e2e_test.py --base-url http://localhost:8000
    python integrations/e2e/e2e_test.py --no-memory     # stack running with MEMORY_ENABLED=false
 
Field names follow backend/app/models/schemas.py (Diagnosis, Feedback).
Exit code 0 = all green.
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
# schemas.Feedback
FEEDBACK = {
    "correct": True,
    "root_cause": "Vendor renamed customer_id to cust_id",
    "fix": "Updated column mapping in vendor_schema.yaml",
    "verdict": "UPSTREAM_CHANGE",
    "minutes_to_fix": 4,
}
# schemas.Diagnosis
CARD_FIELDS = ("verdict", "root_cause", "confidence", "suggested_fix",
               "evidence", "page_oncall", "memory_used")
VERDICTS = {"KNOWN_ISSUE", "FLAKY", "UPSTREAM_CHANGE", "NEW"}
GREEN, RED, YELLOW, END = "\033[92m", "\033[91m", "\033[93m", "\033[0m"
failures = []
 
 
def check(name, ok, detail=""):
    print(f"{GREEN if ok else RED}[{'PASS' if ok else 'FAIL'}]{END} {name} {detail}")
    if not ok:
        failures.append(name)
    return ok
 
 
def skip(name, why):
    print(f"{YELLOW}[SKIP]{END} {name} ({why})")
 
 
def get_id(d):
    if not isinstance(d, dict):
        return None
    for k in ("incident_id", "id"):
        if k in d:
            return d[k]
    return get_id(d.get("incident"))
 
 
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument("--no-memory", action="store_true",
                    help="skip memory-dependent checks (stack started with memory off)")
    a = ap.parse_args()
    s, base = requests.Session(), a.base_url.rstrip("/")
 
    try:
        check("health endpoint", s.get(f"{base}/api/health", timeout=5).status_code == 200)
    except Exception as e:
        check("health endpoint", False, str(e))
        sys.exit(1)
 
    # 1. ingest (response already carries the diagnosis)
    r = s.post(f"{base}/api/failures", json=FAILURE, timeout=a.timeout)
    check("POST /api/failures", r.status_code in (200, 201),
          f"({r.status_code}) {'' if r.ok else r.text[:200]}")
    if not r.ok:
        sys.exit(1)
    body = r.json()
    inc_id = get_id(body)
    check("incident id returned", inc_id is not None)
    if inc_id is None:
        sys.exit(1)
 
    # 2. diagnosis card (from POST response, else poll)
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
        print(f"       memory_used={card.get('memory_used')}  page_oncall={card.get('page_oncall')}")
 
    # 3. inbox
    r = s.get(f"{base}/api/incidents", timeout=10)
    items = r.json() if r.ok else []
    items = items.get("items", items) if isinstance(items, dict) else items
    check("GET /api/incidents lists it", r.ok and any(get_id(i) == inc_id for i in items))
 
    # 4. feedback -> retain
    r = s.post(f"{base}/api/incidents/{inc_id}/feedback", json=FEEDBACK, timeout=60)
    check("POST feedback", r.status_code in (200, 201),
          f"({r.status_code}) {'' if r.ok else r.text[:200]}")
 
    # 5. memory search (needs memory ON)
    if a.no_memory:
        skip("memory search", "--no-memory")
    else:
        time.sleep(5)  # retain may be async
        r = s.get(f"{base}/api/memory/search", params={"q": "customer_id load_orders"}, timeout=30)
        check("memory search returns hits", r.ok and len(str(r.json())) > 20,
              "" if r.ok else f"({r.status_code})")
 
    # 6. memory OFF re-diagnosis
    r = s.post(f"{base}/api/incidents/{inc_id}/rediagnose", params={"memory": "off"}, timeout=a.timeout)
    check("rediagnose?memory=off", r.status_code in (200, 201), f"({r.status_code})")
 
    # 7. metrics + reflect
    check("learning-curve endpoint", s.get(f"{base}/api/metrics/learning-curve", timeout=10).ok)
    if a.no_memory:
        skip("reflect (Health Brief)", "--no-memory")
    else:
        check("reflect (Health Brief)", s.post(f"{base}/api/reflect", timeout=a.timeout).ok)
 
    print()
    if failures:
        print(f"{RED}{len(failures)} check(s) failed: {', '.join(failures)}{END}")
        sys.exit(1)
    print(f"{GREEN}ALL CHECKS PASSED{END}")
 
 
if __name__ == "__main__":
    main()