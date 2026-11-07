"""Incident persistence (SQLite) and metrics. Owner: P1 (Jay)."""
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
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


def to_utc_naive(dt: datetime) -> datetime:
    """Store every timestamp as naive UTC so SQLite ordering, day grouping and windows agree."""
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


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
        occurred_at=to_utc_naive(event.occurred_at),
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
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[Incident], int]:
    """Newest first. Returns (page, total matching) so the UI can paginate."""
    filters = []
    if status:
        filters.append(IncidentRow.status == status)
    if dag_id:
        filters.append(IncidentRow.dag_id == dag_id)
    if verdict:
        filters.append(IncidentRow.diagnosis["verdict"].as_string() == verdict)
    if q:
        like = f"%{q.strip()}%"
        filters.append(
            or_(
                IncidentRow.dag_id.ilike(like),
                IncidentRow.task_id.ilike(like),
                IncidentRow.error_class.ilike(like),
                IncidentRow.error_message.ilike(like),
                IncidentRow.fingerprint.ilike(like),
            )
        )
    total = session.scalar(select(func.count()).select_from(IncidentRow).where(*filters)) or 0
    stmt = (
        select(IncidentRow)
        .where(*filters)
        .order_by(IncidentRow.occurred_at.desc(), IncidentRow.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return [to_model(r) for r in session.scalars(stmt)], total


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


def metrics_summary(session: Session) -> dict:
    """Totals for the Insights KPI tiles. Shape matches api.metrics.MetricsSummary."""
    incidents = [to_model(r) for r in session.scalars(select(IncidentRow).order_by(IncidentRow.occurred_at))]
    diagnosed = [i for i in incidents if i.diagnosis]
    reviewed = [i for i in incidents if i.feedback]
    timed = [i for i in reviewed if i.feedback.minutes_to_fix is not None]

    by_verdict: dict[str, int] = defaultdict(int)
    for i in diagnosed:
        by_verdict[i.diagnosis.verdict.value] += 1

    auto = [
        i for i in diagnosed
        if i.diagnosis.verdict != Verdict.NEW and i.diagnosis.confidence >= AUTO_DIAGNOSED_MIN_CONFIDENCE
    ]

    mttr_days: dict[str, list[int]] = defaultdict(list)
    for i in timed:
        mttr_days[i.occurred_at.date().isoformat()].append(i.feedback.minutes_to_fix)
    mttr_by_day = [
        {"day": day, "resolved": len(mins), "mttr_minutes": round(sum(mins) / len(mins), 1)}
        for day, mins in sorted(mttr_days.items())
    ]
    mttr_lookup = {d["day"]: d["mttr_minutes"] for d in mttr_by_day}

    curve = learning_curve(session)

    def snapshot(point: LearningCurvePoint | None) -> dict | None:
        if point is None:
            return None
        return {
            "day": point.day,
            "auto_diagnosed_pct": point.auto_diagnosed_pct,
            "accuracy_pct": point.accuracy_pct,
            "mttr_minutes": mttr_lookup.get(point.day),
        }

    def pct(part: int, whole: int) -> float:
        return round(100 * part / whole, 1) if whole else 0.0

    return {
        "total_incidents": len(incidents),
        "diagnosed": len(diagnosed),
        "resolved": len(reviewed),
        "by_verdict": dict(by_verdict),
        "auto_diagnosed_pct": pct(len(auto), len(diagnosed)),
        "accuracy_pct": pct(sum(i.feedback.correct for i in reviewed), len(reviewed)) if reviewed else None,
        "pages_avoided": sum(
            1 for i in diagnosed if i.diagnosis.verdict == Verdict.FLAKY and not i.diagnosis.page_oncall
        ),
        "avg_latency_ms": round(sum(i.diagnosis.latency_ms for i in diagnosed) / len(diagnosed), 1) if diagnosed else 0.0,
        "mttr_minutes": round(sum(i.feedback.minutes_to_fix for i in timed) / len(timed), 1) if timed else None,
        "mttr_by_day": mttr_by_day,
        "first_day": snapshot(curve[0] if curve else None),
        "latest_day": snapshot(curve[-1] if curve else None),
    }
