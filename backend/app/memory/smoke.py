"""Day 1 smoke test: does our Hindsight integration work against a real server?  Owner: P2.

Run from backend/:
    python -m app.memory.smoke           # create throwaway bank, test, delete it
    python -m app.memory.smoke --keep    # keep the bank so you can inspect it in the Hindsight UI
    python -m app.memory.smoke --async-check   # also test background (async) retain under quota pressure
    python -m app.memory.smoke --pace 0        # no pause between retains (paid Groq tier / Hindsight Cloud)
    python -m app.memory.smoke --async-check --skip-reflect   # cheapest run that answers the seeding question

Groq free tier: Hindsight's fact extraction uses ~3.5K tokens per retain and the limit is 8K
tokens/minute, so retains are paced (--pace, default 30 s) and a "quota exhausted, retry at ..."
error is waited out (max 5 min) and retried. A full run takes about 3-6 minutes.

What it checks (the assumptions hindsight_store.py relies on):
  1. Hindsight is reachable with the URL/key in .env
  2. retain() accepts content + context + timestamp + document_id + metadata + tags
  3. recall() finds the memories again
  4. tags survive fact extraction (they come back on recall results)
  5. tags_match="any_strict" keeps OTHER tasks and UNTAGGED memories out
  6. tags_match="any" behaviour with untagged memories (info only)
  7. tags_match="all_strict" with task + kind narrows to resolutions of that task
  8. reflect() returns text
  9. (--async-check) retain_async=True queues work and Hindsight's worker finishes it, even
     when the burst is bigger than one minute of Groq quota

Exit code: 0 = all checks passed, 1 = a check failed, 2 = could not reach Hindsight.
Never prints the API key.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from app.config import settings

TASK = "task:orders_etl.load_orders"
OTHER_TASK = "task:payments_etl.load_payments"
FP = "fp:fp_test"
OUR_TAGS = ["dag:orders_etl", TASK, FP, "kind:resolution"]

# (document_id, content, tags, days_ago)
MEMORIES = [
    (
        "smoke-res-1",
        "Task orders_etl.load_orders failed with KeyError: 'customer_id'. Fingerprint fp_test. "
        "Root cause: vendor FreshFarm renamed customer_id to cust_id in their daily feed. "
        "Fix: map cust_id to customer_id in config/vendor_schema.yaml. Took 45 min to fix.",
        OUR_TAGS,
        10,
    ),
    (
        "smoke-res-2",
        "Task orders_etl.load_orders failed again with KeyError: 'customer_id' (fingerprint fp_test). "
        "Same FreshFarm column rename; the engineer re-applied the vendor_schema.yaml mapping. Took 12 min to fix.",
        OUR_TAGS,
        5,
    ),
    (
        "smoke-res-3",
        "Task orders_etl.load_orders hit fingerprint fp_test a third time after FreshFarm reprocessed old files. "
        "Engineer reused the vendor_schema.yaml mapping. Took 4 min to fix.",
        OUR_TAGS,
        2,
    ),
    (  # control 1: different task, must NOT appear in task-scoped recall
        "smoke-ctrl-task",
        "Task payments_etl.load_payments failed with psycopg2 OperationalError: connection to the payments "
        "replica timed out. Fingerprint fp_other. It passed on retry; known flaky, do not page.",
        ["dag:payments_etl", OTHER_TASK, "fp:fp_other", "kind:resolution"],
        3,
    ),
    (  # control 2: no tags at all, must NOT appear with *_strict matching
        "smoke-ctrl-untagged",
        "General note: the data platform team rotates the S3 credentials used by orders_etl every 90 days.",
        None,
        7,
    ),
]

QUOTA_MAX_WAIT_S = 300.0  # longest we wait for Hindsight's "retry at" before giving up

QUERY = "Past failures, root causes and fixes for task orders_etl.load_orders with KeyError customer_id"

results_table: list[tuple[str, str, str]] = []  # (check, PASS/FAIL/INFO, detail)


def record(check: str, ok: bool | None, detail: str = "") -> None:
    status = "INFO" if ok is None else ("PASS" if ok else "FAIL")
    results_table.append((check, status, detail))
    print(f"   -> {status}: {check}" + (f" ({detail})" if detail else ""))


def quota_wait_seconds(exc: BaseException) -> float | None:
    """Seconds to wait if exc is a provider-quota / rate-limit error, else None."""
    body = str(getattr(exc, "body", None) or exc)
    low = body.lower()
    if not any(k in low for k in ("quota", "rate limit", "rate_limit", "429")):
        return None
    m = re.search(r"retry at (\d{4}-\d{2}-\d{2}T[\d:.]+(?:\+00:00|Z)?)", body)
    if m:
        at = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        return max(1.0, (at - datetime.now(timezone.utc)).total_seconds() + 2)
    m = re.search(r"try again in ([\d.]+)s", body)
    return float(m.group(1)) + 2 if m else 30.0


def with_quota_retry(label: str, fn, tries: int = 3):
    """Run fn(); if Groq's quota is exhausted, wait until Hindsight says to retry, then retry."""
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except Exception as exc:
            wait = quota_wait_seconds(exc)
            if wait is None or attempt == tries:
                raise
            wait = min(wait, QUOTA_MAX_WAIT_S)
            print(f"   Groq quota exhausted; waiting {wait:.0f} s, then retrying {label} (attempt {attempt + 1}/{tries})")
            time.sleep(wait)


