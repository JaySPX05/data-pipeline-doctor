from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class IncidentRow(Base):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    dag_id: Mapped[str] = mapped_column(String, index=True)
    task_id: Mapped[str] = mapped_column(String, index=True)
    run_id: Mapped[str] = mapped_column(String)
    try_number: Mapped[int] = mapped_column(Integer, default=1)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    error_class: Mapped[str] = mapped_column(String)
    error_message: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String, index=True)
    log_tail: Mapped[str] = mapped_column(Text)
    recent_changes: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String, default="open", index=True)
    diagnosis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    feedback: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
