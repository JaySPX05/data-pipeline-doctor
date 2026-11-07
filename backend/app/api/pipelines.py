"""Pipeline inventory for the Pipeline Monitor page (read-only). Owner: P1 (Jay).

The DAG -> tasks inventory comes from P3's data/dags.yaml (the single source of truth for the
demo company); per-DAG incident stats come from the incident store. There is no run history,
owner or schedule in the demo data, so those fields are deliberately absent.
"""
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_session
from app.db.tables import IncidentRow

router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])


class Pipeline(BaseModel):
    dag_id: str
    tasks: list[str]
    incidents: int = Field(description="All incidents recorded for this DAG")
    open_incidents: int = Field(description="Not yet resolved (status open or diagnosed)")
    last_failure_at: datetime | None
    failing_tasks: dict[str, int] = Field(description="task_id -> incident count, tasks that have failed")
    flaky_tasks: list[str] = Field(description="Tasks with at least one FLAKY diagnosis")
    verdicts: dict[str, int] = Field(description="Agent verdict counts for this DAG")


class PipelineInventory(BaseModel):
    company: str | None
    pipelines: list[Pipeline]


def load_dags() -> tuple[str | None, dict[str, list[str]]]:
    path = Path(settings.data_dir) / "dags.yaml"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{path.name} not found in the data folder")
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return doc.get("company"), {dag: list(tasks or []) for dag, tasks in (doc.get("dags") or {}).items()}


@router.get("", response_model=PipelineInventory)
def list_pipelines(session: Session = Depends(get_session)) -> PipelineInventory:
    """The demo company's DAGs and tasks, with incident stats per DAG."""
    company, dags = load_dags()
    rows = session.execute(
        select(IncidentRow.dag_id, IncidentRow.task_id, IncidentRow.status, IncidentRow.occurred_at, IncidentRow.diagnosis)
    ).all()

    by_dag: dict[str, list] = defaultdict(list)
    for row in rows:
        by_dag[row.dag_id].append(row)

    pipelines = []
    for dag_id, tasks in dags.items():
        items = by_dag.get(dag_id, [])
        verdicts = Counter((r.diagnosis or {}).get("verdict") for r in items if r.diagnosis)
        flaky = sorted({r.task_id for r in items if (r.diagnosis or {}).get("verdict") == "FLAKY"})
        pipelines.append(
            Pipeline(
                dag_id=dag_id,
                tasks=tasks,
                incidents=len(items),
                open_incidents=sum(1 for r in items if r.status != "resolved"),
                last_failure_at=max((r.occurred_at for r in items), default=None),
                failing_tasks=dict(Counter(r.task_id for r in items).most_common()),
                flaky_tasks=flaky,
                verdicts=dict(verdicts),
            )
        )
    return PipelineInventory(company=company, pipelines=pipelines)
