#!/usr/bin/env python3
"""Generate 30 days of QuickBasket pipeline failures + a matching change feed.

Outputs (committed, so setup never depends on an LLM):
  data/generated/incidents.jsonl     one failure per line, with ground truth under "truth"
  data/generated/change_feed.jsonl   schema changes / deploys / vendor releases
  data/generated/manifest.json       counts, seed, which fingerprinter was used

Two layers:
  1. Deterministic (seeded RNG): schedule, timestamps, row counts, hosts, paths, stack depth.
  2. Optional --llm: Groq adds realistic INFO/WARNING lines BEFORE the error. Cached, validated,
     and silently skipped for any line that could change the fingerprint.

Usage:
  python scripts/generate_history.py                # deterministic, seed 42
  python scripts/generate_history.py --llm          # + Groq context lines (needs GROQ_API_KEY)
  python scripts/generate_history.py --no-stack-variation   # if the fingerprint test fails on frames
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _common as C  # noqa: E402

SUB = re.compile(r"\{(\w+)\}")
AF = "/home/airflow/.local/lib/python3.11/site-packages/airflow"
EXTRA_FRAMES = [   # outer frames, randomly included (keeps order) to vary stack depth
    (f"{AF}/models/taskinstance.py", "_execute_task", "result = execute_callable(context=context)"),
    (f"{AF}/operators/python.py", "execute", "return_value = self.execute_callable()"),
    (f"{AF}/operators/python.py", "execute_callable", "return self.python_callable(*self.op_args, **self.op_kwargs)"),
    ("{path}/common/runner.py", "run_step", "return step.run(ctx)"),
    ("{path}/common/io.py", "read_frame", "return self._reader(self.uri)"),
]
FILLER = [
    "[{ts}] INFO - Dependencies all met for task {dag}.{task}",
    "[{ts}] INFO - Executing task on host {host}",
    "[{ts}] INFO - Using connection {conn}",
    "[{ts}] INFO - Starting attempt {try_number} of {max_tries}",
    "[{ts}] WARNING - Task retry policy: retries=2 delay=300s",
]
DEFAULT_VARS = {
    "rows": {"type": "int", "range": [10000, 90000]},
    "host": {"type": "choice", "values": ["airflow-w-01", "airflow-w-02", "airflow-w-03", "airflow-w-04"]},
    "pid": {"type": "int", "range": [1000, 32000]},
    "objid": {"type": "int", "range": [100, 999]},
    "partition": {"type": "int", "range": [0, 11]},
    "profile_id": {"type": "int", "range": [10000, 99999]},
    "ln2": {"type": "int", "range": [2, 900]},
    "ch": {"type": "int", "range": [100, 99999]},
    "release": {"type": "choice", "values": ["r1840", "r1841", "r1842", "r1843"]},
    "conn": {"type": "choice", "values": ["pg_orders", "s3_inventory", "kafka_main", "spark_default"]},
}
# Unrelated change-feed noise so the agent's tools have to discriminate. (day, hour, type, source, summary, affects)
NOISE = [
    (2, 14, "deploy", "ci", "Deployed events_ingest v1.18 (logging format tweak)", ["events_ingest.write_lake"]),
    (4, 11, "deploy", "ci", "Deployed marketing_attribution v0.9 (new ROI chart)", ["marketing_attribution.publish_roi"]),
    (7, 16, "schema_change", "warehouse", "Added nullable column loyalty_tier to dim_customers", ["customer_360.publish_profiles"]),
    (8, 10, "deploy", "ci", "Deployed payments_daily v2.4 (ledger rounding fix)", ["payments_daily.publish_ledger"]),
    (10, 15, "vendor_release", "vendor_feed", "Vendor feed v2.3 docs update (no schema change)", ["orders_etl.validate_feed"]),
    (13, 12, "deploy", "ci", "Deployed inventory_sync v1.7 (Slack notifier)", ["inventory_sync.notify_ops"]),
    (15, 17, "schema_change", "warehouse", "Added index on orders.created_at", ["orders_etl.load_returns"]),
    (17, 11, "deploy", "ci", "Deployed customer_360 v3.1 (profile dedupe flag, off by default)", ["customer_360.extract_cdc"]),
    (21, 13, "deploy", "ci", "Deployed events_ingest v1.19 (consumer config comments)", ["events_ingest.dedupe_events"]),
    (24, 16, "schema_change", "warehouse", "Renamed view v_orders_daily to v_orders_daily_v2 (alias kept)", ["orders_etl.publish_orders"]),
    (26, 10, "deploy", "ci", "Deployed marketing_attribution v1.0 (UTM parser)", ["marketing_attribution.attribute_touchpoints"]),
    (29, 15, "deploy", "ci", "Deployed payments_daily v2.5 (metrics only)", ["payments_daily.extract_payments"]),
]


def fill(text: str, vars: dict) -> str:
    return SUB.sub(lambda m: str(vars.get(m.group(1), m.group(0))), text)


def sample_var(spec, rng, occ):
    t = spec["type"]
    if t == "int":
        return rng.randint(*spec["range"])
    if t == "float":
        return round(rng.uniform(*spec["range"]), 1)
    if t == "choice":
        return rng.choice(spec["values"])
    if t == "seq":
        return f"{spec.get('prefix', '')}{spec.get('start', 0) + occ}"
    raise ValueError(f"unknown variable type {t}")


def minutes(sc, rng, occ):
    m = sc["minutes_to_resolve"]
    if "by_occurrence" in m:
        return m["by_occurrence"][min(occ, len(m["by_occurrence"]) - 1)]
    return max(1, m["base"] + rng.randint(-m.get("jitter", 0), m.get("jitter", 0)))


def render_frames(sc, rng, vars, stack_variation):
    extras = []
    if stack_variation:
        k = rng.randint(1, 4)
        extras = [EXTRA_FRAMES[i] for i in sorted(rng.sample(range(len(EXTRA_FRAMES)), k))]
    else:
        extras = EXTRA_FRAMES[:2]
    frames = extras + [(f["file"], f["fn"], f["code"]) for f in sc["core_frames"]]
    out = []
    for file, fn, code in frames:
        out.append(f'  File "{fill(file, vars)}", line {rng.randint(10, 400)}, in {fn}')
        out.append(f"    {code}")
    return "\n".join(out)


def render_log(sc, rng, vars, ts, run_id, try_number, stack_variation, llm_lines):
    tsf = ts.strftime("%Y-%m-%d %H:%M:%S,") + f"{rng.randint(0, 999):03d}"
    v = {**vars, "ts": tsf, "dag": sc["dag_id"], "task": sc["task_id"], "run_id": run_id,
         "try_number": try_number, "max_tries": 3}
    if llm_lines:
        lines = list(llm_lines)
    else:   # filler lines get earlier timestamps than the ERROR line
        picked = rng.sample(FILLER, rng.randint(2, 3))
        lines = []
        for k, l in enumerate(picked):
            earlier = ts - timedelta(seconds=(len(picked) - k) * rng.randint(1, 4))
            lines.append(fill(l, {**v, "ts": earlier.strftime("%Y-%m-%d %H:%M:%S,") + f"{rng.randint(0, 999):03d}"}))
    lines += [
        fill(f"[{{ts}}] ERROR - Task failed: {sc['dag_id']}.{sc['task_id']} (run_id={{run_id}}, try={{try_number}})", v),
        "Traceback (most recent call last):",
        render_frames(sc, rng, vars, stack_variation),
    ]
    lines += [fill(l, v) for l in sc["error_lines"]]
    lines.append(fill("rows_read={rows} host={host} pid={pid}", v))
    return "\n".join(lines)


# ------------------------------------------------------------------ optional LLM layer
def llm_context_lines(sc, inc_key, ts, cache, cache_path):
    """Ask Groq for 2-4 benign pre-failure log lines. Returns [] on any problem."""
    if inc_key in cache:
        return cache[inc_key]
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.environ["GROQ_API_KEY"], base_url="https://api.groq.com/openai/v1")
        prompt = (f"Write 2-4 realistic Airflow task log lines (INFO/WARNING only) that appear just BEFORE a "
                  f"failure of {sc['dag_id']}.{sc['task_id']}. Start each with [{ts:%Y-%m-%d %H:%M:%S},123] LEVEL - . "
                  f"No errors, no tracebacks, no words like error/exception/failed. Return JSON: {{\"lines\": [...]}}")
        r = client.chat.completions.create(model="openai/gpt-oss-120b",
                                           messages=[{"role": "user", "content": prompt}],
                                           response_format={"type": "json_object"}, temperature=0.8)
        lines = json.loads(r.choices[0].message.content)["lines"]
        bad = re.compile(r"error|exception|traceback|fail", re.I)
        lines = [l for l in lines if isinstance(l, str) and l.startswith("[") and not bad.search(l)][:4]
    except Exception as e:
        print(f"  llm skipped for {inc_key}: {type(e).__name__}", file=sys.stderr)
        lines = []
    cache[inc_key] = lines
    cache_path.write_text(json.dumps(cache, indent=1))
    return lines


# ------------------------------------------------------------------------- build
def build(seed=42, use_llm=False, stack_variation=True):
    rng = random.Random(seed)
    scenarios = C.load_scenarios()
    cache_path = C.GEN_DIR / "llm_cache.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    # 1. occurrences ---------------------------------------------------------------
    raw = []
    for sc in scenarios:
        sch, hrs = sc["schedule"], sc["schedule"].get("hour_range", [0, 23])
        if "days" in sch:
            days = list(sch["days"])
        else:
            lo, hi = sch["random"].get("window", [1, 30])
            days = sorted(rng.randint(lo, hi) for _ in range(sch["random"]["count"]))
        for occ, d in enumerate(days):
            ts = C.START_DATE + timedelta(days=d - 1, hours=rng.randint(*hrs),
                                          minutes=rng.randint(0, 59), seconds=rng.randint(0, 59))
            raw.append((ts, sc, occ))
    raw.sort(key=lambda x: (x[0], x[1]["id"]))

    # 2. incidents -----------------------------------------------------------------
    incidents = []
    for n, (ts, sc, occ) in enumerate(raw, 1):
        inc_id = f"INC-{n:04d}"
        specs = {**DEFAULT_VARS, **sc.get("variables", {})}
        vars = {k: sample_var(s, rng, occ) for k, s in specs.items()}
        vars["path"] = f"/opt/airflow/dags/{vars['release']}/{sc['dag_id']}"
        run_id = f"scheduled__{ts.replace(minute=0, second=0).isoformat()}+00:00"
        flaky = sc.get("auto_retry_passes", False)
        try_number = 1 if flaky else 3
        llm_lines = llm_context_lines(sc, inc_id, ts, cache, cache_path) if use_llm else []
        log = render_log(sc, rng, vars, ts, run_id, try_number, stack_variation, llm_lines)
        mins = minutes(sc, rng, occ)
        incidents.append({
            "incident_id": inc_id, "scenario_id": sc["id"], "scenario_name": sc["name"],
            "day": C.day_of(ts), "failed_at": ts.isoformat(), "dag_id": sc["dag_id"], "task_id": sc["task_id"],
            "run_id": run_id, "try_number": try_number, "log_tail": log, "error_class": sc["error_class"],
            "error_summary": fill(next((l for l in sc["error_lines"] if sc["error_class"] in l), sc["error_lines"][0]).lstrip(": "), vars), "hold_feedback": bool(sc.get("hold_feedback")),
            "truth": {"verdict": sc["verdict"], "root_cause": fill(sc["root_cause"], vars),
                      "fix": fill(sc["fix"], vars), "minutes_to_resolve": mins,
                      "auto_retry_passes": flaky, "retry_count": sc.get("retry_count", 0),
                      "resolved_at": (ts + timedelta(minutes=mins)).isoformat(), "caused_by_change": None},
            "_sc": sc, "_vars": vars, "_occ": occ,
        })

    # 3. change feed ---------------------------------------------------------------
    pending = []        # (ts, type, source, summary, affects, [incident idx...])
    once_slot = {}
    for i, inc in enumerate(incidents):
        cc = inc["_sc"].get("caused_by_change")
        if not cc:
            continue
        key = inc["scenario_id"]
        if cc.get("once") and key in once_slot:
            pending[once_slot[key]][5].append(i)
            continue
        ts = datetime.fromisoformat(inc["failed_at"]) - timedelta(hours=cc["lead_hours"])
        pending.append([ts, cc["type"], cc["source"], fill(cc["summary"], inc["_vars"]),
                        [f"{inc['dag_id']}.{inc['task_id']}"], [i]])
        if cc.get("once"):
            once_slot[key] = len(pending) - 1
    for d, h, typ, src, summ, aff in NOISE:
        pending.append([C.START_DATE + timedelta(days=d - 1, hours=h), typ, src, summ, aff, []])
    pending.sort(key=lambda p: p[0])
    changes = []
    for n, (ts, typ, src, summ, aff, idxs) in enumerate(pending, 1):
        cid = f"chg_{n:03d}"
        changes.append({"id": cid, "ts": ts.isoformat(), "type": typ, "source": src, "summary": summ,
                        "affects": aff, "day": C.day_of(ts)})
        for i in idxs:
            incidents[i]["truth"]["caused_by_change"] = cid

    # 4. recent_changes (<=24h before failure, same task or DAG) + fingerprints ------
    seen = Counter()
    for inc in incidents:
        t0 = datetime.fromisoformat(inc["failed_at"])
        inc["recent_changes"] = [
            {"id": c["id"], "ts": c["ts"], "type": c["type"], "source": c["source"], "summary": c["summary"]}
            for c in changes
            if timedelta(0) <= t0 - datetime.fromisoformat(c["ts"]) <= timedelta(hours=24)
            and any(a == f"{inc['dag_id']}.{inc['task_id']}" or a.startswith(inc["dag_id"] + ".") for a in c["affects"])
        ]
        inc["fingerprint"] = C.fingerprint_log(inc["log_tail"], inc["task_id"])
        seen[inc["fingerprint"]] += 1
        inc["occurrence"] = seen[inc["fingerprint"]]
        for k in ("_sc", "_vars", "_occ"):
            inc.pop(k)

    manifest = {
        "seed": seed, "llm": use_llm, "stack_variation": stack_variation,
        "fingerprinter": C.fingerprint_backend_name(), "start_date": C.START_DATE.date().isoformat(),
        "total_incidents": len(incidents), "total_changes": len(changes),
        "seed_window_incidents": sum(1 for i in incidents if i["day"] <= C.SEED_DAYS[1]),
        "replay_window_incidents": sum(1 for i in incidents if i["day"] >= C.REPLAY_DAYS[0]),
        "by_scenario": dict(sorted(Counter(i["scenario_id"] for i in incidents).items())),
        "by_scenario_seed_window": dict(sorted(Counter(i["scenario_id"] for i in incidents if i["day"] <= 14).items())),
        "by_scenario_replay_window": dict(sorted(Counter(i["scenario_id"] for i in incidents if i["day"] >= 15).items())),
        "distinct_fingerprints": len({i["fingerprint"] for i in incidents}),
    }
    return incidents, changes, manifest


def check(incidents) -> list[str]:
    problems, by_sc = [], defaultdict(set)
    for i in incidents:
        by_sc[i["scenario_id"]].add(i["fingerprint"])
    for sid, fps in by_sc.items():
        if len(fps) > 1:
            problems.append(f"{sid}: {len(fps)} different fingerprints (log variation leaked into the fingerprint)")
    for i in incidents:       # the agent must see the error class we intended
        got = C.backend_extract_error(i["log_tail"])
        if got and got[0] != i["error_class"]:
            problems.append(f"{i['incident_id']} ({i['scenario_id']}): backend reads error class "
                            f"{got[0]!r}, scenario says {i['error_class']!r}")
            break
    owner = {}
    for sid, fps in by_sc.items():
        for fp in fps:
            if fp in owner and owner[fp] != sid:
                problems.append(f"{sid} and {owner[fp]} share fingerprint {fp}")
            owner[fp] = sid
    return problems


def demo_warnings(m) -> list[str]:
    w, s, r = [], m["by_scenario_seed_window"], m["by_scenario_replay_window"]
    if s.get("S01", 0) < 2: w.append("S01 needs >=2 incidents in days 1-14 (Memory ON demo)")
    if m["by_scenario"].get("S04") != 4: w.append("S04 should have exactly 4 incidents (Health Brief line)")
    if r.get("S02", 0) < 5: w.append("S02 needs several Kafka timeouts in days 15-30 for FLAKY/don't-page")
    if s.get("S02", 0) < 3: w.append("S02 needs history in days 1-14 so the agent can call it flaky")
    if r.get("S09c", 0) != 1: w.append("S09c (correction incident) must be in the replay window")
    return w


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--llm", action="store_true", help="add Groq-written context lines (optional)")
    ap.add_argument("--no-stack-variation", action="store_true")
    ap.add_argument("--out", type=Path, default=C.GEN_DIR)
    a = ap.parse_args()

    incidents, changes, manifest = build(a.seed, a.llm, not a.no_stack_variation)
    problems = check(incidents)
    if problems:
        print("FINGERPRINT PROBLEMS:\n  " + "\n  ".join(problems), file=sys.stderr)
        print("Try --no-stack-variation, or fix the template.", file=sys.stderr)
        sys.exit(1)
    C.write_jsonl(a.out / "incidents.jsonl", incidents)
    C.write_jsonl(a.out / "change_feed.jsonl", changes)
    (a.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({k: manifest[k] for k in ("total_incidents", "total_changes", "seed_window_incidents",
                                                "replay_window_incidents", "by_scenario", "fingerprinter")}, indent=2))
    for w in demo_warnings(manifest):
        print("WARNING:", w)


if __name__ == "__main__":
    main()
