"""The diagnosis agent: recall -> reason (with tools) -> diagnosis card. Owner: P1."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from sqlalchemy.orm import Session

from app.agent.llm import LLMClient, extract_json
from app.agent.tools import ToolContext, run_tool, tool_schemas
from app.config import Settings, settings as default_settings
from app.memory.hindsight_store import MemoryStore
from app.models.schemas import Diagnosis, Evidence, Incident, Verdict

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (Path(__file__).resolve().parents[1] / "prompts" / "diagnose_system.md").read_text()


def _format_memories(evidence: list[Evidence]) -> str:
    if not evidence:
        return "MEMORIES: none (memory is off or nothing matched)."
    lines = ["MEMORIES (most relevant first):"]
    for ev in evidence:
        when = f"{ev.occurred_at:%Y-%m-%d}" if ev.occurred_at else "unknown date"
        lines.append(f"- [id={ev.memory_id}] ({when}) {ev.text}")
    return "\n".join(lines)


def _incident_prompt(incident: Incident, evidence: list[Evidence]) -> str:
    changes = "\n".join(f"- {c}" for c in incident.recent_changes) or "- none reported"
    return (
        f"FAILED TASK: {incident.dag_id}.{incident.task_id} (run {incident.run_id}, try {incident.try_number})\n"
        f"WHEN: {incident.occurred_at.isoformat()}\n"
        f"ERROR: {incident.error_class}: {incident.error_message}\n"
        f"FINGERPRINT: {incident.fingerprint}\n"
        f"RECENT CHANGES REPORTED BY SCHEDULER:\n{changes}\n\n"
        f"{_format_memories(evidence)}\n\n"
        f"LOG TAIL (last 60 lines):\n{chr(10).join(incident.log_tail.splitlines()[-60:])}"
    )


class FailureDoctor:
    def __init__(self, store: MemoryStore, llm: LLMClient, cfg: Settings = default_settings):
        self.store, self.llm, self.cfg = store, llm, cfg

    def diagnose(self, incident: Incident, session: Session, use_memory: bool = True) -> Diagnosis:
        started = time.perf_counter()
        use_memory = use_memory and self.store.enabled
        evidence = self.store.recall_for_incident(incident) if use_memory else []

        if self.llm.available:
            try:
                diagnosis = self._llm_diagnose(incident, session, evidence, use_memory)
            except Exception as exc:
                log.error("LLM diagnosis failed, using heuristic: %s", exc)
                diagnosis = self._heuristic(incident, evidence)
        else:
            diagnosis = self._heuristic(incident, evidence)

        diagnosis.memory_used = bool(evidence)
        diagnosis.latency_ms = int((time.perf_counter() - started) * 1000)
        return diagnosis

    # ------------------------------------------------------------------ LLM path
    def _llm_diagnose(self, incident: Incident, session: Session, evidence: list[Evidence], use_memory: bool) -> Diagnosis:
        ctx = ToolContext(incident=incident, session=session, store=self.store, use_memory=use_memory)
        tools = tool_schemas(use_memory)
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _incident_prompt(incident, evidence)},
        ]
        model_used = self.cfg.llm_model
        msg = None
        for _ in range(self.cfg.max_tool_steps):
            msg, model_used = self.llm.chat(messages, tools=tools)
            if not getattr(msg, "tool_calls", None):
                break
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                        for tc in msg.tool_calls
                    ],
                }
            )
            for tc in msg.tool_calls:
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": run_tool(ctx, tc.function.name, tc.function.arguments)})

        data = extract_json(msg.content if msg else None)
        if data is None:  # one repair attempt, no tools
            messages.append({"role": "user", "content": "Reply with ONLY the JSON object described in the instructions."})
            msg, model_used = self.llm.chat(messages)
            data = extract_json(msg.content)
        if data is None:
            raise ValueError("model did not return JSON")

        used_ids = set(data.get("evidence_ids") or [])
        cited = [e for e in evidence if e.memory_id in used_ids] or evidence[:5]
        verdict = data.get("verdict", "NEW")
        return Diagnosis(
            verdict=Verdict(verdict) if verdict in Verdict.__members__ else Verdict.NEW,
            root_cause=str(data.get("root_cause", "Unknown")),
            suggested_fix=str(data.get("suggested_fix", "Investigate the log.")),
            confidence=max(0.0, min(1.0, float(data.get("confidence", 0.4)))),
            page_oncall=bool(data.get("page_oncall", True)),
            evidence=cited,
            model=model_used,
        )

    # ------------------------------------------------------------------ offline path
    def _heuristic(self, incident: Incident, evidence: list[Evidence]) -> Diagnosis:
        """No API key or the LLM is down: rule-based guess so the app still works (and tests run)."""
        matches = [e for e in evidence if incident.fingerprint in e.text]
        resolutions = [e for e in matches if "Root cause" in e.text or "Correction" in e.text]
        if resolutions:
            return Diagnosis(
                verdict=Verdict.KNOWN_ISSUE,
                root_cause=resolutions[0].text,
                suggested_fix="Apply the fix recorded in the matching past incident.",
                confidence=0.75,
                evidence=resolutions[:3],
            )
        retries = [e for e in evidence if "retry" in e.text.lower()]
        if len(retries) >= 2:
            return Diagnosis(
                verdict=Verdict.FLAKY,
                root_cause=f"{incident.error_class} on this task has passed on retry before.",
                suggested_fix="Retry the task; open a ticket if it fails 3 times in a row.",
                confidence=0.7,
                page_oncall=False,
                evidence=retries[:3],
            )
        return Diagnosis(
            verdict=Verdict.NEW,
            root_cause=f"{incident.error_class}: {incident.error_message[:200]}",
            suggested_fix="No matching history. Inspect the log and recent upstream changes.",
            confidence=0.3,
            evidence=evidence[:3],
        )


def debug_dump(diagnosis: Diagnosis) -> str:
    return json.dumps(diagnosis.model_dump(mode="json"), indent=2)
