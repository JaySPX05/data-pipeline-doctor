"""Load the agent's 'past' (days 1..N of history) straight into Hindsight. Owner: P2 + P3.

    python scripts/seed_memory.py --until-day 14

Retains, per historical failure: the failure event + the engineer's confirmed resolution,
plus every change-feed entry up to that day. No LLM agent calls, so it is fast.
Use scripts/replay.py for the days you want to show live in the demo.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.memory.hindsight_store import MemoryStore  # noqa: E402
from app.models.schemas import Diagnosis, Feedback, Incident, Verdict  # noqa: E402
from app.services.fingerprint import fingerprint  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--until-day", type=int, default=14)
    args = ap.parse_args()

    gen = ROOT / "data" / "generated"
    history = [json.loads(line) for line in (gen / "history.jsonl").read_text().splitlines() if line.strip()]
    changes = [json.loads(line) for line in (gen / "change_feed.jsonl").read_text().splitlines() if line.strip()]
    past = [h for h in history if h["day"] <= args.until_day]
    if not past:
        sys.exit("No history found. Run scripts/generate_history.py first.")
    cutoff = datetime.fromisoformat(past[-1]["event"]["occurred_at"]) + timedelta(days=1)

    store = MemoryStore()
    for change in changes:
        if datetime.fromisoformat(change["at"]) <= cutoff:
            store.retain_change(change)

    for i, item in enumerate(past, 1):
        ev, truth = item["event"], item["truth"]
        fp, err_cls, err_msg = fingerprint(ev["task_id"], ev["log_tail"])
        verdict = Verdict(truth["verdict"])
        incident = Incident(
            id=f"seed_{i:03d}",
            dag_id=ev["dag_id"],
            task_id=ev["task_id"],
            run_id=ev["run_id"],
            try_number=ev["try_number"],
            occurred_at=datetime.fromisoformat(ev["occurred_at"]),
            error_class=err_cls,
            error_message=err_msg,
            fingerprint=fp,
            log_tail=ev["log_tail"],
            diagnosis=Diagnosis(verdict=verdict, root_cause=truth["root_cause"], suggested_fix=truth["fix"], confidence=1.0),
        )
        store.retain_failure(incident)
        store.retain_feedback(
            incident,
            Feedback(correct=True, root_cause=truth["root_cause"], fix=truth["fix"], verdict=verdict, minutes_to_fix=truth.get("minutes_to_fix")),
            resolved_at=incident.occurred_at + timedelta(minutes=truth.get("minutes_to_fix") or 15),
        )
        print(f"[{i}/{len(past)}] day {item['day']:>2}  {ev['dag_id']}.{ev['task_id']}  {fp}  {verdict.value}")

    status = store.status()
    if status["last_error"]:
        sys.exit(f"Hindsight error: {status['last_error']}")
    print(f"Seeded {len(past)} incidents into bank '{store.bank_id}'.")


if __name__ == "__main__":
    main()
