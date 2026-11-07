#!/usr/bin/env python3
"""Reset the demo to a known state (run before every rehearsal).

  python scripts/reset_demo.py --empty     # Day 0 cold start: empty bank + empty SQLite (memory-off baseline)
  python scripts/reset_demo.py --seeded    # empty everything, then re-seed days 1-14 into Hindsight

Env: HINDSIGHT_URL (default http://localhost:8888), HINDSIGHT_API_KEY, HINDSIGHT_BANK_ID,
     DATABASE_URL (sqlite:///path) or --db-path.
Stop the backend before deleting the SQLite file, or pass --skip-db.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _common as C  # noqa: E402
import seed_memory  # noqa: E402


def sqlite_path(arg: str | None) -> Path | None:
    url = arg or os.getenv("DATABASE_URL", "")
    if url.startswith("sqlite"):
        p = url.split("///", 1)[-1]
        return Path(p) if Path(p).is_absolute() else (C.ROOT / "backend" / p)
    return Path(arg) if arg else None


def delete_bank(base_url: str, bank_id: str, api_key: str | None) -> None:
    client = C.get_hindsight(base_url, api_key)
    fn = getattr(client, "delete_bank", None)
    try:
        if fn:
            fn(bank_id=bank_id)
        else:                                     # REST fallback
            C.http_json("DELETE", f"{base_url}/v1/default/banks/{bank_id}")
        print(f"  bank '{bank_id}' deleted")
    except Exception as e:
        print(f"  could not delete bank ({type(e).__name__}: {e}); it may not exist yet, continuing")
    finally:
        C.close_quietly(client)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--empty", action="store_true")
    g.add_argument("--seeded", action="store_true")
    ap.add_argument("--base-url", default=os.getenv("HINDSIGHT_URL", "http://localhost:8888"))
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--bank-id", default=C.BANK_ID)
    ap.add_argument("--db-path", default=None)
    ap.add_argument("--skip-db", action="store_true")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    a = ap.parse_args()

    db = None if a.skip_db else sqlite_path(a.db_path)
    print(f"Mode:      {'empty' if a.empty else 'seeded'}")
    print(f"Hindsight: {a.base_url}")
    print(f"Bank:      {a.bank_id}  (will be DELETED and recreated)")
    print(f"SQLite:    {db if db else 'untouched'}")
    if a.dry_run:
        print("Dry run: nothing was changed.")
        return
    if not a.yes:
        try:
            answer = input(f"Type the bank name '{a.bank_id}' to confirm: ").strip()
        except EOFError:  # no terminal to ask (CI, piped input)
            answer = ""
            print()
        if answer != a.bank_id:
            print("Cancelled. Nothing was changed. (Pass --yes to skip this prompt.)")
            return

    print("1/3 Hindsight bank")
    delete_bank(a.base_url, a.bank_id, a.api_key)
    admin = C.get_hindsight(a.base_url, a.api_key)
    try:
        C.ensure_bank(admin, a.bank_id)
    finally:
        C.close_quietly(admin)

    print("2/3 SQLite incident store")
    if db and db.exists():
        db.unlink()
        print(f"  removed {db}  (restart the backend so it recreates the tables)")
    else:
        print("  nothing to remove (use --db-path or set DATABASE_URL if your DB lives elsewhere)")

    print("3/3 Memory")
    if a.seeded:
        seed_memory.run(bank_id=a.bank_id, base_url=a.base_url, api_key=a.api_key, workers=a.workers)
    else:
        print("  empty bank: Day 0 baseline ready (memory-off diagnosis)")
    print("Reset complete.")

if __name__ == "__main__":
    main()
