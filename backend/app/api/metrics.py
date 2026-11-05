from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_session
from app.models.schemas import LearningCurvePoint
from app.services import incident_service as svc

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("/learning-curve", response_model=list[LearningCurvePoint])
def learning_curve(session: Session = Depends(get_session)):
    return svc.learning_curve(session)
