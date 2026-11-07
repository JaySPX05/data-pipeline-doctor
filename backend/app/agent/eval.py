"""Does memory make the agent better? Memory ON vs OFF on the replay window. Owner: P1 (Jay) / P4.

    cd backend
    python -m app.agent.eval                       # 2 failures per scenario, days 15-30, ON and OFF
    python -m app.agent.eval --limit 1 --delay 4   # gentler on the Groq free tier
    python -m app.agent.eval --scenario S01 --limit 4
    python -m app.agent.eval --markdown            # also print a table to paste into the README

Prerequisite for memory ON: the Hindsight bank (HINDSIGHT_URL / HINDSIGHT_API_KEY in .env) is
seeded with days 1-14:  python scripts/seed_memory.py   (from the repo root)

What it measures, per failure (the truth comes from P3's data/generated/incidents.jsonl):
- verdict accuracy        exact match with truth.verdict
- recognised              truth is not NEW and the agent did not say NEW (it knew this failure)
- root-cause match        >= 40% of the truth root cause's key words appear in the diagnosis
- page decision correct   FLAKY truth -> no page; anything else -> page
- confidence, evidence count, latency, LLM provider, offline-heuristic fallbacks

Read-only: nothing is written to the incident DB or to Hindsight, so runs are repeatable.
Raw results go to data/generated/eval_results.jsonl (git-ignored).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.agent.doctor import FailureDoctor
from app.agent.llm import LLMClient
from app.config import settings
from app.db.database import SessionLocal, init_db
from app.memory.hindsight_store import MemoryStore
from app.models.schemas import Verdict
from app.services import demo_data

ROOT_CAUSE_MATCH = 0.4
_STOP = {
    "the", "and", "for", "with", "that", "this", "from", "was", "were", "has", "have", "but", "not",
    "into", "when", "then", "after", "before", "again", "column", "table", "task", "failed", "failure",
}


def key_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9_]+", (text or "").lower()) if len(w) > 3 and w not in _STOP}


@dataclass
class CaseResult:
    scenario_id: str
    scenario_name: str
    day: int
    memory: bool
    truth_verdict: str
    verdict: str
    confidence: float
    correct: bool
    recognised: bool | None      # None when the truth itself is NEW
    root_cause_match: bool
    page_correct: bool
    evidence: int
    latency_ms: int
    providers: str
    fallback: str | None
    guardrails: list[str] = field(default_factory=list)  # which rules fired and why
    evidence_seen: list[str] = field(default_factory=list)  # "kind | tags | text" per cited memory


def score(record: dict, diagnosis, trace, memory: bool) -> CaseResult:
    truth = demo_data.truth(record)
    truth_verdict = truth["verdict"]
    wanted = key_words(truth.get("root_cause", ""))
    said = key_words(f"{diagnosis.root_cause} {diagnosis.suggested_fix}")
    overlap = len(wanted & said) / len(wanted) if wanted else 0.0
    should_page = truth_verdict != Verdict.FLAKY.value
    return CaseResult(
        scenario_id=record["scenario_id"],
        scenario_name=record.get("scenario_name", ""),
        day=record["day"],
        memory=memory,
        truth_verdict=truth_verdict,
        verdict=diagnosis.verdict.value,
        confidence=diagnosis.confidence,
        correct=diagnosis.verdict.value == truth_verdict,
        recognised=None if truth_verdict == Verdict.NEW.value else diagnosis.verdict != Verdict.NEW,
        root_cause_match=overlap >= ROOT_CAUSE_MATCH,
        page_correct=diagnosis.page_oncall == should_page,
        evidence=len(diagnosis.evidence),
        latency_ms=diagnosis.latency_ms,
        providers=",".join(sorted({f"{c['provider']}/{c['model']}" for c in trace.llm_calls})) or "none",
        fallback=trace.fallback,
        guardrails=list(trace.guardrails),
        evidence_seen=[
            f"{e.kind() or e.type or '?'} | {','.join(e.tags[:4])} | {e.text[:140]}" for e in diagnosis.evidence[:5]
        ],
    )


def pick_records(from_day: int, scenario: str | None, limit: int) -> list[dict]:
    rows = demo_data.load_records(from_day=from_day)
    if scenario:  # exact id or name, or an id prefix: "S09" matches S09c, S09d, S09e
        rows = [r for r in rows if r["scenario_id"].startswith(scenario) or r.get("scenario_name") == scenario]
    per: dict[str, int] = defaultdict(int)
    picked = []
    for r in rows:
        if per[r["scenario_id"]] < limit:
            per[r["scenario_id"]] += 1
            picked.append(r)
    return picked


def run_eval(doctor: FailureDoctor, records: list[dict], modes: list[bool], delay: float = 0.0, log=print) -> list[CaseResult]:
    results: list[CaseResult] = []
    with SessionLocal() as session:
        for i, record in enumerate(records, 1):
            for memory in modes:
                incident = demo_data.to_incident(record, f"eval_{i:03d}_{'on' if memory else 'off'}")
                diagnosis, trace = doctor.diagnose_with_trace(incident, session, use_memory=memory)
                res = score(record, diagnosis, trace, memory)
                results.append(res)
                mark = "OK  " if res.correct else "MISS"
                log(
                    f"[{i:>2}/{len(records)}] {res.scenario_id:<5} day {res.day:>2} memory {'ON ' if memory else 'OFF'} "
                    f"{mark} {res.verdict:<15} (truth {res.truth_verdict:<15}) conf={res.confidence:.2f} "
                    f"evidence={res.evidence} {res.latency_ms}ms"
                    + (f"  [{res.fallback}]" if res.fallback else "")
                )
                if not res.correct:  # say why, so a miss can be fixed without guessing
                    for note in res.guardrails:
                        log(f"        guardrail: {note}")
                    for ev in res.evidence_seen[:3]:
                        log(f"        evidence:  {ev}")
                if delay:
                    time.sleep(delay)
    return results


def _pct(values: list[bool]) -> float | None:
    return round(100 * sum(values) / len(values), 1) if values else None


def summarise(results: list[CaseResult]) -> dict:
    """Per scenario and overall, for memory ON and OFF."""

    def block(rows: list[CaseResult]) -> dict:
        recog = [r.recognised for r in rows if r.recognised is not None]
        return {
            "n": len(rows),
            "accuracy": _pct([r.correct for r in rows]),
            "recognised": _pct(recog),
            "root_cause_match": _pct([r.root_cause_match for r in rows]),
            "page_correct": _pct([r.page_correct for r in rows]),
            "avg_confidence": round(sum(r.confidence for r in rows) / len(rows), 2) if rows else None,
            "avg_evidence": round(sum(r.evidence for r in rows) / len(rows), 1) if rows else None,
            "avg_latency_ms": round(sum(r.latency_ms for r in rows) / len(rows)) if rows else None,
            "fallbacks": sum(1 for r in rows if r.fallback),
        }

    out: dict = {"overall": {}, "by_scenario": {}}
    for memory, key in ((False, "off"), (True, "on")):
        rows = [r for r in results if r.memory == memory]
        if rows:
            out["overall"][key] = block(rows)
    scenarios = sorted({(r.scenario_id, r.scenario_name) for r in results})
    for sid, name in scenarios:
        entry = {"name": name, "truth": next(r.truth_verdict for r in results if r.scenario_id == sid)}
        for memory, key in ((False, "off"), (True, "on")):
            rows = [r for r in results if r.scenario_id == sid and r.memory == memory]
            if rows:
                entry[key] = block(rows)
        out["by_scenario"][sid] = entry
    return out


def _fmt(v, suffix="%") -> str:
    return "–" if v is None else f"{v:g}{suffix}"


def print_table(summary: dict) -> None:
    has_off, has_on = "off" in summary["overall"], "on" in summary["overall"]
    print("\nscenario                              truth            accuracy OFF -> ON     conf OFF -> ON   evidence ON")
    for sid, e in summary["by_scenario"].items():
        off, on = e.get("off", {}), e.get("on", {})
        print(
            f"{sid:<5} {e['name'][:30]:<31} {e['truth']:<16} "
            f"{_fmt(off.get('accuracy')) if has_off else '':>7} -> {_fmt(on.get('accuracy')) if has_on else '':<7}      "
            f"{_fmt(off.get('avg_confidence'), '') if has_off else '':>5} -> {_fmt(on.get('avg_confidence'), '') if has_on else '':<5}   "
            f"{_fmt(on.get('avg_evidence'), '') if has_on else ''}"
        )
    print()
    for key in ("off", "on"):
        if key in summary["overall"]:
            o = summary["overall"][key]
            print(
                f"memory {key.upper():<3}: accuracy {_fmt(o['accuracy'])}, recognised {_fmt(o['recognised'])}, "
                f"root cause {_fmt(o['root_cause_match'])}, page decision {_fmt(o['page_correct'])}, "
                f"confidence {o['avg_confidence']}, evidence {o['avg_evidence']}, {o['avg_latency_ms']} ms, "
                f"{o['n']} runs" + (f", {o['fallbacks']} offline fallbacks" if o["fallbacks"] else "")
            )


def markdown(summary: dict) -> str:
    o_off, o_on = summary["overall"].get("off", {}), summary["overall"].get("on", {})
    lines = [
        "| Metric | Memory OFF | Memory ON |",
        "| --- | --- | --- |",
        f"| Verdict accuracy | {_fmt(o_off.get('accuracy'))} | {_fmt(o_on.get('accuracy'))} |",
        f"| Known failures recognised | {_fmt(o_off.get('recognised'))} | {_fmt(o_on.get('recognised'))} |",
        f"| Root cause matches the real one | {_fmt(o_off.get('root_cause_match'))} | {_fmt(o_on.get('root_cause_match'))} |",
        f"| Correct page / don't-page decision | {_fmt(o_off.get('page_correct'))} | {_fmt(o_on.get('page_correct'))} |",
        f"| Average confidence | {_fmt(o_off.get('avg_confidence'), '')} | {_fmt(o_on.get('avg_confidence'), '')} |",
        f"| Memories cited per diagnosis | {_fmt(o_off.get('avg_evidence'), '')} | {_fmt(o_on.get('avg_evidence'), '')} |",
        "",
        "| Scenario | Truth | Accuracy OFF | Accuracy ON |",
        "| --- | --- | --- | --- |",
    ]
    for sid, e in summary["by_scenario"].items():
        lines.append(
            f"| {sid} {e['name']} | {e['truth']} | {_fmt(e.get('off', {}).get('accuracy'))} | {_fmt(e.get('on', {}).get('accuracy'))} |"
        )
    n = max(o_off.get("n", 0), o_on.get("n", 0))
    lines.append(f"\n_{n} failures from the replay window (days 15–30), Hindsight bank seeded with days 1–14._")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m app.agent.eval", description="Memory ON vs OFF evaluation")
    ap.add_argument("--from-day", type=int, default=15, help="first replay day (days before it are the seeded past)")
    ap.add_argument("--scenario", help="only this scenario (id like S01, or name)")
    ap.add_argument("--limit", type=int, default=2, help="failures per scenario (keeps the free tier happy)")
    ap.add_argument("--memory", choices=["both", "on", "off"], default="both")
    ap.add_argument("--delay", type=float, default=2.0, help="seconds between diagnoses (Groq free tier: 30 RPM, 8K TPM)")
    ap.add_argument("--out", type=Path, default=Path(settings.data_dir) / "generated" / "eval_results.jsonl")
    ap.add_argument("--markdown", action="store_true", help="also print a markdown table for the README")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = ap.parse_args()

    init_db()
    store, llm = MemoryStore(), LLMClient()
    doctor = FailureDoctor(store=store, llm=llm)
    modes = {"both": [False, True], "on": [True], "off": [False]}[args.memory]

    if not llm.available:
        print("WARNING: no GROQ_API_KEY or GEMINI_API_KEY: every diagnosis uses the offline heuristic.", file=sys.stderr)
    if True in modes:
        if not store.enabled:
            sys.exit("Memory is disabled (MEMORY_ENABLED=false). Use --memory off, or enable it in .env.")
        probe = store.search("load_orders KeyError customer_id")
        if not probe:
            print(
                f"WARNING: the Hindsight bank '{store.bank_id}' at {settings.hindsight_url} returned nothing. "
                "Seed it first: python scripts/seed_memory.py (repo root). Memory ON results will look like OFF.",
                file=sys.stderr,
            )

    try:
        records = pick_records(args.from_day, args.scenario, args.limit)
    except FileNotFoundError as exc:
        sys.exit(str(exc))
    if not records:
        sys.exit("No failures matched. Check --from-day / --scenario.")
    print(
        f"Evaluating {len(records)} failures x memory {'/'.join('ON' if m else 'OFF' for m in modes)} "
        f"(LLM chain: {' -> '.join(llm.describe()) or 'offline'})\n"
    )

    results = run_eval(doctor, records, modes, delay=args.delay)
    summary = summarise(results)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(asdict(r)) + "\n")

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print_table(summary)
    if args.markdown:
        print("\n" + markdown(summary))
    print(f"\nRaw results: {args.out}")


if __name__ == "__main__":
    main()
