"""Replay generated failures against the running API. This is the live part of the demo. Owner: P3.

    python scripts/replay.py --from-day 15 --to-day 30 --delay 2 --auto-feedback

--auto-feedback plays the on-call engineer: after each diagnosis it posts the ground
truth as feedback (marking the agent right or wrong), so memory grows as the replay runs.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
COLORS = {"KNOWN_ISSUE": "\033[34m", "FLAKY": "\033[33m", "UPSTREAM_CHANGE": "\033[35m", "NEW": "\033[31m"}
RESET = "\033[0m"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--from-day", type=int, default=15)
    ap.add_argument("--to-day", type=int, default=30)
    ap.add_argument("--delay", type=float, default=1.0, help="Seconds between events")
    ap.add_argument("--auto-feedback", action="store_true")
    args = ap.parse_args()

    history = [json.loads(line) for line in (ROOT / "data/generated/history.jsonl").read_text().splitlines() if line.strip()]
    todo = [h for h in history if args.from_day <= h["day"] <= args.to_day]
    print(f"Replaying {len(todo)} failures (days {args.from_day}-{args.to_day}) against {args.api}\n")

    correct = 0
    with httpx.Client(base_url=args.api, timeout=120) as client:
        for item in todo:
            ev, truth = item["event"], item["truth"]
            incident = client.post("/api/failures", json=ev).raise_for_status().json()
            dx = incident["diagnosis"]
            hit = dx["verdict"] == truth["verdict"]
            correct += hit
            color = COLORS.get(dx["verdict"], "")
            print(
                f"day {item['day']:>2}  {ev['dag_id'] + '.' + ev['task_id']:<36} "
                f"{color}{dx['verdict']:<16}{RESET} conf={dx['confidence']:.2f}  "
                f"mem={len(dx['evidence'])}  {dx['latency_ms']:>5}ms  {'OK' if hit else 'MISS (truth: ' + truth['verdict'] + ')'}"
            )
            if args.auto_feedback:
                client.post(
                    f"/api/incidents/{incident['id']}/feedback",
                    json={
                        "correct": hit,
                        "root_cause": truth["root_cause"],
                        "fix": truth["fix"],
                        "verdict": truth["verdict"],
                        "minutes_to_fix": truth.get("minutes_to_fix"),
                        "engineer": "replay-bot",
                    },
                ).raise_for_status()
            time.sleep(args.delay)

    if todo:
        print(f"\nVerdict accuracy: {correct}/{len(todo)} ({100 * correct / len(todo):.0f}%)")


if __name__ == "__main__":
    main()
