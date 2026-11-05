"""Generate 30 days of QuickBasket pipeline failures from data/scenarios/*.yaml. Owner: P3.

    python scripts/generate_history.py --start 2026-10-01 --seed 42

Writes data/generated/history.jsonl and data/generated/change_feed.jsonl.
Deterministic for a given seed, so the whole team sees the same demo data.

TODO(P3): optional --llm-vary flag that asks Groq to rewrite each log's noise lines.
"""
from __future__ import annotations

import argparse
import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "data" / "scenarios"
OUT = ROOT / "data" / "generated"


def pick(spec, rng: random.Random, occurrence: int, sequential: bool):
    if isinstance(spec, list):
        return spec[occurrence % len(spec)] if sequential else rng.choice(spec)
    if isinstance(spec, str) and spec.startswith("int:"):
        lo, hi = map(int, spec[4:].split("-"))
        return rng.randint(lo, hi)
    if isinstance(spec, str) and spec.startswith("hex:"):
        return "".join(rng.choice("0123456789ABCDEF") for _ in range(int(spec[4:])))
    return spec


def render(template: str, values: dict, base: datetime, rng: random.Random) -> str:
    """Fill the template; each {ts} gets a slightly later timestamp."""
    lines, t = [], base - timedelta(seconds=rng.randint(30, 90))
    for line in template.splitlines():
        t += timedelta(milliseconds=rng.randint(40, 4000))
        ts = t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}+0000"
        lines.append(line.format(ts=ts, **values))
    return "\n".join(lines)


def generate(start: datetime, seed: int) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    events, changes = [], []
    for path in sorted(SCENARIOS.glob("*.yaml")):
        sc = yaml.safe_load(path.read_text())
        sequential = set(sc.get("sequential", []))
        minutes = sc["truth"].get("minutes_to_fix", 15)
        for n, day in enumerate(sc["days"]):
            at = start + timedelta(days=day - 1, hours=sc.get("hour", 3), minutes=rng.randint(0, 50))
            values = {k: pick(v, rng, n, k in sequential) for k, v in (sc.get("vars") or {}).items()}
            values.update(
                date=at.strftime("%Y-%m-%d"),
                run_id=f"scheduled__{at:%Y-%m-%dT%H}:00:00+00:00",
            )
            values = {k: (v.format(**values) if isinstance(v, str) and "{" in v else v) for k, v in values.items()}
            truth = {k: (v.format(**values) if isinstance(v, str) else v) for k, v in sc["truth"].items()}
            truth["minutes_to_fix"] = minutes[min(n, len(minutes) - 1)] if isinstance(minutes, list) else minutes

            recent = []
            if ce := sc.get("change_event"):
                change = {
                    "id": f"chg_{uuid.UUID(int=rng.getrandbits(128)).hex[:10]}",
                    "at": (at - timedelta(hours=ce.get("hours_before", 4))).isoformat(),
                    "type": ce.get("type", "change"),
                    "affects_dags": ce.get("affects_dags", [sc["dag_id"]]),
                    "text": ce["text"].format(**values),
                }
                changes.append(change)

            events.append(
                {
                    "scenario_id": sc["id"],
                    "day": day,
                    "event": {
                        "dag_id": sc["dag_id"],
                        "task_id": sc["task_id"],
                        "run_id": values["run_id"],
                        "try_number": 1,
                        "occurred_at": at.isoformat(),
                        "operator": sc.get("operator"),
                        "recent_changes": recent,
                        "log_tail": render(sc["log_template"], values, at, rng),
                    },
                    "truth": truth,
                }
            )
    events.sort(key=lambda e: e["event"]["occurred_at"])
    changes.sort(key=lambda c: c["at"])
    return events, changes


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-10-01", help="Date of day 1 (YYYY-MM-DD)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    events, changes = generate(start, args.seed)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "history.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    (OUT / "change_feed.jsonl").write_text("\n".join(json.dumps(c) for c in changes) + "\n")
    print(f"Wrote {len(events)} failures and {len(changes)} changes to {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
