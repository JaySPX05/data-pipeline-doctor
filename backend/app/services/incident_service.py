"""Incident persistence (SQLite) and metrics. Owner: P1 (Jay)."""
import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.tables import IncidentRow
from app.models.schemas import (
    Diagnosis,
    FailureEvent,
    Feedback,
    Incident,
    IncidentStatus,
    LearningCurvePoint,
    Verdict,
)
from app.services.fingerprint import fingerprint

AUTO_DIAGNOSED_MIN_CONFIDENCE = 0.7


def to_model(row: IncidentRow) -> Incident:
    return Incident(
        id=row.id,
        dag_id=row.dag_id,
        task_id=row.task_id,
        run_id=row.run_id,
        try_number=row.try_number,
        occurred_at=row.occurred_at,
        error_class=row.error_class,
        error_message=row.error_message,
        fingerprint=row.fingerprint,
        log_tail=row.log_tail,
        recent_changes=row.recent_changes or [],
        status=IncidentStatus(row.status),
        diagnosis=Diagnosis.model_validate(row.diagnosis) if row.diagnosis else None,
        feedback=Feedback.model_validate(row.feedback) if row.feedback else None,
    )


def create_incident(session: Session, event: FailureEvent) -> Incident:
    fp, error_class, message = fingerprint(event.task_id, event.log_tail)
    row = IncidentRow(
        id=f"inc_{uuid.uuid4().hex[:10]}",
        dag_id=event.dag_id,
        task_id=event.task_id,
        run_id=event.run_id,
        try_number=event.try_number,
        occurred_at=event.occurred_at,
        error_class=error_class,
        error_message=message,
        fingerprint=fp,
        log_tail=event.log_tail[-20_000:],  # cap huge logs
        recent_changes=event.recent_changes,
        status=IncidentStatus.OPEN.value,
    )
    session.add(row)
    session.commit()
    return to_model(row)


def get_incident(session: Session, incident_id: str) -> Incident | None:
    row = session.get(IncidentRow, incident_id)
    return to_model(row) if row else None


def list_incidents(
    session: Session,
    status: str | None = None,
    verdict: str | None = None,
    dag_id: str | None = None,
    limit: int = 200,
) -> list[Incident]:
    stmt = select(IncidentRow).order_by(IncidentRow.occurred_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(IncidentRow.status == status)
    if dag_id:
        stmt = stmt.where(IncidentRow.dag_id == dag_id)
    items = [to_model(r) for r in session.scalars(stmt)]
    if verdict:
        items = [i for i in items if i.diagnosis and i.diagnosis.verdict.value == verdict]
    return items


def save_diagnosis(session: Session, incident_id: str, diagnosis: Diagnosis) -> Incident:
    row = session.get(IncidentRow, incident_id)
    row.diagnosis = diagnosis.model_dump(mode="json")
    row.status = IncidentStatus.DIAGNOSED.value
    session.commit()
    return to_model(row)


def save_feedback(session: Session, incident_id: str, feedback: Feedback) -> Incident:
    row = session.get(IncidentRow, incident_id)
    row.feedback = feedback.model_dump(mode="json")
    row.status = IncidentStatus.RESOLVED.value
    session.commit()
    return to_model(row)


def task_history(session: Session, dag_id: str, task_id: str, exclude_id: str | None = None) -> list[Incident]:
    stmt = (
        select(IncidentRow)
        .where(IncidentRow.dag_id == dag_id, IncidentRow.task_id == task_id)
        .order_by(IncidentRow.occurred_at.desc())
        .limit(50)
    )
    return [to_model(r) for r in session.scalars(stmt) if r.id != exclude_id]


def learning_curve(session: Session) -> list[LearningCurvePoint]:
    by_day: dict[str, list[Incident]] = defaultdict(list)
    for row in session.scalars(select(IncidentRow).order_by(IncidentRow.occurred_at)):
        by_day[row.occurred_at.date().isoformat()].append(to_model(row))

    points = []
    for day, items in sorted(by_day.items()):
        diagnosed = [i for i in items if i.diagnosis]
        auto = [
            i for i in diagnosed
            if i.diagnosis.verdict != Verdict.NEW
            and i.diagnosis.confidence >= AUTO_DIAGNOSED_MIN_CONFIDENCE
        ]
        reviewed = [i for i in items if i.feedback]
        points.append(
            LearningCurvePoint(
                day=day,
                incidents=len(items),
                auto_diagnosed_pct=round(100 * len(auto) / len(diagnosed), 1) if diagnosed else 0.0,
                avg_latency_ms=round(sum(i.diagnosis.latency_ms for i in diagnosed) / len(diagnosed), 1) if diagnosed else 0.0,
                accuracy_pct=round(100 * sum(i.feedback.correct for i in reviewed) / len(reviewed), 1) if reviewed else None,
            )
        )
    return points
