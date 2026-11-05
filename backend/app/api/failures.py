from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.agent.doctor import FailureDoctor
from app.db.database import get_session
from app.deps import get_doctor, get_store
from app.memory.hindsight_store import MemoryStore
from app.models.schemas import FailureEvent, Incident
from app.services import incident_service as svc

router = APIRouter(prefix="/api/failures", tags=["failures"])


@router.post("", response_model=Incident)
def ingest_failure(
    event: FailureEvent,
    session: Session = Depends(get_session),
    doctor: FailureDoctor = Depends(get_doctor),
    store: MemoryStore = Depends(get_store),
) -> Incident:
    """Called by the Airflow on_failure_callback or scripts/replay.py."""
    incident = svc.create_incident(session, event)
    diagnosis = doctor.diagnose(incident, session, use_memory=True)
    incident = svc.save_diagnosis(session, incident.id, diagnosis)
    store.retain_failure(incident)  # after recall, so the incident never matches itself
    return incident