def short_error(exc: BaseException) -> str:
    body = str(getattr(exc, "body", None) or "")
    m = re.search(r'"detail":\s*"([^"]{0,220})', body)
    return f"{type(exc).__name__}: {m.group(1) if m else str(exc)[:220]}"


@contextmanager
def timed(label: str):
    t0 = time.perf_counter()
    print(f"\n== {label}")
    yield
    print(f"   [{(time.perf_counter() - t0) * 1000:.0f} ms]")


def doc_of(r) -> str:
    return str(getattr(r, "document_id", None) or "?")


def show(results) -> None:
    if not results:
        print("   (no results)")
    for r in results:
        tags = ",".join(getattr(r, "tags", None) or []) or "-"
        text = (r.text or "").replace("\n", " ")
        print(f"   [{doc_of(r)}] ({getattr(r, 'type', '?')}) tags={tags}\n      {text[:140]}")


def recall(client, bank_id: str, **kwargs):
    """Recall, retrying briefly in case indexing lags just after retain."""
    for attempt in range(3):
        resp = client.recall(bank_id=bank_id, query=QUERY, budget="mid", max_tokens=2048, **kwargs)
        res = list(resp.results or [])
        if res or attempt == 2:
            return res
        time.sleep(3)
    return []


ASYNC_FP = "fp:fp_async"
ASYNC_MEMORIES = [
    f"Task inventory_sync.dq_checks failed its Great Expectations null check on sku_id (fingerprint fp_async), run {i}. "
    f"Cause: warehouse sync lag; it passed on retry {i} minutes later. Known flaky, do not page."
    for i in (1, 2, 3)
]


