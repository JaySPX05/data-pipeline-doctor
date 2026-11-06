"""Metrics for the Insights page. Owner: P1 (Jay)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import get_session
from app.models.schemas import LearningCurvePoint
from app.services import incident_service as svc

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


class DayMTTR(BaseModel):
    day: str
    resolved: int
    mttr_minutes: float = Field(description="Mean minutes_to_fix of incidents resolved that day")


class DaySnapshot(BaseModel):
    day: str
    auto_diagnosed_pct: float
    accuracy_pct: float | None
    mttr_minutes: float | None


class MetricsSummary(BaseModel):
    """Everything the KPI tiles need, in one call."""

    total_incidents: int
    diagnosed: int
    resolved: int
    by_verdict: dict[str, int] = Field(description="Agent verdicts, e.g. {'FLAKY': 12, 'NEW': 3}")
    auto_diagnosed_pct: float = Field(description="Not NEW and confidence >= 0.7, over all diagnosed")
    accuracy_pct: float | None = Field(description="Share of reviewed diagnoses the engineer marked correct")
    pages_avoided: int = Field(description="FLAKY diagnoses with page_oncall=false")
    avg_latency_ms: float
    mttr_minutes: float | None = Field(description="Mean minutes_to_fix over all resolved incidents")
    mttr_by_day: list[DayMTTR]
    first_day: DaySnapshot | None = Field(description="For 'change since day 1' on the tiles")
    latest_day: DaySnapshot | None


@router.get("/learning-curve", response_model=list[LearningCurvePoint])
def learning_curve(session: Session = Depends(get_session)):
    return svc.learning_curve(session)


@router.get("/summary", response_model=MetricsSummary)
def summary(session: Session = Depends(get_session)):
    return MetricsSummary(**svc.metrics_summary(session))
