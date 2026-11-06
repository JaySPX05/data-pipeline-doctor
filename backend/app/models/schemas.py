"""Shared contracts between the API, the agent, memory and the frontend.

FROZEN at the Day 1 midday checkpoint. Change only with a heads-up to the whole team,
and update frontend/src/mocks/incidents.js in the same PR.
"""
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Verdict(str, Enum):
    KNOWN_ISSUE = "KNOWN_ISSUE"          # seen before, we know the fix
    FLAKY = "FLAKY"                      # transient, passes on retry
    UPSTREAM_CHANGE = "UPSTREAM_CHANGE"  # caused by a schema/deploy/vendor change
    NEW = "NEW"                          # never seen; needs a human


class IncidentStatus(str, Enum):
    OPEN = "open"
    DIAGNOSED = "diagnosed"
    RESOLVED = "resolved"


class FailureEvent(BaseModel):
    """What a scheduler (Airflow callback or our simulator) posts to /api/failures."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "dag_id": "orders_etl",
                    "task_id": "load_orders",
                    "run_id": "scheduled__2026-10-19T03:00:00+00:00",
                    "try_number": 1,
                    "occurred_at": "2026-10-19T03:12:44+00:00",
                    "log_tail": "Traceback (most recent call last):\n  File \"/opt/airflow/dags/orders/load.py\", line 88, in load_orders\n    df[\"customer_id\"] = df[\"customer_id\"].astype(\"int64\")\nKeyError: 'customer_id'",
                    "operator": "PythonOperator",
                    "recent_changes": [],
                }
            ]
        }
    )

    dag_id: str
    task_id: str
    run_id: str = "manual"
    try_number: int = 1
    occurred_at: datetime
    log_tail: str = Field(..., description="Last ~200 lines of the task log")
    operator: str | None = None
    recent_changes: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    """A memory the agent used, shown on the diagnosis card."""

    memory_id: str | None = None
    text: str
    occurred_at: datetime | None = None
    document_id: str | None = None  # incident id the memory came from
    type: str | None = None          # Hindsight fact type: world / experience / observation
    tags: list[str] = Field(default_factory=list)  # e.g. ["fp:fp_13e8231d", "kind:correction"] (additive, Day 1)

    def kind(self) -> str | None:
        """failure / resolution / correction / change, from the kind: tag."""
        return next((t.split(":", 1)[1] for t in self.tags if t.startswith("kind:")), None)


class Diagnosis(BaseModel):
    verdict: Verdict
    root_cause: str
    suggested_fix: str
    confidence: float = Field(ge=0, le=1)
    page_oncall: bool = True
    evidence: list[Evidence] = Field(default_factory=list)
    memory_used: bool = False
    model: str = "offline-heuristic"
    latency_ms: int = 0


class Feedback(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "correct": False,
                    "root_cause": "Vendor FreshFarm renamed customer_id to cust_id in order feed v2.5",
                    "fix": "Map cust_id -> customer_id in config/vendor_schema.yaml and re-run load_orders",
                    "verdict": "UPSTREAM_CHANGE",
                    "minutes_to_fix": 4,
                    "engineer": "jay",
                }
            ]
        }
    )

    correct: bool
    root_cause: str
    fix: str
    verdict: Verdict | None = None
    minutes_to_fix: int | None = None
    engineer: str | None = None


class Incident(BaseModel):
    id: str
    dag_id: str
    task_id: str
    run_id: str
    try_number: int
    occurred_at: datetime
    error_class: str
    error_message: str
    fingerprint: str
    log_tail: str
    recent_changes: list[str] = Field(default_factory=list)
    status: IncidentStatus = IncidentStatus.OPEN
    diagnosis: Diagnosis | None = None
    feedback: Feedback | None = None


class LearningCurvePoint(BaseModel):
    day: str
    incidents: int
    auto_diagnosed_pct: float
    avg_latency_ms: float
    accuracy_pct: float | None = None  # share of diagnoses the engineer marked correct
