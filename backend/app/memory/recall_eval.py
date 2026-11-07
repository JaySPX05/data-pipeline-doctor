"""Measure recall quality: does recall_for_incident() bring back the RIGHT past memories?  Owner: P2.

Prerequisites:
    python scripts/generate_history.py          # data/generated/incidents.jsonl
    python scripts/seed_memory.py --until-day 14  # bank holds days 1-14

Run from backend/:
    python -m app.memory.recall_eval                          # all variants
    python -m app.memory.recall_eval --variant baseline       # just the current settings
    python -m app.memory.recall_eval --scenario vendor_schema_change --limit 5

For every failure on day >= --from-day it builds the Incident (same fingerprinting as the
API), calls recall_for_incident(), and scores the top 3 results:

    hit = a top-3 memory has the same fingerprint (fp: tag or fp_... in the text)
          OR mentions the same root cause as truth.root_cause (>= 50% of its key words)

Prints hit@3 and average latency per scenario for each variant, and writes every result
to data/generated/recall_eval.jsonl. Pick the best variant, then set it in hindsight_store.py.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from collections import defaultdict
from datetime import datetime

from app.config import ROOT_DIR, settings
from app.memory.hindsight_store import MemoryStore
from app.services import demo_data
from app.models.schemas import Evidence, Incident
from app.services.fingerprint import fingerprint

GENERATED = ROOT_DIR / "data" / "generated"
STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "when", "were", "was", "has",
    "have", "after", "before", "then", "than", "task", "failed", "failure", "error", "re-run",
}


# ------------------------------------------------------------------ variants
class UntaggedStore(MemoryStore):
    """Same queries, no tag filters: shows what tag scoping is worth."""

    def _queries(self, incident):
        return [(q, {}) for q, _ in super()._queries(incident)]


class ShortQueryStore(MemoryStore):
    """Keyword-style wording instead of full sentences."""

    def _queries(self, incident):
        task = f"{incident.dag_id}.{incident.task_id}"
        scopes = [scope for _, scope in super()._queries(incident)]
        words = [
            f"{task} {incident.error_class} {incident.fingerprint} root cause fix",
            f"{task} flaky retry",
            f"{incident.dag_id} upstream change schema deploy vendor",
        ]
        return list(zip(words, scopes))


def make_variants() -> dict[str, MemoryStore]:
    def build(cls=MemoryStore, level="mid", parallel=True):
        store = cls(settings, cache_ttl_s=0)  # no cache: every incident is a real recall
        store.recall_level = level
        store.parallel_recall = parallel
        return store

    return {
        "baseline": build(),
        "sequential": build(parallel=False),  # same queries, one after another (the old behaviour)
        "budget_low": build(level="low"),
        "budget_high": build(level="high"),
        "no_tags": build(UntaggedStore),
        "short_query": build(ShortQueryStore),
    }


# ------------------------------------------------------------------ scoring
def key_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9_]+", text.lower()) if len(w) >= 4 and w not in STOPWORDS}


def is_hit(ev: Evidence, fp: str, truth_words: set[str]) -> bool:
    text = ev.text.lower()
    if f"fp:{fp}" in ev.tags or fp.lower() in text:
        return True
    if not truth_words:
        return False
    return len(truth_words & key_words(text)) / len(truth_words) >= 0.5


def load_events(from_day: int, scenario: str | None, limit: int | None) -> list[dict]:
    try:
        rows = demo_data.load_records(from_day=from_day, scenario=scenario)
    except FileNotFoundError as exc:
        raise SystemExit(str(exc))
    if limit:
        per: dict[str, int] = defaultdict(int)
        kept = []
        for r in rows:
            if per[r["scenario_id"]] < limit:
                per[r["scenario_id"]] += 1
                kept.append(r)
        rows = kept
    return rows


def to_incident(i: int, row: dict) -> Incident:
    # Shared loader: reads P3's flat incidents.jsonl (and the older nested layout).
    return demo_data.to_incident(row, f"eval_{i:03d}")


# ------------------------------------------------------------------ main
def run(store: MemoryStore, rows: list[dict]) -> list[dict]:
    # Untimed warm-up: open connections first, so the first variant doesn't pay a cold start.
    if rows:
        store.recall_for_incident(to_incident(0, rows[0]))
    out = []
    for i, row in enumerate(rows, 1):
        inc = to_incident(i, row)
        t0 = time.perf_counter()
        evidence = store.recall_for_incident(inc)
        ms = (time.perf_counter() - t0) * 1000
        truth_words = key_words(row["truth"]["root_cause"])
        hits = [is_hit(e, inc.fingerprint, truth_words) for e in evidence[:3]]
        out.append({
            "scenario": row["scenario_id"], "day": row["day"], "fingerprint": inc.fingerprint,
            "hit_at_3": any(hits), "first_hit_rank": hits.index(True) + 1 if any(hits) else None,
            "latency_ms": round(ms), "n_evidence": len(evidence),
            "top3": [e.text[:160] for e in evidence[:3]], "truth_root_cause": row["truth"]["root_cause"],
            "error": store.last_error,
        })
    return out


def table(name: str, results: list[dict]) -> tuple[float, float]:
    by = defaultdict(list)
    for r in results:
        by[r["scenario"]].append(r)
    print(f"\n### {name}")
    print(f"{'scenario':32} {'n':>3} {'hit@3':>7} {'avg ms':>8} {'avg mems':>9}")
    for sc, rs in sorted(by.items()):
        hit = 100 * sum(r["hit_at_3"] for r in rs) / len(rs)
        print(f"{sc:32} {len(rs):>3} {hit:>6.0f}% {sum(r['latency_ms'] for r in rs) / len(rs):>8.0f} "
              f"{sum(r['n_evidence'] for r in rs) / len(rs):>9.1f}")
    hit = 100 * sum(r["hit_at_3"] for r in results) / max(1, len(results))
    ms = sum(r["latency_ms"] for r in results) / max(1, len(results))
    print(f"{'ALL':32} {len(results):>3} {hit:>6.0f}% {ms:>8.0f}")
    return hit, ms


def main() -> int:
    ap = argparse.ArgumentParser(description="Recall quality eval (hit@3)")
    ap.add_argument("--from-day", type=int, default=15)
    ap.add_argument("--scenario", help="only this scenario id, e.g. vendor_schema_change")
    ap.add_argument("--limit", type=int, help="max incidents per scenario (keeps runs short)")
    ap.add_argument("--variant", choices=["all", "baseline", "sequential", "budget_low", "budget_high", "no_tags", "short_query"],
                    default="all")
    args = ap.parse_args()

    if not settings.memory_enabled:
        raise SystemExit("MEMORY_ENABLED=false in .env: nothing to evaluate.")
    rows = load_events(args.from_day, args.scenario, args.limit)
    if not rows:
        raise SystemExit(f"No events with day >= {args.from_day}.")

    variants = make_variants()
    names = list(variants) if args.variant == "all" else [args.variant]
    summary, all_rows = [], []
    for name in names:
        store = variants[name]
        results = run(store, rows)
        if store.failed_calls:
            print(f"\n!! {name}: {store.failed_calls} Hindsight calls failed (last: {store.last_error})")
        hit, ms = table(name, results)
        summary.append((name, hit, ms))
        all_rows += [{"variant": name, **r} for r in results]

    GENERATED.mkdir(parents=True, exist_ok=True)
    out = GENERATED / "recall_eval.jsonl"
    out.write_text("\n".join(json.dumps(r) for r in all_rows) + "\n")

    print("\n### Summary (best first)")
    for name, hit, ms in sorted(summary, key=lambda s: (-s[1], s[2])):
        print(f"  {name:12} hit@3 {hit:5.1f}%   avg {ms:6.0f} ms")
    print(f"\nRaw results: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
