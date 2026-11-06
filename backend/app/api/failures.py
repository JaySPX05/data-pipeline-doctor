"""Failure ingestion: the entry point for Airflow callbacks and scripts/replay.py. Owner: P1 (Jay)."""
import logging
import time

from fastapi import APIRouter, BackgroundTasks, Depends, Response
from sqlalchemy.orm import Session

from app.agent.doctor import FailureDoctor
from app.db.database import get_session
from app.deps import get_doctor, get_store
from app.memory.hindsight_store import MemoryStore
from app.models.schemas import FailureEvent, Incident
from app.services import incident_service as svc

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/failures", tags=["failures"])


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


@router.post("", response_model=Incident)
def ingest_failure(
    event: FailureEvent,
    response: Response,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    doctor: FailureDoctor = Depends(get_doctor),
    store: MemoryStore = Depends(get_store),
) -> Incident:
    """Store the failure, diagnose it, return the incident with its diagnosis card.

    The failure itself is retained to Hindsight AFTER the response is sent (background task),
    so a slow memory write never delays the on-call engineer. It also runs after recall,
    so the incident can never match itself.
    """
    t_start = time.perf_counter()

    t0 = time.perf_counter()
    incident = svc.create_incident(session, event)
    db_create_ms = _ms(t0)

    diagnosis, trace = doctor.diagnose_with_trace(incident, session, use_memory=True)

    t0 = time.perf_counter()
    incident = svc.save_diagnosis(session, incident.id, diagnosis)
    db_save_ms = _ms(t0)

    background.add_task(store.retain_failure, incident)

    llm_ms = sum(c["ms"] for c in trace.llm_calls)
    tool_ms = sum(t["ms"] for t in trace.tool_calls)
    total_ms = _ms(t_start)
    response.headers["Server-Timing"] = (
        f"db;dur={db_create_ms + db_save_ms}, recall;dur={trace.recall_ms}, "
        f"llm;dur={llm_ms}, tools;dur={tool_ms}, total;dur={total_ms}"
    )
    log.info(
        "ingest %s %s.%s -> %s conf=%.2f | db=%dms recall=%dms llm=%dms (%d calls) tools=%dms total=%dms%s",
        incident.id,
        incident.dag_id,
        incident.task_id,
        diagnosis.verdict.value,
        diagnosis.confidence,
        db_create_ms + db_save_ms,
        trace.recall_ms,
        llm_ms,
        len(trace.llm_calls),
        tool_ms,
        total_ms,
        f" fallback={trace.fallback}" if trace.fallback else "",
    )
    if trace.guardrails:
        log.info("ingest %s guardrails: %s", incident.id, "; ".join(trace.guardrails))
    if total_ms > 15_000:
        log.warning("ingest %s took %dms (> 15s budget)", incident.id, total_ms)
    return incident
