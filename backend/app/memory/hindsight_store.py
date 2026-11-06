"""Hindsight memory layer: everything the agent remembers goes through here. Owner: P2.

Docs: https://hindsight.vectorize.io/sdks/python

Design rules:
- Every memory carries tags (dag:, task:, fp:, kind:) so recall can be scoped.
- Identifiers (task, fingerprint, error) are ALSO written into the content text, so
  semantic + keyword recall find them even without tag filters.
- Every call is wrapped: if Hindsight is down or slow, the agent keeps working without memory.

Demo-safety rules (Day 2/3):
- One Hindsight client PER THREAD. The client's sync methods run an asyncio loop with an
  aiohttp session bound to that loop; FastAPI runs our sync routes in a thread pool, and a
  client shared across threads fails on every thread but the first
  ("Timeout context manager should be used inside a task").
- Recall is on the diagnosis path: short timeout (5 s) and a total budget across queries.
- Circuit breaker: after a timeout / connection error, skip Hindsight for 30 s instead of
  making every request wait. HTTP errors (bad request, 5xx) do not open it.
- 60 s in-process recall cache keyed by fingerprint, cleared when feedback for that
  fingerprint is retained, so a correction is visible on the very next diagnosis.
- Health Brief fallback: reflect() saves the last good brief to
  data/generated/health_brief_<bank>.json (git-ignored). If reflect fails (on the Groq free
  tier it often hits the token limit), the saved brief is returned with a "cached" note.
  Reflect has its own 60 s client and never opens the breaker, so a slow brief can't
  switch off recall for diagnoses.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.config import Settings, settings as default_settings
from app.models.schemas import Evidence, Feedback, Incident

log = logging.getLogger(__name__)

REFLECT_MISSION = (
    "You are the on-call memory of QuickBasket's data platform team. Track how data "
    "pipelines fail, why they fail, which failures are flaky, which upstream changes "
    "break which pipelines, and exactly what fixed each failure and how long it took. "
    "When summarising, name tasks as dag.task, give counts, and prefer concrete fixes."
)
RETAIN_MISSION = (
    "Keep: task names (dag.task), fingerprints (fp_...), error classes, root causes, "
    "fixes, minutes to fix, whether the task passed on retry, and which upstream change, "
    "deploy or vendor release caused a failure. Keep corrections of earlier wrong diagnoses."
)
BANK_MISSION = REFLECT_MISSION  # backward-compatible name

HEALTH_BRIEF_QUERY = (
    "Write a Pipeline Health Brief for the data platform team, using only what is in memory. "
    "Use exactly these markdown headings, in this order:\n"
    "## Most failing pipelines\n"
    "Up to 5 tasks as dag.task, each with the number of failures, the main error, and the root cause.\n"
    "## Flaky (do not page)\n"
    "Tasks whose failures pass on retry, with how often they happened.\n"
    "## Upstream causes\n"
    "Which upstream teams, vendors, schema changes or deploys caused breakages, and how many.\n"
    "## Top 3 permanent fixes\n"
    "A numbered list. For each fix: what to change, which tasks it protects, and how many "
    "incidents and engineer-minutes it would have prevented.\n"
    "Be concrete. If memory does not support a claim, leave it out."
)

# Timeouts and limits (seconds). Constructor arguments override them, e.g. in tests.
RECALL_TIMEOUT_S = 5.0      # one recall request
WRITE_TIMEOUT_S = 30.0      # retain / create bank: Hindsight runs an LLM for retain
REFLECT_TIMEOUT_S = 60.0    # reflect makes several LLM calls; never opens the breaker
RECALL_BUDGET_S = 5.0       # all recall queries for one incident together
BREAKER_COOLDOWN_S = 30.0   # how long to skip Hindsight after it timed out or refused a connection
CACHE_TTL_S = 60.0          # recall cache lifetime


def tags_for(dag_id: str, task_id: str, fingerprint: str | None = None, kind: str | None = None) -> list[str]:
    tags = [f"dag:{dag_id}", f"task:{dag_id}.{task_id}"]
    if fingerprint:
        tags.append(f"fp:{fingerprint}")
    if kind:
        tags.append(f"kind:{kind}")
    return tags


def is_outage(exc: BaseException) -> bool:
    """True if the error means Hindsight is unreachable or too slow (open the breaker).

    False for HTTP error responses: the server is up, it just rejected this one request.
    """
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError)):
        return True
    try:
        import aiohttp

        if isinstance(exc, aiohttp.ClientResponseError):
            return False
        if isinstance(exc, aiohttp.ClientError):  # connector / server-disconnected errors
            return True
    except ImportError:  # pragma: no cover
        pass
    if isinstance(exc, OSError):
        return True
    name = type(exc).__name__
    return "Timeout" in name or "Connect" in name


class MemoryStore:
    def __init__(
        self,
        cfg: Settings = default_settings,
        *,
        recall_timeout_s: float = RECALL_TIMEOUT_S,
        write_timeout_s: float = WRITE_TIMEOUT_S,
        recall_budget_s: float = RECALL_BUDGET_S,
        breaker_cooldown_s: float = BREAKER_COOLDOWN_S,
        cache_ttl_s: float = CACHE_TTL_S,
        reflect_timeout_s: float = REFLECT_TIMEOUT_S,
        brief_cache_path: Path | None = None,
        client_factory: Callable[[float], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.cfg = cfg
        self.bank_id = cfg.hindsight_bank_id
        self.enabled = cfg.memory_enabled
        self.recall_timeout_s = recall_timeout_s
        self.write_timeout_s = write_timeout_s
        self.recall_budget_s = recall_budget_s
        self.breaker_cooldown_s = breaker_cooldown_s
        self.cache_ttl_s = cache_ttl_s
        self.reflect_timeout_s = reflect_timeout_s
        self.brief_cache_path = brief_cache_path or (
            Path(cfg.data_dir) / "generated" / f"health_brief_{self.bank_id}.json"
        )
        self.recall_level = "mid"  # Hindsight recall budget: low / mid / high (recall_eval.py compares them)
        self._client_factory = client_factory or self._default_client
        self._clock = clock
        self._local = threading.local()  # per-thread clients, see module docstring
        self._lock = threading.Lock()
        self._bank_ready = False
        self._down_until = 0.0
        self._cache: dict[tuple, tuple[float, list[Evidence]]] = {}
        self.last_error: str | None = None
        self.failed_calls = 0  # every failed call since startup (seed_memory can check this)

    # ------------------------------------------------------------------ plumbing
    def _default_client(self, timeout: float):
        from hindsight_client import Hindsight

        return Hindsight(
            base_url=self.cfg.hindsight_url,
            api_key=self.cfg.hindsight_api_key or None,
            timeout=timeout,
            max_attempts=1,  # never retry on the request path; the breaker handles outages
        )

    def _thread_client(self, kind: str, timeout: float):
        client = getattr(self._local, kind, None)
        if client is None:
            client = self._client_factory(timeout)
            setattr(self._local, kind, client)
        return client

    @property
    def client(self):
        """Client for retain / reflect / bank admin (long timeout). Also used by scripts/reset_demo.py."""
        return self._thread_client("write_client", self.write_timeout_s)

    @property
    def recall_client(self):
        return self._thread_client("recall_client", self.recall_timeout_s)

    @property
    def reflect_client(self):
        return self._thread_client("reflect_client", self.reflect_timeout_s)

    @property
    def available(self) -> bool:
        """False while memory is off or the circuit breaker is open."""
        return self.enabled and self._clock() >= self._down_until

    def _call(self, fn_name: str, default: Any, *, fast: bool = False, slow_ok: bool = False, **kwargs) -> Any:
        """fast: recall client (5 s). slow_ok: reflect client (60 s), and failures never open the breaker."""
        if not self.enabled:
            return default
        if self._clock() < self._down_until:
            return default  # breaker open: Hindsight timed out recently, don't wait on it again
        try:
            self._ensure_bank()
            client = self.recall_client if fast else (self.reflect_client if slow_ok else self.client)
            result = getattr(client, fn_name)(bank_id=self.bank_id, **kwargs)
            self.last_error = None
            return result
        except Exception as exc:  # network down, timeout, bad request, ...
            self.failed_calls += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            if is_outage(exc) and not slow_ok:
                self._down_until = self._clock() + self.breaker_cooldown_s
                log.warning(
                    "Hindsight %s failed (%s); skipping memory for %.0f s", fn_name, self.last_error, self.breaker_cooldown_s
                )
            else:
                log.warning("Hindsight %s failed: %s", fn_name, self.last_error)
            return default

    def _ensure_bank(self) -> None:
        if self._bank_ready:
            return
        # create_bank is "create or update", so it is safe to call when the bank exists.
        # If it raises, _call() reports the error and we try again on the next call.
        self.client.create_bank(
            bank_id=self.bank_id,
            reflect_mission=REFLECT_MISSION,
            retain_mission=RETAIN_MISSION,
        )
        self._bank_ready = True

    def status(self) -> dict:
        breaker_s = max(0.0, self._down_until - self._clock())
        return {
            "enabled": self.enabled,
            "available": self.available,
            "bank_id": self.bank_id,
            "url": self.cfg.hindsight_url,
            "last_error": self.last_error,
            "failed_calls": self.failed_calls,
            "breaker_open_for_s": round(breaker_s, 1),
            "cached_recalls": len(self._cache),
            "health_brief_cached_at": (self._load_brief() or {}).get("generated_at"),
        }

    # ------------------------------------------------------------------ cache
    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    def _cache_get(self, key: tuple) -> list[Evidence] | None:
        with self._lock:
            hit = self._cache.get(key)
            if hit and self._clock() - hit[0] < self.cache_ttl_s:
                return hit[1]
            self._cache.pop(key, None)
            return None

    def _cache_put(self, key: tuple, evidence: list[Evidence]) -> None:
        with self._lock:
            self._cache[key] = (self._clock(), evidence)

    def _invalidate_fingerprint(self, fingerprint: str) -> None:
        with self._lock:
            for key in [k for k in self._cache if k[0] == fingerprint]:
                del self._cache[key]

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
        # The next diagnosis for this fingerprint must see the new resolution/correction.
        self._invalidate_fingerprint(incident.fingerprint)

    
    def retain_change(self, change: dict) -> None:
        """Retain an upstream change from either supported change-feed format."""

        change_text = change.get("text") or change.get("summary", "")
        change_time = change.get("at") or change.get("ts")

        affects = (
            change.get("affects_dags")
            or change.get("affects")
            or []
        )

        # Convert task-level references into DAG and task tags.
        tags = ["kind:change"]

        for affected in affects:
            dag, separator, task = affected.partition(".")

            tags.append(f"dag:{dag}")

            if separator and task:
                tags.append(f"task:{dag}.{task}")

        self._call(
            "retain",
            None,
            content=change_text,
            context=f"upstream change: {change.get('type', 'change')}",
            timestamp=change_time,
            document_id=change.get("id"),
            tags=sorted(set(tags)),
        )



    # ------------------------------------------------------------------ recall
    def _to_evidence(self, response) -> list[Evidence]:
        out = []
        for r in getattr(response, "results", None) or []:
            occurred = getattr(r, "occurred_start", None) or getattr(r, "mentioned_at", None)
            try:
                occurred_dt = occurred if isinstance(occurred, datetime) else (
                    datetime.fromisoformat(str(occurred)) if occurred else None
                )
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

    def _queries(self, incident: Incident) -> list[tuple[str, dict]]:
        """(query, recall kwargs). Order = priority: earlier results are kept first."""
        dag, task = incident.dag_id, f"{incident.dag_id}.{incident.task_id}"
        return [
            (  # 1. this exact failure: same task or same fingerprint
                f"Past failures, root causes, fixes and corrections for task {task} with "
                f"{incident.error_class}: {incident.error_message[:160]}. Fingerprint {incident.fingerprint}.",
                {"tags": [f"task:{task}", f"fp:{incident.fingerprint}"], "tags_match": "any_strict"},
            ),
            (  # 2. flakiness in the same DAG
                f"Is {task} or {incident.error_class} known to be flaky, transient, or to pass on retry?",
                {"tags": [f"dag:{dag}"], "tags_match": "any_strict"},
            ),
            (  # 3. upstream changes that hit this DAG shortly before the failure
                f"Upstream schema changes, deploys or vendor releases affecting {dag} "
                f"in the days before {incident.occurred_at:%Y-%m-%d}",
                {"tags": ["kind:change", f"dag:{dag}"], "tags_match": "all_strict"},
            ),
        ]

    def recall_for_incident(self, incident: Incident, limit: int = 8) -> list[Evidence]:
        """Evidence for one incident: 3 scoped queries, merged and de-duplicated.

        Never returns memories that came from this incident itself (its failure event or
        its own feedback), so the memory-ON result is never self-confirming.
        """
        key = (incident.fingerprint, incident.dag_id, incident.task_id)
        merged = self._cache_get(key)
        if merged is None:
            merged, complete = self._recall_uncached(incident)
            if complete:
                self._cache_put(key, merged)
        own = (incident.id, f"{incident.id}-")
        return [
            ev for ev in merged
            if not (ev.document_id and (ev.document_id == own[0] or ev.document_id.startswith(own[1])))
        ][:limit]

    def _recall_uncached(self, incident: Incident) -> tuple[list[Evidence], bool]:
        started = self._clock()
        when = incident.occurred_at.strftime("%Y-%m-%dT%H:%M:%S")  # recency is relative to the failure
        seen: set[str] = set()
        evidence: list[Evidence] = []
        complete = True
        for i, (query, scope) in enumerate(self._queries(incident)):
            if self._clock() - started > self.recall_budget_s or not self.available:
                complete = False
                break
            base = {"query": query, "budget": self.recall_level, "max_tokens": 2048, "query_timestamp": when}
            resp = self._call("recall", None, fast=True, **base, **scope)
            if resp is None:
                complete = False
                continue
            found = self._to_evidence(resp)
            if i == 0 and not found and self.available:
                # Safety net: if tags did not survive Hindsight's fact extraction, the strict
                # filter returns nothing. The ids are in the text, so search without tags.
                found = self._to_evidence(self._call("recall", None, fast=True, **base))
            for ev in found:
                k = ev.memory_id or ev.text
                if k not in seen:
                    seen.add(k)
                    evidence.append(ev)
        return evidence, complete

    def search(self, query: str, limit: int = 20) -> list[Evidence]:
        return self._to_evidence(self._call("recall", None, fast=True, query=query, budget="mid"))[:limit]

    # ------------------------------------------------------------------ reflect
    def reflect(self, query: str | None = None) -> str:
        """Pipeline Health Brief (query=None) or a custom question about memory.

        The Health Brief falls back to the last good one when Hindsight can't produce a new one.
        """
        if not self.enabled:
            return "Memory unavailable: memory is switched off (MEMORY_ENABLED=false)."
        is_brief = query is None
        resp = self._call("reflect", None, slow_ok=True, query=query or HEALTH_BRIEF_QUERY, budget="mid")
        text = (getattr(resp, "text", None) or "").strip()
        if text:
            if is_brief:
                self._save_brief(text)
            return text
        if is_brief:
            cached = self._load_brief()
            if cached:
                return (
                    f"{cached['text']}\n\n"
                    f"_Cached Health Brief from {cached['generated_at']}: memory is busy or unreachable "
                    f"right now, so this is the last brief Hindsight produced._"
                )
        return "Memory unavailable: could not reach Hindsight."

    def _save_brief(self, text: str) -> None:
        try:
            self.brief_cache_path.parent.mkdir(parents=True, exist_ok=True)
            data = {"bank_id": self.bank_id, "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "text": text}
            tmp = self.brief_cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(self.brief_cache_path)
        except OSError as exc:  # a read-only disk must never break the brief itself
            log.warning("could not save Health Brief cache: %s", exc)

    def _load_brief(self) -> dict | None:
        try:
            data = json.loads(self.brief_cache_path.read_text(encoding="utf-8"))
            return data if data.get("text") else None
        except (OSError, ValueError):
            return None
