"""Tools the diagnosis agent can call. Owner: P1 (wiring) + P3 (change feed data).

Each tool = an OpenAI-style JSON schema + a Python function taking a ToolContext.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.memory.hindsight_store import MemoryStore
from app.models.schemas import Incident
from app.services import incident_service as svc


@dataclass
class ToolContext:
    incident: Incident
    session: Session
    store: MemoryStore
    use_memory: bool


def _load_change_feed() -> list[dict]:
    path = Path(settings.data_dir) / "generated" / "change_feed.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _naive_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def changes_for_incident(incident: Incident, hours_back: int = 72, keyword: str | None = None, limit: int = 10) -> list[dict]:
    """Changes in the window before the failure that affect this DAG (or match any keyword word).

    Matching on the DAG is what makes this reliable: the model's keyword guesses often miss
    (e.g. it searches 'aws_inventory' but the event says 'svc-inventory-export').
    """
    end = _naive_utc(incident.occurred_at)
    start = end - timedelta(hours=hours_back)
    words = [w for w in re.split(r"[^a-z0-9]+", (keyword or "").lower()) if len(w) > 2]
    items = []
    for change in _load_change_feed():
        at = _naive_utc(datetime.fromisoformat(change["at"]))
        if not (start <= at <= end):
            continue
        affects = change.get("affects_dags") or []
        text = change["text"].lower()
        if incident.dag_id in affects or not affects or any(w in text for w in words):
            items.append({**change, "hours_before_failure": round((end - at).total_seconds() / 3600, 1)})
    items.sort(key=lambda c: c["hours_before_failure"])
    return items[:limit]


def get_change_feed(ctx: ToolContext, hours_back: int = 72, keyword: str | None = None) -> dict:
    """Schema changes, deploys and vendor releases in the window before the failure."""
    return {"window_hours": hours_back, "changes": changes_for_incident(ctx.incident, hours_back, keyword)}


def get_retry_history(ctx: ToolContext) -> dict:
    """How often this task failed before, and how those failures were resolved."""
    history = svc.task_history(ctx.session, ctx.incident.dag_id, ctx.incident.task_id, exclude_id=ctx.incident.id)
    return {
        "previous_failures": len(history),
        "same_fingerprint": sum(h.fingerprint == ctx.incident.fingerprint for h in history),
        "recent": [
            {
                "occurred_at": h.occurred_at.isoformat(),
                "error_class": h.error_class,
                "fingerprint": h.fingerprint,
                "verdict": h.diagnosis.verdict.value if h.diagnosis else None,
                "resolution": h.feedback.root_cause if h.feedback else None,
            }
            for h in history[:10]
        ],
    }


def grep_log(ctx: ToolContext, pattern: str, context_lines: int = 2) -> dict:
    """Search the full task log for a regex and return matching lines with context."""
    lines = ctx.incident.log_tail.splitlines()
    try:
        rx = re.compile(pattern, re.I)
    except re.error:
        rx = re.compile(re.escape(pattern), re.I)
    hits = []
    for i, line in enumerate(lines):
        if rx.search(line):
            lo, hi = max(0, i - context_lines), min(len(lines), i + context_lines + 1)
            hits.append("\n".join(lines[lo:hi]))
    return {"matches": hits[:10]}


def search_memory(ctx: ToolContext, query: str) -> dict:
    """Free-form recall from Hindsight (only offered when memory is ON)."""
    return {"memories": [e.model_dump(mode="json") for e in ctx.store.search(query, limit=8)]}


_REGISTRY = {
    "get_change_feed": (
        get_change_feed,
        {
            "description": "List upstream schema changes, deploys and vendor releases shortly before this failure.",
            "parameters": {
                "type": "object",
                "properties": {
                    "hours_back": {"type": "integer", "description": "Window size in hours (default 72)"},
                    "keyword": {"type": "string", "description": "Optional filter, e.g. a table or column name"},
                },
            },
        },
    ),
    "get_retry_history": (
        get_retry_history,
        {
            "description": "Previous failures of this same task, with fingerprints, verdicts and resolutions.",
            "parameters": {"type": "object", "properties": {}},
        },
    ),
    "grep_log": (
        grep_log,
        {
            "description": "Regex search over the full task log.",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    ),
    "search_memory": (
        search_memory,
        {
            "description": "Ask the team's long-term incident memory a question in plain English.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    ),
}


# Tools that read past incidents. Hidden when memory is OFF, so the "before" picture is honest:
# without memory the agent has no access to the team's history at all.
MEMORY_TOOLS = {"search_memory", "get_retry_history"}


def tool_schemas(use_memory: bool) -> list[dict]:
    return [
        {"type": "function", "function": {"name": name, **schema}}
        for name, (_, schema) in _REGISTRY.items()
        if use_memory or name not in MEMORY_TOOLS
    ]


def run_tool(ctx: ToolContext, name: str, arguments: str | None) -> str:
    if name not in _REGISTRY or (name in MEMORY_TOOLS and not ctx.use_memory):
        return json.dumps({"error": f"unknown tool {name}"})
    try:
        args = json.loads(arguments or "{}") or {}
        result = _REGISTRY[name][0](ctx, **args)
    except Exception as exc:  # bad args from the model must not crash the agent
        result = {"error": f"{type(exc).__name__}: {exc}"}
    return json.dumps(result, default=str)[:2500]  # tool output is resent on every later turn: keep it small