def async_check(client, bank_id: str, now: datetime, max_wait: float) -> None:
    """Fire 3 background retains at once (more than one minute of Groq quota) and watch them finish."""
    from hindsight_client.hindsight_client import _run_async

    ops: dict[str, str | None] = {}
    with timed("9a. retain_async x3, back to back"):
        for i, content in enumerate(ASYNC_MEMORIES, 1):
            doc = f"smoke-async-{i}"
            try:
                t0 = time.perf_counter()
                resp = client.retain(
                    bank_id=bank_id, content=content, context="smoke test: async retain",
                    timestamp=now - timedelta(hours=i), document_id=doc,
                    tags=["dag:inventory_sync", "task:inventory_sync.dq_checks", ASYNC_FP, "kind:resolution"],
                    retain_async=True,
                )
                op = getattr(resp, "operation_id", None) or (getattr(resp, "operation_ids", None) or [None])[0]
                ops[doc] = op
                print(f"   {doc}: accepted in {(time.perf_counter() - t0) * 1000:.0f} ms, operation {op}")
            except Exception as exc:
                ops[doc] = None
                print(f"   {doc}: REJECTED {short_error(exc)}")
    accepted = [d for d, op in ops.items() if op]
    record("retain_async accepts a burst without failing", len(accepted) == len(ASYNC_MEMORIES),
           f"{len(accepted)}/{len(ASYNC_MEMORIES)} queued")

    final: dict[str, str] = {}
    max_retries = 0
    with timed(f"9b. wait for the worker (up to {max_wait:.0f} s)"):
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline and len(final) < len(accepted):
            for doc in accepted:
                if doc in final:
                    continue
                try:
                    st = _run_async(client.operations.get_operation_status(
                        bank_id=bank_id, operation_id=ops[doc], _request_timeout=30))
                except Exception as exc:
                    print(f"   {doc}: status check failed: {short_error(exc)}")
                    continue
                retries = getattr(st, "retry_count", 0) or 0
                max_retries = max(max_retries, retries)
                status = str(getattr(st, "status", "?"))
                if status in ("completed", "failed", "cancelled"):
                    final[doc] = status
                    err = getattr(st, "error_message", None)
                    print(f"   {doc}: {status} (retries={retries})" + (f" error={err[:160]}" if err else ""))
            if len(final) < len(accepted):
                time.sleep(10)
        for doc in accepted:
            if doc not in final:
                print(f"   {doc}: still not finished after {max_wait:.0f} s")
    done = [d for d, s in final.items() if s == "completed"]
    record("Hindsight's worker finishes queued retains", bool(accepted) and len(done) == len(accepted),
           f"{len(done)}/{len(accepted)} completed, max retries {max_retries}")
    record("worker retried after a quota error", None,
           f"yes, up to {max_retries} retries" if max_retries else "no retries were needed")

    with timed("9c. recall the async memories"):
        found = with_quota_retry("recall", lambda: client.recall(
            bank_id=bank_id, query="Is inventory_sync.dq_checks flaky?", budget="mid",
            max_tokens=2048, tags=[ASYNC_FP], tags_match="any_strict")).results or []
        show(found)
    docs = {doc_of(r) for r in found}
    record("async memories are recallable", bool(done) and docs >= set(done), f"found docs: {sorted(docs)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Hindsight smoke test")
    parser.add_argument("--keep", action="store_true", help="do not delete the throwaway bank")
    parser.add_argument("--pace", type=float, default=30.0, help="seconds to pause between retains (Groq free tier: 30)")
    parser.add_argument("--async-check", action="store_true", help="also test retain_async under quota pressure")
    parser.add_argument("--skip-reflect", action="store_true", help="skip reflect (it needs the most Groq quota)")
    parser.add_argument("--async-wait", type=float, default=420.0, help="max seconds to wait for async retains")
    args = parser.parse_args()

    from hindsight_client import Hindsight

    bank_id = f"smoke-{uuid.uuid4().hex[:8]}"
    print(f"Hindsight URL : {settings.hindsight_url}")
    print(f"API key set   : {'yes' if settings.hindsight_api_key else 'no (fine for local Docker)'}")
    print(f"Test bank     : {bank_id}")

    client = Hindsight(
        base_url=settings.hindsight_url,
        api_key=settings.hindsight_api_key or None,
        timeout=120.0,  # retain runs LLM fact extraction, so allow time here (prod wrapper uses less)
        max_attempts=1,
    )

    # 1. connectivity -------------------------------------------------------
    with timed("1. connect"):
        try:
            v = client.get_version()
            print(f"   server version: {getattr(v, 'version', v)}")
        except Exception as exc:
            print(f"   could not reach Hindsight: {type(exc).__name__}: {exc}")
            print("   Check HINDSIGHT_URL / HINDSIGHT_API_KEY in .env and that the server is running.")
            return 2

    created = False
    try:
        # 2. bank ------------------------------------------------------------
        with timed("2. create bank"):
            client.create_bank(
                bank_id=bank_id,
                reflect_mission="Smoke test bank for Pipeline Failure Doctor. Summarise pipeline failures and fixes.",
                retain_mission="Keep task names, fingerprints (fp_...), error classes, root causes, fixes and time to fix.",
            )
            created = True
            print("   ok")

        # 3. retain ----------------------------------------------------------
        now = datetime.now(timezone.utc)
        ok_count = 0
        for n, (doc_id, content, tags, days_ago) in enumerate(MEMORIES):
            if n and args.pace > 0:
                print(f"\n   (pausing {args.pace:.0f} s for the Groq tokens-per-minute limit)")
                time.sleep(args.pace)
            with timed(f"3. retain {doc_id}"):
                try:
                    with_quota_retry(doc_id, lambda: client.retain(
                        bank_id=bank_id,
                        content=content,
                        context="smoke test: pipeline incident",
                        timestamp=now - timedelta(days=days_ago),
                        document_id=doc_id,
                        metadata={"source": "smoke"},
                        tags=tags,
                    ))
                    ok_count += 1
                    print(f"   ok  tags={tags}")
                except Exception as exc:
                    print(f"   FAILED: {short_error(exc)}")
        record("retain accepts all our arguments", ok_count == len(MEMORIES), f"{ok_count}/{len(MEMORIES)} stored")

        # 4. recall without tags --------------------------------------------
        with timed("4. recall, no tag filter"):
            plain = recall(client, bank_id)
            show(plain)
        ours = [r for r in plain if doc_of(r).startswith("smoke-res")]
        record("recall finds our memories", bool(ours), f"{len(ours)}/{len(plain)} results from smoke-res-*")
        tagged_back = [r for r in ours if TASK in (r.tags or []) and FP in (r.tags or [])]
        record("tags survive fact extraction", bool(ours) and len(tagged_back) == len(ours),
               f"{len(tagged_back)}/{len(ours)} carry {TASK} + {FP}")

        # 5. recall, task tag, any_strict -----------------------------------
        with timed("5. recall, tags=[task], tags_match=any_strict"):
            strict = recall(client, bank_id, tags=[TASK], tags_match="any_strict")
            show(strict)
        leaks = [doc_of(r) for r in strict if doc_of(r).startswith("smoke-ctrl")]
        record("any_strict keeps other tasks + untagged out", bool(strict) and not leaks,
               f"leaked: {leaks}" if leaks else f"{len(strict)} results, no leaks")

        # 6. recall, task tag, any (current hindsight_store behaviour) -------
        with timed("6. recall, tags=[task], tags_match=any  (what hindsight_store.py uses today)"):
            loose = recall(client, bank_id, tags=[TASK], tags_match="any")
            show(loose)
        loose_leaks = [doc_of(r) for r in loose if doc_of(r).startswith("smoke-ctrl")]
        record("'any' lets untagged/other memories in", None,
               f"leaked: {loose_leaks}" if loose_leaks else "no leaks this time")

        # 7. recall, task + kind, all_strict --------------------------------
        with timed("7. recall, tags=[task, kind:resolution], tags_match=all_strict"):
            both = recall(client, bank_id, tags=[TASK, "kind:resolution"], tags_match="all_strict")
            show(both)
        wrong = [doc_of(r) for r in both if not doc_of(r).startswith("smoke-res")]
        record("all_strict narrows to this task's resolutions", bool(both) and not wrong,
               f"wrong docs: {wrong}" if wrong else f"{len(both)} results")

        # 8. reflect ---------------------------------------------------------
        # Own try/except: a reflect failure (usually Groq quota) must not skip the async check.
        if args.skip_reflect:
            record("reflect returns text", None, "skipped (--skip-reflect)")
        else:
            with timed("8. reflect"):
                try:
                    resp = with_quota_retry("reflect", lambda: client.reflect(
                        bank_id=bank_id,
                        query="Which task fails most, why, and how has time to fix changed?",
                        budget="low",
                    ))
                    text = (resp.text or "").strip()
                    print("   " + text[:600].replace("\n", "\n   "))
                    record("reflect returns text", bool(text), f"{len(text)} chars")
                except Exception as exc:
                    print(f"   FAILED: {short_error(exc)}")
                    record("reflect returns text", False, short_error(exc))

        # 9. async retain under quota pressure ------------------------------
        if args.async_check:
            async_check(client, bank_id, now, args.async_wait)

    except Exception as exc:
        record("unexpected error", False, short_error(exc))
    finally:
        if created and not args.keep:
            with timed("9. delete bank"):
                try:
                    client.delete_bank(bank_id=bank_id)
                    print("   deleted")
                except Exception as exc:
                    print(f"   could not delete {bank_id}: {exc}")
        elif created:
            print(f"\nKept bank {bank_id}: inspect it in the Hindsight UI.")
        try:
            client.close()
        except Exception:
            pass

    # summary ---------------------------------------------------------------
    print("\n" + "=" * 70 + "\nSUMMARY")
    for check, status, detail in results_table:
        print(f"  {status:4}  {check}" + (f"  - {detail}" if detail else ""))
    failed = any(s == "FAIL" for _, s, _ in results_table)
    print("=" * 70)
    print("Some checks FAILED: paste this output to your teammate/assistant." if failed else "All checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
