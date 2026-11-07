"""Gate 2 check, memory side: failure -> diagnosis -> feedback -> retain -> BETTER next diagnosis.  Owner: P2.

Run from backend/ with Hindsight running:
    python -m app.memory.loop_check            # throwaway bank + temp database, deleted afterwards
    python -m app.memory.loop_check --keep     # keep the test bank to inspect it in the Hindsight UI
    python -m app.memory.loop_check --pace 0   # no pauses (paid LLM tier / Hindsight Cloud)

Drives Jay's real API code in-process (FastAPI TestClient), so it needs no backend server:
  1. POST /api/failures                 vendor renamed customer_id -> first diagnosis (no memory yet)
  2. POST /api/incidents/{id}/feedback  engineer corrects it with the real root cause -> retained
  3. POST /api/failures                 the same failure a week later -> must use that memory
  4. POST /api/incidents/{id}/rediagnose?memory=false   same incident, memory OFF, for comparison

Strict: it passes only if Hindsight returns the engineer's CORRECTION for the second failure.
The agent also reads earlier incidents straight from the database ("task history"), so a better
second diagnosis alone does not prove that memory worked.

Uses its own Hindsight bank (gate2-check-xxxxxx) and a temporary SQLite file, so the real demo
bank and data/pfd.sqlite3 are never touched. Costs ~3 Hindsight retains + 2 agent diagnoses.
Exit code 0 = the loop works, 1 = it doesn't, 2 = setup problem.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

# Isolate BEFORE app.config is imported: settings are read once, at import time.
BANK = f"gate2-check-{uuid.uuid4().hex[:6]}"
_TMP = Path(tempfile.mkdtemp(prefix="gate2-"))
os.environ["HINDSIGHT_BANK_ID"] = BANK
os.environ["MEMORY_ENABLED"] = "true"
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'gate2.sqlite3').as_posix()}"

import argparse  # noqa: E402
import logging  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.deps import get_store  # noqa: E402
from app.main import app  # noqa: E402
from app.models.schemas import Incident  # noqa: E402

LOG = """[{date}T03:{m}:02.114+0000] {{taskinstance.py:1937}} INFO - Executing <Task(PythonOperator): load_orders> on scheduled__{date}T03:00:00+00:00
[{date}T03:{m}:04.870+0000] {{load.py:41}} INFO - Reading s3://qb-landing/freshfarm/orders/{date}/part-0000.parquet from {host}
[{date}T03:{m}:09.311+0000] {{load.py:57}} INFO - Loaded {rows} rows, 23 columns
[{date}T03:{m}:09.402+0000] {{taskinstance.py:1937}} ERROR - Task failed with exception
Traceback (most recent call last):
  File "/opt/airflow/dags/orders/load.py", line {line}, in load_orders
    df["customer_id"] = df["customer_id"].astype("int64")
  File "/usr/local/lib/python3.11/site-packages/pandas/core/frame.py", line 4102, in __getitem__
    indexer = self.columns.get_loc(key)
