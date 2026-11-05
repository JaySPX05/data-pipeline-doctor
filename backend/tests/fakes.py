"""Test doubles for the agent: a scripted LLM and an in-memory MemoryStore. No network."""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

from app.agent.llm import ChatResult
from app.models.schemas import Evidence, Incident


def tool_call(name: str, args: dict | None = None, call_id: str = "call_1"):
    return SimpleNamespace(id=call_id, type="function", function=SimpleNamespace(name=name, arguments=json.dumps(args or {})))


def reply(content: str | None = None, tool_calls: list | None = None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def diagnosis_json(**overrides) -> str:
    body = {
        "verdict": "KNOWN_ISSUE",
        "root_cause": "Vendor FreshFarm renamed customer_id to cust_id",
        "suggested_fix": "Map cust_id -> customer_id in config/vendor_schema.yaml",
        "confidence": 0.9,
        "page_oncall": True,
        "evidence_ids": [],
    }
    body.update(overrides)
    return json.dumps(body)


class ScriptedLLM:
    """Returns the scripted replies in order; records every request it saw."""

    def __init__(self, *replies, model: str = "fake-model"):
        self.replies = list(replies)
        self.requests: list[dict] = []
        self.model = model
        self.available = True

    def chat(self, messages, tools=None, temperature=0.2, json_mode=False) -> ChatResult:
        self.requests.append({"messages": [dict(m) for m in messages], "tools": tools, "json_mode": json_mode})
        if not self.replies:
            raise AssertionError("ScriptedLLM ran out of replies")
        nxt = self.replies.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return ChatResult(message=nxt, model=self.model, attempts=[f"{self.model}:ok"])


class FakeStore:
    """MemoryStore stand-in: returns canned evidence, records retains."""

    def __init__(self, evidence: list[Evidence] | None = None, enabled: bool = True):
        self.evidence = evidence or []
        self.enabled = enabled
        self.recall_calls = 0

    def recall_for_incident(self, incident: Incident, limit: int = 8) -> list[Evidence]:
        self.recall_calls += 1
        return list(self.evidence)

    def search(self, query: str, limit: int = 20) -> list[Evidence]:
        return list(self.evidence)


def memory(text: str, kind: str, fp: str | None = None, task: str | None = None, mid: str = "m1", when: str = "2026-10-11") -> Evidence:
    tags = [f"kind:{kind}"]
    if fp:
        tags.append(f"fp:{fp}")
    if task:
        tags.append(f"task:{task}")
    return Evidence(memory_id=mid, text=text, occurred_at=datetime.fromisoformat(when), tags=tags)
