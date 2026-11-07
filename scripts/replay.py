#!/usr/bin/env python3
"""Replay failures live against the backend (POST /api/failures), in simulated time.

  python scripts/replay.py --speed 60x --auto-feedback          # days 15-30, agent learns as it goes
  python scripts/replay.py --only S01 --from-day 15             # just the vendor-schema failures
  python scripts/replay.py --only S09c --no-feedback            # correction moment: agent gets it wrong
  python scripts/replay.py --only S09c                          # ...then re-run AFTER the engineer corrects
  python scripts/replay.py --dry-run                            # print payloads, send nothing

--auto-feedback simulates the engineer: after each diagnosis it posts the ground-truth resolution, so
Hindsight memory grows during days 15-30 (this is what makes the learning curve go up). Incidents with
hold_feedback (the S09c wildcard) are never auto-confirmed.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _common as C  # noqa: E402


def parse_speed(s: str) -> float:
    return float(s.lower().rstrip("x"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--speed", default="60x", help="simulated time multiplier, e.g. 60x or 3600x")
    ap.add_argument("--max-gap", type=float, default=3.0, help="never sleep longer than this many real seconds")
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--from-day", type=int, default=C.REPLAY_DAYS[0])
    ap.add_argument("--to-day", type=int, default=C.REPLAY_DAYS[1])
    ap.add_argument("--only", help="comma-separated scenario ids, e.g. S01,S02,S09c")
    ap.add_argument("--repeat", type=int, default=1, help="send the selected set N times")
    ap.add_argument("--auto-feedback", action="store_true", help="post ground-truth feedback after each diagnosis")
    ap.add_argument("--no-feedback", action="store_true", help="never post feedback (overrides --auto-feedback)")
    ap.add_argument("--feedback-delay", type=float, default=1.5, help="seconds to wait for diagnosis before feedback")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    speed = parse_speed(a.speed)
    only = set(a.only.split(",")) if a.only else None
    rows = [i for i in C.read_jsonl(C.INCIDENTS_PATH)
            if a.from_day <= i["day"] <= a.to_day and (not only or i["scenario_id"] in only)]
    rows.sort(key=lambda i: i["failed_at"])
    if not rows:
        sys.exit("No incidents match those filters.")
    print(f"Replaying {len(rows)} failures x{a.repeat} at {a.speed} -> {a.base_url}")

    sent = 0
    for rep in range(a.repeat):
        prev = None
        for inc in rows:
            t = datetime.fromisoformat(inc["failed_at"])
            if prev is not None and not a.dry_run:
                time.sleep(min(max((t - prev).total_seconds() / speed, 0.0), a.max_gap))
            prev = t
            label = f"[day {inc['day']:>2} {t:%H:%M}] {inc['incident_id']} {inc['dag_id']}.{inc['task_id']}"
            body = C.failure_payload(inc)
            if a.dry_run:
                print(f"{label}  (truth: {inc['truth']['verdict']}, {inc['scenario_id']})")
                continue
            try:
                resp = C.http_json("POST", f"{a.base_url}/api/failures", body)
            except Exception as e:
                print(f"{label}  POST failed: {type(e).__name__}: {e}")
                continue
            sent += 1
            new_id = resp.get("id") or resp.get("incident_id")
            agent = (resp.get("diagnosis") or {}).get("verdict") or resp.get("verdict")
            truth = inc["truth"]["verdict"]
            want_feedback = a.auto_feedback and not a.no_feedback and not inc["hold_feedback"] and new_id
            if want_feedback:
                time.sleep(a.feedback_delay)           # let the (async) diagnosis finish
                if not agent:
                    try:
                        got = C.http_json("GET", f"{a.base_url}/api/incidents/{new_id}")
                        agent = (got.get("diagnosis") or {}).get("verdict")
                    except Exception:
                        pass
            print(f"{label} -> {new_id}  agent: {agent or '?'}  truth: {truth}")
            if want_feedback:
                correct = (agent == truth)             # decides confirmed-resolution vs correction
                try:
                    C.http_json("POST", f"{a.base_url}/api/incidents/{new_id}/feedback",
                                C.feedback_payload(inc, correct=correct))
                    print(f"      feedback sent (correct={correct})")
                except Exception as e:
                    print(f"      feedback failed: {type(e).__name__}: {e}")
    print(f"Done. Sent {sent} failures.")


if __name__ == "__main__":
    main()