KeyError: 'customer_id'"""

ROOT_CAUSE = "Vendor FreshFarm renamed column customer_id to cust_id in their order feed v2.4"
FIX = "Map cust_id -> customer_id in config/vendor_schema.yaml and re-run load_orders"

results: list[tuple[str, str, str]] = []


def record(check: str, ok: bool | None, detail: str = "") -> None:
    status = "INFO" if ok is None else ("PASS" if ok else "FAIL")
    results.append((check, status, detail))
    print(f"   -> {status}: {check}" + (f" ({detail})" if detail else ""))


def event(date: str, minute: str, host: str, rows: int, line: int) -> dict:
    return {
        "dag_id": "orders_etl",
        "task_id": "load_orders",
        "run_id": f"scheduled__{date}T03:00:00+00:00",
        "occurred_at": f"{date}T03:{minute}:09",
        "operator": "PythonOperator",
        "log_tail": LOG.format(date=date, m=minute, host=host, rows=rows, line=line),
    }


def show(label: str, inc: dict) -> dict:
    d = inc.get("diagnosis") or {}
    ev = d.get("evidence") or []
    print(f"   incident   : {inc['id']}  fingerprint {inc['fingerprint']}  ({inc['error_class']})")
    print(f"   verdict    : {d.get('verdict')}   confidence {d.get('confidence')}   page on-call: {d.get('page_oncall')}")
    print(f"   memory used: {d.get('memory_used')}   evidence: {len(ev)}   model: {d.get('model')}   {d.get('latency_ms')} ms")
    print(f"   root cause : {(d.get('root_cause') or '')[:160]}")
    for e in ev[:4]:
        print(f"     - [{e.get('document_id') or e.get('type')}] {e['text'][:120]}")
    return d


def pause(seconds: float) -> None:
    if seconds > 0:
        print(f"\n   (pausing {seconds:.0f} s for the free-tier LLM rate limit)")
        time.sleep(seconds)


def main() -> int:
    ap = argparse.ArgumentParser(description="Gate 2 memory loop check")
    ap.add_argument("--keep", action="store_true", help="keep the test bank")
    ap.add_argument("--pace", type=float, default=30.0, help="seconds between steps (free Groq tier: 30)")
    args = ap.parse_args()

    store = get_store()
    print(f"Hindsight : {store.cfg.hindsight_url}   test bank: {BANK}")
    print(f"Database  : temporary ({_TMP})")

    with TestClient(app) as client:
        health = client.get("/api/health").json()
        print(f"Agent LLM : {'available (' + health['llm']['model'] + ')' if health['llm']['available'] else 'none: rule-based fallback'}")

        try:
            print("\n== 1. first failure (nothing in memory yet)")
            inc1 = client.post("/api/failures", json=event("2026-10-03", "12", "ip-10-0-3-17", 48211, 88)).json()
            d1 = show("first", inc1)
            if store.failed_calls:
                print(f"   !! Hindsight error: {store.last_error}")

            pause(args.pace)
            print("\n== 2. engineer corrects the diagnosis (this is what the agent learns from)")
            fb = {"correct": False, "root_cause": ROOT_CAUSE, "fix": FIX, "verdict": "UPSTREAM_CHANGE",
                  "minutes_to_fix": 45, "engineer": "gate2-check"}
            r = client.post(f"/api/incidents/{inc1['id']}/feedback", json=fb)
            print(f"   feedback saved: HTTP {r.status_code}, status {r.json().get('status')}")
            failed_before_2 = store.failed_calls
            record("feedback retained in Hindsight", r.status_code == 200 and store.failed_calls == 0,
                   store.last_error or "no Hindsight errors so far")

            pause(args.pace)
            print("\n== 3. the same failure a week later")
            inc2 = client.post("/api/failures", json=event("2026-10-11", "20", "ip-10-0-4-92", 61873, 91)).json()
            d2 = show("second", inc2)

            def is_correction(e: dict) -> bool:
                """The engineer's correction (or an observation built from it), NOT the plain failure event."""
                text, doc, tags = e.get("text") or "", e.get("document_id") or "", e.get("tags") or []
                return (doc == f"{inc1['id']}-correction" or "kind:correction" in tags
                        or "FreshFarm" in text or "cust_id" in text)

            recalled = [e.model_dump() for e in store.recall_for_incident(Incident(**inc2))]
            ev2 = d2.get("evidence") or []
            corr_recalled = [e for e in recalled if is_correction(e)]
            corr_cited = [e for e in ev2 if is_correction(e)]
            record("same fingerprint both times", inc1["fingerprint"] == inc2["fingerprint"], inc2["fingerprint"])
            record("recall had no Hindsight errors", store.failed_calls == failed_before_2, store.last_error or "ok")
            record("Hindsight returns the engineer's correction", bool(corr_recalled),
                   f"{len(corr_recalled)} of {len(recalled)} recalled memories"
                   + (f": {corr_recalled[0]['text'][:80]}" if corr_recalled else ""))
            record("the diagnosis cites the correction as evidence", bool(corr_cited),
                   f"{len(corr_cited)} of {len(ev2)} cited")

            print("\n== 4. same incident with memory OFF (what the database history alone gives)")
            d_off = client.post(f"/api/incidents/{inc2['id']}/rediagnose?memory=false").json()
            print(f"   verdict    : {d_off.get('verdict')}   confidence {d_off.get('confidence')}")
            print(f"   root cause : {(d_off.get('root_cause') or '')[:160]}")

            record("verdict: 1st -> 2nd (memory ON) / memory OFF", None,
                   f"{d1.get('verdict')} -> {d2.get('verdict')} / {d_off.get('verdict')}")
            record("confidence: 1st -> 2nd (memory ON) / memory OFF", None,
                   f"{d1.get('confidence')} -> {d2.get('confidence')} / {d_off.get('confidence')}")
            names = lambda d: "FreshFarm" in (d.get("root_cause") or "") or "cust_id" in (d.get("root_cause") or "")
            record("2nd root cause names the real cause", None,
                   f"memory ON: {'yes' if names(d2) else 'no'}, memory OFF: {'yes' if names(d_off) else 'no'}"
                   " (OFF can still be right: the agent reads past incidents from the database)")
        finally:
            if not args.keep:
                try:
                    store.client.delete_bank(bank_id=BANK)
                    print(f"\n(deleted test bank {BANK})")
                except Exception as exc:
                    print(f"\n(could not delete test bank {BANK}: {exc})")
            else:
                print(f"\n(kept test bank {BANK}: inspect it in the Hindsight UI)")

    logging.getLogger("asyncio").setLevel(logging.CRITICAL)  # hide harmless "Unclosed client session" noise at exit
    print("\n" + "=" * 70 + "\nGATE 2 (memory side)")
    for check, status, detail in results:
        print(f"  {status:4}  {check}" + (f"  - {detail}" if detail else ""))
    failed = any(s == "FAIL" for _, s, _ in results)
    print("=" * 70)
    print("Loop NOT working yet: paste this output to your assistant." if failed
          else "The memory loop works: feedback from one incident improved the next diagnosis.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
