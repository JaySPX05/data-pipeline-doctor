"""Wipe the incident DB and (optionally) the Hindsight bank, for a clean demo run. Owner: P3/P7.

    python scripts/reset_demo.py            # DB only
    python scripts/reset_demo.py --memory   # DB + Hindsight bank (cold start, for the memory-OFF story)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.memory.hindsight_store import MemoryStore  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--memory", action="store_true", help="Also delete the Hindsight bank")
    args = ap.parse_args()

    if settings.database_url.startswith("sqlite:///"):
        db = Path(settings.database_url.removeprefix("sqlite:///"))
        db.unlink(missing_ok=True)
        print(f"Deleted {db}")

    if args.memory:
        store = MemoryStore()
        try:
            store.client.delete_bank(bank_id=store.bank_id)
            print(f"Deleted Hindsight bank '{store.bank_id}'")
        except Exception as exc:
            print(f"Could not delete bank: {exc}")

    print("Done. Restart the backend, then run seed_memory.py and replay.py.")


if __name__ == "__main__":
    main()
