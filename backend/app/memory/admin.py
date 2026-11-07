"""Small admin commands for the memory layer.  Owner: P2.

Run from backend/:
    python -m app.memory.admin status                 # memory status: reachable? breaker? queued retains?
    python -m app.memory.admin flush                  # resend retains waiting in the retry queue
    python -m app.memory.admin brief                  # Pipeline Health Brief (also refreshes the cached copy)
    python -m app.memory.admin brief --question "Which failures are flaky and should not page anyone?"

Uses HINDSIGHT_URL / HINDSIGHT_API_KEY / HINDSIGHT_BANK_ID from .env (or the environment), so
`$env:HINDSIGHT_BANK_ID="p2-eval"` in PowerShell points it at another bank.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from app.memory.hindsight_store import MemoryStore


def main() -> int:
    ap = argparse.ArgumentParser(description="Memory admin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="show memory status")
    sub.add_parser("flush", help="resend queued retains")
    b = sub.add_parser("brief", help="print a Health Brief")
    b.add_argument("--question", help="ask something else instead of the standard Health Brief")
    args = ap.parse_args()

    store = MemoryStore()
    print(f"Hindsight: {store.cfg.hindsight_url}   bank: {store.bank_id}\n")

    if args.cmd == "status":
        print(json.dumps(store.status(), indent=2))
        return 0

    if args.cmd == "flush":
        before = len(store._read_outbox())
        sent = store.flush_outbox()
        print(f"queued before: {before}   resent: {sent}   still waiting: {before - sent if before else 0}")
        if store.last_error and sent < before:
            print(f"last error: {store.last_error[:300]}")
        return 0 if sent == before else 1

    t0 = time.perf_counter()
    text = store.reflect(args.question)
    print(text)
    print(f"\n[{(time.perf_counter() - t0):.1f} s]" + (f"   last error: {store.last_error[:200]}" if store.last_error else ""))
    return 1 if text.startswith("Memory unavailable") else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        MemoryStore.close_all()  # close Hindsight clients: no "Unclosed client session" noise
