"""Tools the diagnosis agent can call. Owner: P1 (wiring) + P3 (change feed data).

Each tool = an OpenAI-style JSON schema + a Python function taking a ToolContext.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
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


def get_change_feed(ctx: ToolContext, hours_back: int = 72, keyword: str | None = None) -> dict:
    """Schema changes, deploys and vendor releases in the window before the failure."""
    end = ctx.incident.occurred_at
    start = end - timedelta(hours=hours_back)
    items = []
    for change in _load_change_feed():
        at = datetime.fromisoformat(change["at"])
        if start <= at.replace(tzinfo=end.tzinfo) <= end:
            if keyword and keyword.lower() not in change["text"].lower():
                continue
            items.append(change)
    return {"window_hours": hours_back, "changes": items[:20]}


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


def tool_schemas(use_memory: bool) -> list[dict]:
    return [
        {"type": "function", "function": {"name": name, **schema}}
        for name, (_, schema) in _REGISTRY.items()
        if use_memory or name != "search_memory"
    ]


def run_tool(ctx: ToolContext, name: str, arguments: str | None) -> str:
    if name not in _REGISTRY or (name == "search_memory" and not ctx.use_memory):
        return json.dumps({"error": f"unknown tool {name}"})
    try:
        args = json.loads(arguments or "{}") or {}
        result = _REGISTRY[name][0](ctx, **args)
    except Exception as exc:  # bad args from the model must not crash the agent
        result = {"error": f"{type(exc).__name__}: {exc}"}
    return json.dumps(result, default=str)[:6000]
