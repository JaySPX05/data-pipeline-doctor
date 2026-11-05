"""Hindsight memory layer: everything the agent remembers goes through here. Owner: P2.

Docs: https://hindsight.vectorize.io/sdks/python

Design rules:
- Every memory carries tags (dag:, task:, fp:, kind:) so recall can be scoped.
- Identifiers (task, fingerprint, error) are ALSO written into the content text, so
  semantic + keyword recall find them even without tag filters.
- Every call is wrapped: if Hindsight is down, the agent keeps working without memory.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.config import Settings, settings as default_settings
from app.models.schemas import Evidence, Feedback, Incident

log = logging.getLogger(__name__)

BANK_MISSION = (
    "You are the on-call memory of QuickBasket's data platform team. Track how data "
    "pipelines fail, why they fail, which failures are flaky, which upstream changes "
    "break which pipelines, and exactly what fixed each failure and how long it took."
)


def tags_for(dag_id: str, task_id: str, fingerprint: str | None = None, kind: str | None = None) -> list[str]:
    tags = [f"dag:{dag_id}", f"task:{dag_id}.{task_id}"]
    if fingerprint:
        tags.append(f"fp:{fingerprint}")
    if kind:
        tags.append(f"kind:{kind}")
    return tags


class MemoryStore:
    def __init__(self, cfg: Settings = default_settings):
        self.cfg = cfg
        self.bank_id = cfg.hindsight_bank_id
        self.enabled = cfg.memory_enabled
        self._client = None
        self._bank_ready = False
        self.last_error: str | None = None

    # ------------------------------------------------------------------ plumbing
    @property
    def client(self):
        if self._client is None:
            from hindsight_client import Hindsight

            self._client = Hindsight(
                base_url=self.cfg.hindsight_url,
                api_key=self.cfg.hindsight_api_key or None,
                timeout=30.0,
                max_attempts=2,
            )
        return self._client

    def _call(self, fn_name: str, default: Any, **kwargs) -> Any:
        if not self.enabled:
            return default
        try:
            self._ensure_bank()
            result = getattr(self.client, fn_name)(bank_id=self.bank_id, **kwargs)
            self.last_error = None
            return result
        except Exception as exc:  # network down, bad config, etc.
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("Hindsight %s failed: %s", fn_name, self.last_error)
            return default

    def _ensure_bank(self) -> None:
        if self._bank_ready:
            return
        try:
            self.client.create_bank(
                bank_id=self.bank_id,
                name="QuickBasket data platform on-call",
                mission=BANK_MISSION,
            )
        except Exception as exc:  # most likely: bank already exists
            log.debug("create_bank: %s", exc)
        self._bank_ready = True

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "bank_id": self.bank_id,
            "url": self.cfg.hindsight_url,
            "last_error": self.last_error,
        }

    # ------------------------------------------------------------------ retain
    def retain_failure(self, incident: Incident) -> None:
        # The agent's own verdict is deliberately NOT stored here: an unconfirmed guess in memory
        # would reinforce itself. Confirmed verdicts arrive via retain_feedback().
        changes = f" Recent changes: {'; '.join(incident.recent_changes)}." if incident.recent_changes else ""
        content = (
            f"Task {incident.dag_id}.{incident.task_id} failed at {incident.occurred_at:%Y-%m-%d %H:%M} "
            f"(try {incident.try_number}) with {incident.error_class}: {incident.error_message[:300]}. "
            f"Fingerprint {incident.fingerprint}.{changes}"
        )
        self._call(
            "retain",
            None,
            content=content,
            context="pipeline failure event",
            timestamp=incident.occurred_at,
            document_id=incident.id,
            metadata={"dag_id": incident.dag_id, "task_id": incident.task_id, "fingerprint": incident.fingerprint},
            tags=tags_for(incident.dag_id, incident.task_id, incident.fingerprint, "failure"),
        )

    def retain_feedback(self, incident: Incident, feedback: Feedback, resolved_at: datetime | None = None) -> None:
        """Confirmed resolution, or a correction when the agent was wrong."""
        took = f" Took {feedback.minutes_to_fix} min to fix." if feedback.minutes_to_fix else ""
        verdict = (feedback.verdict or (incident.diagnosis.verdict if incident.diagnosis else None))
        verdict_txt = f" Verdict: {verdict.value}." if verdict else ""
        if feedback.correct:
            kind, context = "resolution", "confirmed incident resolution"
            content = (
                f"Root cause of {incident.fingerprint} ({incident.error_class} in "
                f"{incident.dag_id}.{incident.task_id}): {feedback.root_cause}. Fix: {feedback.fix}.{took}{verdict_txt}"
            )
        else:
            kind, context = "correction", "engineer corrected the agent's diagnosis"
            guess = incident.diagnosis.root_cause if incident.diagnosis else "no diagnosis"
            content = (
                f"Correction for {incident.fingerprint} ({incident.error_class} in {incident.dag_id}.{incident.task_id}): "
                f"the agent guessed '{guess}', but the real root cause was {feedback.root_cause}. "
                f"Correct fix: {feedback.fix}.{took}{verdict_txt}"
            )
        self._call(
            "retain",
            None,
            content=content,
            context=context,
            timestamp=resolved_at or incident.occurred_at,
            document_id=f"{incident.id}-{kind}",
            metadata={"dag_id": incident.dag_id, "task_id": incident.task_id, "fingerprint": incident.fingerprint},
            tags=tags_for(incident.dag_id, incident.task_id, incident.fingerprint, kind),
        )

    def retain_change(self, change: dict) -> None:
        """An upstream schema change / deploy / vendor release from the change feed."""
        self._call(
            "retain",
            None,
            content=change["text"],
            context=f"upstream change: {change.get('type', 'change')}",
            timestamp=change.get("at"),
            document_id=change.get("id"),
            tags=[f"kind:change", *[f"dag:{d}" for d in change.get("affects_dags", [])]],
        )

    # ------------------------------------------------------------------ recall
    def _to_evidence(self, response) -> list[Evidence]:
        out = []
        for r in getattr(response, "results", None) or []:
            occurred = getattr(r, "occurred_start", None) or getattr(r, "mentioned_at", None)
            try:
                occurred_dt = datetime.fromisoformat(str(occurred)) if occurred else None
            except ValueError:
                occurred_dt = None
            out.append(
                Evidence(
                    memory_id=str(r.id) if getattr(r, "id", None) else None,
                    text=r.text,
                    occurred_at=occurred_dt,
                    document_id=getattr(r, "document_id", None),
                    type=getattr(r, "type", None),
                    tags=list(getattr(r, "tags", None) or []),
                )
            )
        return out

    def recall_for_incident(self, incident: Incident, limit: int = 8) -> list[Evidence]:
        """TODO(P2): tune these queries on Day 2/3 with the real scenario data."""
        queries = [
            (
                f"Past failures, root causes and fixes for task {incident.dag_id}.{incident.task_id} "
                f"with {incident.error_class}: {incident.error_message[:160]}",
                tags_for(incident.dag_id, incident.task_id, incident.fingerprint),
            ),
            (f"Is {incident.dag_id}.{incident.task_id} known to be flaky or pass on retry?", None),
            (
                f"Upstream schema changes, deploys or vendor releases affecting {incident.dag_id} "
                f"in the days before {incident.occurred_at:%Y-%m-%d}",
                None,
            ),
        ]
        seen, evidence = set(), []
        for query, tags in queries:
            kwargs = {"query": query, "budget": "mid", "max_tokens": 2048}
            if tags:
                kwargs.update(tags=tags, tags_match="any")
            for ev in self._to_evidence(self._call("recall", None, **kwargs)):
                if ev.document_id == incident.id:
                    continue  # never match the incident against itself
                key = ev.memory_id or ev.text
                if key not in seen:
                    seen.add(key)
                    evidence.append(ev)
        return evidence[:limit]

    def search(self, query: str, limit: int = 20) -> list[Evidence]:
        return self._to_evidence(self._call("recall", None, query=query, budget="mid"))[:limit]

    # ------------------------------------------------------------------ reflect
    def reflect(self, query: str | None = None) -> str:
        query = query or (
            "Write a Pipeline Health Brief for the data platform team: which pipelines fail most and why, "
            "which failures are flaky and should not page anyone, which upstream teams or vendors cause the "
            "most breakages, and the top 3 permanent fixes that would remove repeat incidents."
        )
        resp = self._call("reflect", None, query=query, budget="mid")
        if resp is None:
            return "Memory unavailable: could not reach Hindsight."
        return resp.text
