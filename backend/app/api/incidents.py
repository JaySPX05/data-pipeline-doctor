from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.agent.doctor import FailureDoctor
from app.db.database import get_session
from app.deps import get_doctor, get_store
from app.memory.hindsight_store import MemoryStore
from app.models.schemas import Diagnosis, Feedback, Incident
from app.services import incident_service as svc

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


def _get_or_404(session: Session, incident_id: str) -> Incident:
    incident = svc.get_incident(session, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="incident not found")
    return incident


@router.get("", response_model=list[Incident])
def list_incidents(
    status: str | None = None,
    verdict: str | None = None,
    dag_id: str | None = None,
    limit: int = Query(200, le=1000),
    session: Session = Depends(get_session),
):
    return svc.list_incidents(session, status=status, verdict=verdict, dag_id=dag_id, limit=limit)


@router.get("/{incident_id}", response_model=Incident)
def get_incident(incident_id: str, session: Session = Depends(get_session)):
    return _get_or_404(session, incident_id)


@router.post("/{incident_id}/feedback", response_model=Incident)
def submit_feedback(
    incident_id: str,
    feedback: Feedback,
    session: Session = Depends(get_session),
    store: MemoryStore = Depends(get_store),
):
    """Engineer confirms or corrects the diagnosis. This is how the agent learns."""
    _get_or_404(session, incident_id)
    incident = svc.save_feedback(session, incident_id, feedback)
    store.retain_feedback(incident, feedback)
    return incident


@router.post("/{incident_id}/rediagnose", response_model=Diagnosis)
def rediagnose(
    incident_id: str,
    memory: bool = True,
    session: Session = Depends(get_session),
    doctor: FailureDoctor = Depends(get_doctor),
):
    """Run the agent again (memory=false gives the 'before' half of the before/after demo). Not saved."""
    incident = _get_or_404(session, incident_id)
    return doctor.diagnose(incident, session, use_memory=memory)
