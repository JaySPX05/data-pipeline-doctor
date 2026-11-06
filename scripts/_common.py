"""Shared helpers for the P3 scripts (generate / seed / replay / reset).

*** ADAPTER FILE ***
Everything that depends on OTHER people's code lives here, so when you read the real
backend/app/models/schemas.py, services/fingerprint.py and memory/hindsight_store.py
you only need to fix things in this one file:

  1. fingerprint_log()      -> uses backend fingerprinter if importable, else a local one
  2. failure_payload()      -> shape of POST /api/failures      (check schemas.py)
  3. feedback_payload()     -> shape of POST .../feedback       (check schemas.py)
  4. memory_items()         -> what we retain (matches the plan's table)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SCENARIO_DIR = DATA_DIR / "scenarios"
GEN_DIR = DATA_DIR / "generated"
INCIDENTS_PATH = GEN_DIR / "incidents.jsonl"
CHANGES_PATH = GEN_DIR / "change_feed.jsonl"
MANIFEST_PATH = GEN_DIR / "manifest.json"

START_DATE = datetime(2026, 10, 1)      # day 1 = Oct 1, day 30 = Oct 30
SEED_DAYS = (1, 14)                     # the agent's "past" -> Hindsight
REPLAY_DAYS = (15, 30)                  # replayed live in the demo

BANK_ID = os.getenv("HINDSIGHT_BANK_ID", "quickbasket-data-platform")
BANK_NAME = "QuickBasket Data Platform"
BANK_MISSION = ("You are the on-call memory of a data platform team. "
                "Track how pipelines fail, why, and what fixed them.")


# ----------------------------------------------------------------- basic io
def day_of(dt: datetime) -> int:
    return (dt.date() - START_DATE.date()).days + 1


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"Missing {path}. Run: python scripts/generate_history.py")
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def load_scenarios() -> list[dict]:
    """Load data/scenarios/*.yaml. Files with `variants:` (S09) expand into one scenario each."""
    import yaml
    out: list[dict] = []
    for p in sorted(SCENARIO_DIR.glob("*.yaml")):
        doc = yaml.safe_load(p.read_text())
        if "variants" in doc:
            base = doc.get("base", {})
            for v in doc["variants"]:
                sc = {**base, **v}
                sc["schedule"] = {"days": [v["day"]], "hour_range": v.get("hour_range", [1, 4])}
                out.append(sc)
        else:
            out.append(doc)
    return out


# --------------------------------------------------------------- fingerprint
_META = re.compile(r"\b\w+=\S")


def _error_block(text: str) -> str:
    """Fallback only: the exception lines after the last indented (stack-frame) line."""
    lines = text.splitlines()
    last = -1
    for i, l in enumerate(lines):
        if l[:1] in (" ", "\t"):
            last = i
    block = []
    for l in lines[last + 1:]:
        if not l.strip() or l.startswith("["):
            continue
        if _META.search(l):
            break
        block.append(l)
    return "\n".join(block)


def local_fingerprint(text: str, task_id: str = "unknown") -> str:
    """Fallback used only if backend/app/services/fingerprint.py is missing."""
    s = _error_block(text)
    s = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?", "<ts>", s)
    s = re.sub(r"\d+", "N", s)
    s = re.sub(r"\s+", " ", s).strip()
    return "fp_" + hashlib.sha1(f"{task_id}|{s}".encode()).hexdigest()[:8]


_BACKEND_FP = None
_BACKEND_FP_LOADED = False


def _load_backend_fp():
    """Load P1's real fingerprinter BY FILE PATH (no package imports, so no heavy deps)."""
    global _BACKEND_FP, _BACKEND_FP_LOADED
    if _BACKEND_FP_LOADED:
        return _BACKEND_FP
    _BACKEND_FP_LOADED = True
    path = ROOT / "backend" / "app" / "services" / "fingerprint.py"
    if os.getenv("P3_FINGERPRINT") == "local" or not path.exists():
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location("backend_fingerprint", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _BACKEND_FP = mod
    return mod


def fingerprint_backend_name() -> str:
    return "backend:services/fingerprint.py" if _load_backend_fp() else "local (fallback)"


def fingerprint_log(text: str, task_id: str = "unknown") -> str:
    """Real backend fingerprint: fingerprint(task_id, log) -> (fp, error_class, message)."""
    mod = _load_backend_fp()
    return mod.fingerprint(task_id, text)[0] if mod else local_fingerprint(text, task_id)


def backend_extract_error(text: str):
    """(error_class, message) exactly as the backend/agent will see it, or None."""
    mod = _load_backend_fp()
    return mod.extract_error(text) if mod else None


# ----------------------------------------------------------- API payload shapes
# Matches backend/app/models/schemas.py (FROZEN): FailureEvent and Feedback.
_OPERATORS = {"aggregate": "SparkSubmitOperator", "join_spend": "SparkSubmitOperator",
              "dq_checks": "GreatExpectationsOperator", "export_s3": "PythonOperator"}


def _fmt_change(c: dict) -> str:
    return f"{c['ts'][:16].replace('T', ' ')} [{c['type']}] {c['summary']}"


def failure_payload(inc: dict) -> dict:
    """POST /api/failures body = schemas.FailureEvent."""
    return {
        "dag_id": inc["dag_id"],
        "task_id": inc["task_id"],
        "run_id": inc["run_id"],
        "try_number": inc["try_number"],
        "occurred_at": inc["failed_at"],
        "log_tail": inc["log_tail"],
        "operator": _OPERATORS.get(inc["task_id"], "PythonOperator"),
        "recent_changes": [_fmt_change(c) for c in inc.get("recent_changes", [])],
    }


def feedback_payload(inc: dict, correct: bool = True) -> dict:
    """POST /api/incidents/{id}/feedback body = schemas.Feedback.

    correct=True  -> the agent was right: Hindsight stores a confirmed resolution.
    correct=False -> the agent was wrong: stored as a correction (how the agent learns).
    """
    t = inc["truth"]
    return {
        "correct": correct,
        "root_cause": t["root_cause"],
        "fix": t["fix"],
        "verdict": t["verdict"],
        "minutes_to_fix": t["minutes_to_resolve"],
        "engineer": "replay-simulator",
    }


# ------------------------------------------------------------------ memory items
def _ordinal(n: int) -> str:
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def memory_items(inc: dict) -> list[dict]:
    """Retain payloads for one incident (failure event, resolution OR flaky outcome).

    NOTE: document_id must be unique per retain. Hindsight treats a repeated document_id
    as an update of the same document, so one incident ID for 2 retains would overwrite.
    We use INC-0007 for the failure event and INC-0007:resolution for the outcome.
    """
    t, dag, task, fp = inc["truth"], inc["dag_id"], inc["task_id"], inc["fingerprint"]
    ts = datetime.fromisoformat(inc["failed_at"])
    tags = [f"dag:{dag}", f"task:{task}", f"fp:{fp}"]
    meta = {"dag_id": dag, "task_id": task, "fingerprint": fp, "incident_id": inc["incident_id"]}

    content = (f"Task {dag}.{task} failed at {ts:%H:%M} on {ts:%b} {ts.day} with "
               f"{inc['error_summary']}, fingerprint {fp}.")
    if inc.get("recent_changes"):
        c = inc["recent_changes"][-1]
        content += f" It followed: {c['summary']} ({c['ts'][:10]})."
    items = [dict(content=content, context="failure event", timestamp=inc["failed_at"],
                  document_id=inc["incident_id"], metadata=meta, tags=tags + ["kind:failure"])]

    if t["auto_retry_passes"]:
        content = (f"{dag}.{task} failed with {inc['error_summary']} and passed on retry "
                   f"{t['retry_count']}. {_ordinal(inc['occurrence'])} time this month.")
        items.append(dict(content=content, context="flaky retry outcome",
                          timestamp=t["resolved_at"], document_id=f"{inc['incident_id']}:resolution",
                          metadata={**meta, "outcome": "auto_retry_passed"},
                          tags=tags + ["kind:resolution", "outcome:auto_retry"]))
    else:
        content = (f"Root cause of {fp}: {t['root_cause']} Fix: {t['fix']} "
                   f"Took {t['minutes_to_resolve']} min.")
        items.append(dict(content=content, context="confirmed incident resolution",
                          timestamp=t["resolved_at"], document_id=f"{inc['incident_id']}:resolution",
                          metadata={**meta, "outcome": "resolved"},
                          tags=tags + ["kind:resolution"]))
    return items


def change_memory_item(chg: dict) -> dict:
    tags = ["kind:change", f"change:{chg['type']}"]
    for a in chg.get("affects", []):
        d, _, tk = a.partition(".")
        tags += [f"dag:{d}", f"task:{tk}"]
    content = f"{chg['summary']} ({chg['type']}, source {chg['source']}). Affects {', '.join(chg['affects'])}."
    return dict(content=content, context="upstream change feed", timestamp=chg["ts"],
                document_id=chg["id"], metadata={"change_id": chg["id"], "type": chg["type"],
                                                 "source": chg["source"]},
                tags=sorted(set(tags)))


# ------------------------------------------------------------------ hindsight client
def get_hindsight(base_url: str | None = None, api_key: str | None = None):
    from hindsight_client import Hindsight        # pip install hindsight-client
    kw = {"base_url": base_url or os.getenv("HINDSIGHT_URL", "http://localhost:8888")}
    key = api_key or os.getenv("HINDSIGHT_API_KEY")
    if key:
        kw["api_key"] = key
    return Hindsight(**kw)


def ensure_bank(client, bank_id: str = BANK_ID) -> None:
    try:
        client.create_bank(bank_id=bank_id, name=BANK_NAME, mission=BANK_MISSION)
    except Exception as e:                        # already exists / older client: fine
        print(f"  (create_bank skipped: {type(e).__name__}: {e})")


def http_json(method: str, url: str, body: dict | None = None, timeout: float = 60.0):
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw else {}
