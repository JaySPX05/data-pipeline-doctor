#!/usr/bin/env python3
"""Load the agent's "past" (days 1-14) into Hindsight.

Retains, per incident: the failure event + (confirmed resolution OR flaky retry outcome),
plus every change-feed event in the window. Each retain has context, timestamp, document_id,
metadata and tags (dag:, task:, fp:, kind:). Idempotent: re-running updates the same documents.

python scripts/seed_memory.py --dry-run          # print what would be retained
python scripts/seed_memory.py                    # seed days 1-14
python scripts/seed_memory.py --workers 1        # default; more workers hit an aiohttp timeout error
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _common as C  # noqa: E402


def build_items(day_from: int, day_to: int) -> list[dict]:
    incidents = [i for i in C.read_jsonl(C.INCIDENTS_PATH) if day_from <= i["day"] <= day_to]
    changes = [c for c in C.read_jsonl(C.CHANGES_PATH) if day_from <= c["day"] <= day_to]
    items = [it for inc in incidents for it in C.memory_items(inc)] + [C.change_memory_item(c) for c in changes]
    return sorted(items, key=lambda it: it["timestamp"])


def retain_one(client, bank_id: str, it: dict) -> None:
    kw = dict(bank_id=bank_id, content=it["content"], context=it["context"],
              timestamp=datetime.fromisoformat(it["timestamp"]), document_id=it["document_id"],
              metadata={k: str(v) for k, v in it["metadata"].items()})   # Hindsight metadata = str -> str
    for attempt in range(3):
        try:
            try:
                client.retain(tags=it["tags"], **kw)
            except TypeError:                     # older client without tags support
                client.retain(**kw)
            return
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))


def run(day_from=C.SEED_DAYS[0], day_to=C.SEED_DAYS[1], bank_id=C.BANK_ID, base_url=None,
        api_key=None, workers=1, dry_run=False) -> int:
    items = build_items(day_from, day_to)
    kinds = {}
    for it in items:
        k = next(t for t in it["tags"] if t.startswith("kind:"))
        kinds[k] = kinds.get(k, 0) + 1
    print(f"Days {day_from}-{day_to}: {len(items)} retains for bank '{bank_id}' {kinds}")
    if dry_run:
        for it in items[:6]:
            print(f"  [{it['timestamp']}] {it['document_id']}  {it['tags']}\n      {it['content']}")
        print("  ... (dry run, nothing sent)")
        return len(items)
    client = C.get_hindsight(base_url, api_key)
    C.ensure_bank(client, bank_id)
    done, failed = 0, []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(retain_one, client, bank_id, it): it for it in items}
        for f in futs:
            try:
                f.result()
                done += 1
                if done % 20 == 0:
                    print(f"  {done}/{len(items)} retained")
            except Exception as e:
                failed.append((futs[f]["document_id"], f"{type(e).__name__}: {e}"))
    print(f"Done: {done} retained, {len(failed)} failed")
    for doc, err in failed[:10]:
        print(f"  FAILED {doc}: {err}")
    return done


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-day", type=int, default=C.SEED_DAYS[0])
    ap.add_argument("--to-day", type=int, default=C.SEED_DAYS[1])
    ap.add_argument("--bank-id", default=C.BANK_ID)
    ap.add_argument("--base-url", default=None, help="default: $HINDSIGHT_URL or http://localhost:8888")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    sys.exit(0 if run(a.from_day, a.to_day, a.bank_id, a.base_url, a.api_key, a.workers, a.dry_run) else 1)
