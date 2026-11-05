"""The diagnosis agent: recall -> reason (with tools) -> guardrails -> diagnosis card. Owner: P1 (Jay)."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.agent.guardrails import apply_guardrails, corrections_for, matches_fingerprint
from app.agent.llm import LLMClient, LLMError, extract_json
from app.agent.tools import ToolContext, changes_for_incident, get_retry_history, run_tool, tool_schemas
from app.config import Settings, settings as default_settings
from app.memory.hindsight_store import MemoryStore
from app.models.schemas import Diagnosis, Evidence, Incident, Verdict

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (Path(__file__).resolve().parents[1] / "prompts" / "diagnose_system.md").read_text()
_KIND_LABEL = {"correction": "CORRECTION", "resolution": "RESOLUTION", "failure": "PAST FAILURE", "change": "CHANGE"}


@dataclass
class AgentTrace:
    """Everything the agent did for one diagnosis. Logged, printed by the CLI, used by the eval."""

    incident_id: str
    use_memory: bool
    recalled: int = 0
    recall_ms: int = 0
    prefetched_changes: int = 0
    prefetched_history: int = 0
    llm_calls: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    repaired: bool = False
    fallback: str | None = None
    guardrails: list[str] = field(default_factory=list)
    total_ms: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        models = ",".join(sorted({c["model"] for c in self.llm_calls})) or "none"
        tools = ",".join(t["name"] for t in self.tool_calls) or "none"
        return (
            f"memory={'on' if self.use_memory else 'off'} recalled={self.recalled} "
            f"changes={self.prefetched_changes} history={self.prefetched_history} models={models} "
            f"llm_calls={len(self.llm_calls)} tools=[{tools}] repaired={self.repaired} "
            f"fallback={self.fallback} guardrails={len(self.guardrails)} {self.total_ms}ms"
        )


def _format_memories(incident: Incident, evidence: list[Evidence]) -> str:
    if not evidence:
        return "MEMORIES: none (memory is off or nothing matched)."
    lines = ["MEMORIES (most relevant first):"]
    for ev in evidence:
        when = f"{ev.occurred_at:%Y-%m-%d}" if ev.occurred_at else "unknown date"
        label = _KIND_LABEL.get(ev.kind() or "", "MEMORY")
        same = " SAME FINGERPRINT" if matches_fingerprint(ev, incident) else ""
        lines.append(f"- [id={ev.memory_id}] [{label}{same}] ({when}) {ev.text}")
    if corrections_for(incident, evidence):
        lines.append("NOTE: a CORRECTION exists for this exact fingerprint. Follow it.")
    return "\n".join(lines)


def _format_changes(changes: list[dict]) -> str:
    if not changes:
        return "CHANGE FEED (72h before the failure, this DAG): no changes recorded."
    lines = ["CHANGE FEED (72h before the failure, affecting this DAG, newest first):"]
    for c in changes:
        lines.append(f"- [{c.get('type', 'change')}] {c['hours_before_failure']}h before: {c['text']}")
    return "\n".join(lines)


def _format_history(history: dict | None) -> str:
    if history is None:
        return ""
    if not history["previous_failures"]:
        return "TASK HISTORY: no previous failures of this task in the incident store.\n\n"
    lines = [
        f"TASK HISTORY: {history['previous_failures']} previous failures of this task, "
        f"{history['same_fingerprint']} with this exact fingerprint. Most recent:"
    ]
    for h in history["recent"][:5]:
        resolved = f" -> confirmed cause: {h['resolution']}" if h.get("resolution") else ""
        lines.append(f"- {h['occurred_at'][:16]} {h['error_class']} {h['fingerprint']} verdict={h['verdict']}{resolved}")
    return "\n".join(lines) + "\n\n"


def _incident_prompt(
    incident: Incident,
    evidence: list[Evidence],
    changes: list[dict] | None = None,
    history: dict | None = None,
    log_lines: int = 40,
) -> str:
    reported = "\n".join(f"- {c}" for c in incident.recent_changes) or "- none reported"
    return (
        f"FAILED TASK: {incident.dag_id}.{incident.task_id} (run {incident.run_id}, try {incident.try_number})\n"
        f"WHEN: {incident.occurred_at.isoformat()}\n"
        f"ERROR: {incident.error_class}: {incident.error_message}\n"
        f"FINGERPRINT: {incident.fingerprint}\n"
        f"CHANGES REPORTED BY SCHEDULER:\n{reported}\n"
        f"{_format_changes(changes or [])}\n\n"
        f"{_format_history(history)}"
        f"{_format_memories(incident, evidence)}\n\n"
        f"LOG TAIL (last {log_lines} lines):\n{chr(10).join(incident.log_tail.splitlines()[-log_lines:])}"
    )


def _assistant_tool_message(msg) -> dict:
    return {
        "role": "assistant",
        "content": msg.content or "",
        "tool_calls": [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ],
    }


class FailureDoctor:
    def __init__(self, store: MemoryStore, llm: LLMClient, cfg: Settings = default_settings):
        self.store, self.llm, self.cfg = store, llm, cfg

    # ------------------------------------------------------------------ public
    def diagnose(self, incident: Incident, session: Session, use_memory: bool = True) -> Diagnosis:
        diagnosis, trace = self.diagnose_with_trace(incident, session, use_memory)
        log.info("diagnosis %s %s conf=%.2f | %s", incident.id, diagnosis.verdict.value, diagnosis.confidence, trace.summary())
        for note in trace.guardrails:
            log.info("guardrail %s: %s", incident.id, note)
        return diagnosis

    def diagnose_with_trace(self, incident: Incident, session: Session, use_memory: bool = True) -> tuple[Diagnosis, AgentTrace]:
        started = time.perf_counter()
        use_memory = use_memory and self.store.enabled
        trace = AgentTrace(incident_id=incident.id, use_memory=use_memory)

        evidence: list[Evidence] = []
        if use_memory:
            t0 = time.perf_counter()
            evidence = self.store.recall_for_incident(incident)
            trace.recall_ms = int((time.perf_counter() - t0) * 1000)
            trace.recalled = len(evidence)

        # Prefetch cheap, deterministic context so most diagnoses need a single LLM call.
        ctx = ToolContext(incident=incident, session=session, store=self.store, use_memory=use_memory)
        changes = changes_for_incident(incident)
        history = get_retry_history(ctx) if use_memory else None
        trace.prefetched_changes = len(changes)
        trace.prefetched_history = history["previous_failures"] if history else 0

        change_seen = bool(changes)
        diagnosis: Diagnosis | None = None
        if self.llm.available:
            try:
                diagnosis, tool_change_seen = self._llm_diagnose(ctx, evidence, changes, history, trace)
                change_seen = change_seen or tool_change_seen
            except Exception as exc:
                trace.fallback = f"heuristic ({type(exc).__name__}: {str(exc)[:160]})"
                log.error("LLM diagnosis failed for %s, using heuristic: %s", incident.id, exc)
        else:
            trace.fallback = "heuristic (no GROQ_API_KEY)"
        if diagnosis is None:
            diagnosis = self._heuristic(incident, evidence)

        trace.guardrails = apply_guardrails(diagnosis, incident, evidence, change_seen=change_seen)
        diagnosis.memory_used = bool(evidence)
        trace.total_ms = diagnosis.latency_ms = int((time.perf_counter() - started) * 1000)
        return diagnosis, trace

    # ------------------------------------------------------------------ LLM path
    def _chat(self, trace: AgentTrace, messages: list[dict], **kwargs):
        t0 = time.perf_counter()
        result = self.llm.chat(messages, **kwargs)
        trace.llm_calls.append(
            {
                "model": result.model,
                "attempts": result.attempts,
                "salvaged": result.salvaged,
                "ms": int((time.perf_counter() - t0) * 1000),
            }
        )
        return result

    def _llm_diagnose(
        self,
        ctx: ToolContext,
        evidence: list[Evidence],
        changes: list[dict],
        history: dict | None,
        trace: AgentTrace,
    ) -> tuple[Diagnosis, bool]:
        incident = ctx.incident
        tools = tool_schemas(ctx.use_memory)
        prompt = _incident_prompt(incident, evidence, changes, history, self.cfg.log_tail_lines)
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        tool_cache: dict[tuple[str, str], str] = {}
        change_seen = False

        result = None
        for _ in range(self.cfg.max_tool_steps):
            result = self._chat(trace, messages, tools=tools)
            msg = result.message
            if not getattr(msg, "tool_calls", None):
                break
            messages.append(_assistant_tool_message(msg))
            for tc in msg.tool_calls:
                key = (tc.function.name, tc.function.arguments or "{}")
                t0 = time.perf_counter()
                output = tool_cache.get(key) or run_tool(ctx, tc.function.name, tc.function.arguments)
                tool_cache[key] = output
                trace.tool_calls.append(
                    {
                        "name": tc.function.name,
                        "args": tc.function.arguments,
                        "ms": int((time.perf_counter() - t0) * 1000),
                        "result_preview": output[:200],
                    }
                )
                if tc.function.name == "get_change_feed" and '"changes": [{' in output:
                    change_seen = True
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": output})
        else:
            # Ran out of tool steps while the model still wanted tools: force a final answer.
            messages.append({"role": "user", "content": "No more tools. Give your final answer now."})
            result = self._chat(trace, messages)

        data = extract_json(result.message.content if result else None)
        if data is None:
            trace.repaired = True
            messages.append({"role": "assistant", "content": (result.message.content or "") if result else ""})
            messages.append({"role": "user", "content": "Reply with ONLY the JSON object described in the instructions."})
            data = extract_json(self._chat(trace, messages, json_mode=True).message.content)
        if data is None:
            raise LLMError("model did not return a JSON diagnosis")

        return self._to_diagnosis(data, evidence, result.model if result else self.cfg.llm_model), change_seen

    @staticmethod
    def _to_diagnosis(data: dict, evidence: list[Evidence], model: str) -> Diagnosis:
        used_ids = {str(i) for i in (data.get("evidence_ids") or [])}
        cited = [e for e in evidence if e.memory_id in used_ids] or evidence[:5]
        raw_verdict = str(data.get("verdict", "NEW")).strip().upper().replace(" ", "_")
        try:
            confidence = float(data.get("confidence", 0.4))
        except (TypeError, ValueError):
            confidence = 0.4
        fix = data.get("suggested_fix", "Investigate the log.")
        if isinstance(fix, list):  # some models return steps as a list
            fix = "\n".join(f"{i}. {step}" for i, step in enumerate(fix, 1))
        return Diagnosis(
            verdict=Verdict(raw_verdict) if raw_verdict in Verdict.__members__ else Verdict.NEW,
            root_cause=str(data.get("root_cause") or "Unknown"),
            suggested_fix=str(fix),
            confidence=max(0.0, min(1.0, confidence)),
            page_oncall=bool(data.get("page_oncall", True)),
            evidence=cited,
            model=model,
        )

    # ------------------------------------------------------------------ offline path
    def _heuristic(self, incident: Incident, evidence: list[Evidence]) -> Diagnosis:
        """No API key or the LLM is down: rule-based guess so the app still works (and tests run)."""
        same_fp = [e for e in evidence if matches_fingerprint(e, incident)]
        corrections = [e for e in same_fp if e.kind() == "correction"]
        resolutions = corrections or [
            e for e in same_fp if e.kind() == "resolution" or "root cause" in e.text.lower()
        ]
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


def debug_dump(diagnosis: Diagnosis, trace: AgentTrace | None = None) -> str:
    out = {"diagnosis": diagnosis.model_dump(mode="json")}
    if trace:
        out["trace"] = trace.to_dict()
    return json.dumps(out, indent=2, default=str)
