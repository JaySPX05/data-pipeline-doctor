"""Run the diagnosis agent from the terminal. Owner: P1 (Jay).

    cd backend
    python -m app.agent --all                      # one failure per scenario (day >= 15), memory on
    python -m app.agent --all --no-memory          # the "before" picture
    python -m app.agent --scenario vendor_schema_change --day 19
    python -m app.agent --log path/to/task.log --dag orders_etl --task load_orders
    python -m app.agent --all --json > run.json    # full diagnosis + trace for each

Nothing is written to the incident DB or to Hindsight.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.agent.doctor import FailureDoctor
from app.agent.llm import LLMClient
from app.config import settings
from app.db.database import SessionLocal, init_db
from app.memory.hindsight_store import MemoryStore
from app.models.schemas import Incident
from app.services.fingerprint import fingerprint

GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def load_history() -> list[dict]:
    path = Path(settings.data_dir) / "generated" / "history.jsonl"
    if not path.exists():
        sys.exit(f"{path} not found. Run: python scripts/generate_history.py (from the repo root)")
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def to_incident(event: dict) -> Incident:
    fp, error_class, message = fingerprint(event["task_id"], event["log_tail"])
    return Incident(
        id=f"cli_{uuid.uuid4().hex[:8]}",
        dag_id=event["dag_id"],
        task_id=event["task_id"],
        run_id=event.get("run_id", "cli"),
        try_number=event.get("try_number", 1),
        occurred_at=datetime.fromisoformat(event["occurred_at"]) if isinstance(event["occurred_at"], str) else event["occurred_at"],
        error_class=error_class,
        error_message=message,
        fingerprint=fp,
        log_tail=event["log_tail"],
        recent_changes=event.get("recent_changes", []),
    )


def pick_cases(args) -> list[tuple[Incident, dict | None, str]]:
    if args.log:
        event = {
            "dag_id": args.dag,
            "task_id": args.task,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "log_tail": Path(args.log).read_text(errors="replace"),
        }
        return [(to_incident(event), None, Path(args.log).name)]

    history = load_history()
    if args.scenario:
        items = [h for h in history if h["scenario_id"] == args.scenario]
        if not items:
            sys.exit(f"No scenario '{args.scenario}'. Known: {sorted({h['scenario_id'] for h in history})}")
        if args.day:
            items = [h for h in items if h["day"] == args.day] or sys.exit(f"No failure on day {args.day}")
        else:
            items = [next((h for h in items if h["day"] >= args.min_day), items[-1])]
    else:  # --all: first failure on/after --min-day for every scenario
        seen, items = set(), []
        for h in history:
            if h["day"] >= args.min_day and h["scenario_id"] not in seen:
                seen.add(h["scenario_id"])
                items.append(h)
    return [(to_incident(h["event"]), h["truth"], f"{h['scenario_id']} (day {h['day']})") for h in items]


def print_case(label: str, incident: Incident, diagnosis, trace, truth: dict | None) -> None:
    verdict = diagnosis.verdict.value
    check = ""
    if truth:
        ok = verdict == truth["verdict"]
        check = f"  {GREEN}OK{RESET}" if ok else f"  {RED}MISS (truth: {truth['verdict']}){RESET}"
    print(f"\n{BOLD}=== {label}{RESET}  {incident.dag_id}.{incident.task_id}  {incident.fingerprint}  {incident.error_class}")
    print(f"verdict {BOLD}{verdict}{RESET}  confidence {diagnosis.confidence:.2f}  page: {'yes' if diagnosis.page_oncall else 'no'}{check}")
    print(f"root cause: {diagnosis.root_cause}")
    print(f"fix:        {diagnosis.suggested_fix}")
    if diagnosis.evidence:
        print(f"evidence ({len(diagnosis.evidence)}):")
        for ev in diagnosis.evidence[:5]:
            when = f"{ev.occurred_at:%Y-%m-%d}" if ev.occurred_at else "?"
            print(f"  {DIM}- [{ev.kind() or ev.type or 'memory'}] {when} {ev.text[:140]}{RESET}")
    for note in trace.guardrails:
        print(f"  guardrail: {note}")
    for tool in trace.tool_calls:
        print(f"  {DIM}tool {tool['name']}({tool['args']}) {tool['ms']}ms{RESET}")
    print(f"{DIM}trace: {trace.summary()}{RESET}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m app.agent", description="Run the Pipeline Failure Doctor agent")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--all", action="store_true", help="one failure per scenario from history.jsonl")
    src.add_argument("--scenario", help="scenario id from data/scenarios")
    src.add_argument("--log", help="path to a raw task log file")
    ap.add_argument("--day", type=int, help="with --scenario: the failure on this day")
    ap.add_argument("--min-day", type=int, default=15, help="skip days the memory was seeded with (default 15)")
    ap.add_argument("--dag", default="unknown_dag", help="with --log")
    ap.add_argument("--task", default="unknown_task", help="with --log")
    ap.add_argument("--no-memory", action="store_true", help="diagnose without Hindsight (the 'before' picture)")
    ap.add_argument("--json", action="store_true", help="print JSON (diagnosis + trace) instead of text")
    args = ap.parse_args()

    init_db()
    doctor = FailureDoctor(store=MemoryStore(), llm=LLMClient())
    if not doctor.llm.available and not args.json:
        print(f"{RED}No GROQ_API_KEY: using the offline heuristic. Add the key to .env for real diagnoses.{RESET}")

    results, correct, total = [], 0, 0
    with SessionLocal() as session:
        for incident, truth, label in pick_cases(args):
            diagnosis, trace = doctor.diagnose_with_trace(incident, session, use_memory=not args.no_memory)
            if truth:
                total += 1
                correct += diagnosis.verdict.value == truth["verdict"]
            if args.json:
                results.append(
                    {"case": label, "truth": truth, "diagnosis": diagnosis.model_dump(mode="json"), "trace": trace.to_dict()}
                )
            else:
                print_case(label, incident, diagnosis, trace, truth)

    if args.json:
        print(json.dumps(results, indent=2, default=str))
    elif total:
        memory_on = not args.no_memory and doctor.store.enabled
        print(f"\n{BOLD}Verdict accuracy: {correct}/{total}{RESET} (memory {'on' if memory_on else 'off'})")


if __name__ == "__main__":
    main()
