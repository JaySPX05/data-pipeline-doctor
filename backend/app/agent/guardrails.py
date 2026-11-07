"""Code-enforced rules applied to every diagnosis, whatever the LLM said. Owner: P1 (Jay).

The LLM proposes; these rules make sure the card never claims more than the evidence supports.
Each rule that fires adds a human-readable note to the trace.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from app.models.schemas import Diagnosis, Evidence, Incident, Verdict

NO_EVIDENCE_MAX_CONFIDENCE = 0.5
NO_FINGERPRINT_MAX_CONFIDENCE = 0.95
FLAKY_NO_PAGE_MIN_CONFIDENCE = 0.8
CORRECTION_MIN_OVERLAP = 0.2
CHANGE_WINDOW = timedelta(hours=72)  # same window as the change-feed tool (changes_for_incident)

_RETRY_OUTCOME = re.compile(r"passed on retry|auto-retry passed|verdict: flaky", re.I)

_STOP = {
    "the", "and", "for", "with", "that", "this", "from", "was", "were", "has", "have", "but", "not",
    "agent", "guessed", "real", "root", "cause", "correction", "correct", "fix", "took", "min", "verdict",
    "into", "when", "then", "they", "their", "after", "before", "again",
}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9_]+", text.lower()) if len(w) > 3 and w not in _STOP}


def matches_fingerprint(ev: Evidence, incident: Incident) -> bool:
    return f"fp:{incident.fingerprint}" in ev.tags or incident.fingerprint in ev.text


def matches_task(ev: Evidence, incident: Incident) -> bool:
    full = f"{incident.dag_id}.{incident.task_id}"
    return f"task:{full}" in ev.tags or full in ev.text or incident.task_id in ev.text


def _naive_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def is_recent_change(ev: Evidence, incident: Incident) -> bool:
    """A change memory from the 72 h before this failure. Older changes are history, not a cause."""
    if ev.kind() != "change" or ev.occurred_at is None:
        return False
    gap = _naive_utc(incident.occurred_at) - _naive_utc(ev.occurred_at)
    return timedelta(0) <= gap <= CHANGE_WINDOW


def is_retry_outcome(ev: Evidence) -> bool:
    """A past outcome where the task simply passed on retry (no fix applied)."""
    return "outcome:auto_retry" in ev.tags or bool(_RETRY_OUTCOME.search(ev.text))


def corrections_for(incident: Incident, evidence: list[Evidence]) -> list[Evidence]:
    return [e for e in evidence if e.kind() == "correction" and matches_fingerprint(e, incident)]


def apply_guardrails(
    diagnosis: Diagnosis,
    incident: Incident,
    evidence: list[Evidence],
    change_seen: bool = False,
) -> list[str]:
    """Mutate `diagnosis` in place; return notes describing every rule that fired."""
    notes: list[str] = []
    fp_match = any(matches_fingerprint(e, incident) for e in evidence)
    task_match = any(matches_task(e, incident) for e in evidence)
    change_seen = change_seen or bool(incident.recent_changes) or any(is_recent_change(e, incident) for e in evidence)
    history = [e for e in evidence if matches_fingerprint(e, incident) or matches_task(e, incident)]

    # 1. "Known" or "flaky" needs history for THIS task.
    if diagnosis.verdict in (Verdict.KNOWN_ISSUE, Verdict.FLAKY) and not (fp_match or task_match):
        notes.append(f"{diagnosis.verdict.value} without matching memory: downgraded to NEW")
        diagnosis.verdict = Verdict.NEW
        diagnosis.confidence = min(diagnosis.confidence, NO_EVIDENCE_MAX_CONFIDENCE)

    # 1b. "Known issue" whose every past outcome was "passed on retry" is flaky: nobody ever fixed it.
    if diagnosis.verdict == Verdict.KNOWN_ISSUE and not corrections_for(incident, evidence):
        outcomes = [e for e in history if e.kind() == "resolution" or is_retry_outcome(e)]
        if outcomes and all(is_retry_outcome(e) for e in outcomes):
            notes.append(f"KNOWN_ISSUE but all {len(outcomes)} past outcome(s) passed on retry: verdict set to FLAKY")
            diagnosis.verdict = Verdict.FLAKY

    # 2. "Upstream change" needs a change we can point to: in the feed, from the scheduler, or a
    #    change memory from the last 72 h. A change from weeks ago in the same DAG is not a cause.
    if diagnosis.verdict == Verdict.UPSTREAM_CHANGE and not change_seen and not fp_match:
        notes.append("UPSTREAM_CHANGE with no recent change in memory, feed or scheduler: downgraded to NEW")
        diagnosis.verdict = Verdict.NEW
        diagnosis.confidence = min(diagnosis.confidence, NO_EVIDENCE_MAX_CONFIDENCE)

    # 3. Near-certainty only with an exact fingerprint match.
    if not fp_match and diagnosis.confidence > NO_FINGERPRINT_MAX_CONFIDENCE:
        notes.append("no fingerprint match: confidence capped at 0.95")
        diagnosis.confidence = NO_FINGERPRINT_MAX_CONFIDENCE

    # 4. A past correction for this fingerprint must not be contradicted.
    corrections = corrections_for(incident, evidence)
    if corrections:
        latest = max(corrections, key=lambda e: e.occurred_at.timestamp() if e.occurred_at else 0)
        wanted = _words(latest.text)
        said = _words(f"{diagnosis.root_cause} {diagnosis.suggested_fix}")
        overlap = len(wanted & said) / len(wanted) if wanted else 1.0
        if overlap < CORRECTION_MIN_OVERLAP:
            notes.append(f"contradicts a past correction (overlap {overlap:.0%}): confidence capped, paging on-call")
            diagnosis.confidence = min(diagnosis.confidence, NO_EVIDENCE_MAX_CONFIDENCE)
        diagnosis.evidence = [latest] + [e for e in diagnosis.evidence if e is not latest]

    # 5. Only confident FLAKY verdicts may skip the page.
    should_page = not (diagnosis.verdict == Verdict.FLAKY and diagnosis.confidence >= FLAKY_NO_PAGE_MIN_CONFIDENCE)
    if should_page != diagnosis.page_oncall:
        notes.append(f"page_oncall set to {should_page} by policy")
        diagnosis.page_oncall = should_page

    diagnosis.confidence = round(diagnosis.confidence, 2)
    return notes
