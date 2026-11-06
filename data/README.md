# data/: QuickBasket failure history (P3)

Fictional quick-commerce company in Bengaluru: 6 DAGs, 25 tasks (`dags.yaml`), 30 days (Oct 1-30, 2026),
99 failures from 8 repeating scenarios + 5 wildcards, plus a matching change feed.

```
scenarios/*.yaml        scenario definitions (edit these, then regenerate)
generated/              COMMITTED output: incidents.jsonl, change_feed.jsonl, manifest.json
```

## Commands (from repo root)

```bash
python scripts/generate_history.py              # regenerate (deterministic, seed 42)
python scripts/generate_history.py --llm        # optional Groq-written context lines
python scripts/test_data_fingerprints.py        # or: pytest scripts/test_data_fingerprints.py -q
python scripts/seed_memory.py --dry-run         # preview what goes into Hindsight (days 1-14)
python scripts/seed_memory.py                   # seed Hindsight
python scripts/replay.py --speed 60x --auto-feedback   # live demo replay, days 15-30
python scripts/reset_demo.py --seeded           # clean state for a rehearsal
python scripts/reset_demo.py --empty            # Day 0 cold start (memory-off baseline)
```

## Scenario YAML schema

| key | meaning |
|---|---|
| `id`, `name` | S01..S08; wildcards are S09a-e (`variants:` in `09_wildcards.yaml`) |
| `dag_id`, `task_id` | must exist in `dags.yaml` |
| `verdict` | ground truth: KNOWN_ISSUE / FLAKY / UPSTREAM_CHANGE / NEW |
| `error_lines` | the exception lines (the fingerprint is built from these; keep them constant) |
| `core_frames` | innermost stack frames (always present); outer frames vary randomly |
| `root_cause`, `fix` | ground truth; `{version}`-style placeholders are filled per incident |
| `minutes_to_resolve` | `{base, jitter}` or `{by_occurrence: [..]}` |
| `auto_retry_passes`, `retry_count` | flaky scenarios |
| `schedule` | `days: [..]` or `random: {count}`; plus `hour_range` |
| `caused_by_change` | creates a change-feed event `lead_hours` before the failure (`once: true` = one event for all) |
| `variables` | `int`, `float`, `choice`, `seq` generators for fields that vary per log |
| `hold_feedback` | replay.py will not auto-confirm this incident (correction demo) |

## Fingerprint-safe variation: the rules

The fingerprint comes from the **exception lines**. Only these may vary between logs of one scenario:
timestamps, run IDs, row counts, hosts, pids, file paths, line numbers, object IDs, number of outer
stack frames. Never put a varying value in `error_lines` unless it is a number or path (those get stripped).
Never write `word=value` in an error line (it looks like metadata to the local fingerprinter).

## Where each scenario lives in the demo

| Scenario | Seeded (days 1-14) | Replayed (15-30) | Demo moment |
|---|---|---|---|
| S01 vendor schema | 2 (d3, d12) | 2 (d19, d27) | Memory ON: UPSTREAM_CHANGE with 2 past incidents + vendor release |
| S02 Kafka flaky | 22 | 18 | FLAKY + don't page |
| S04 IAM rotation | 2 | 2 | Health Brief: "fix the IAM rotation, it caused 4 incidents" |
| S09c wildcard | 0 | 1 (d16, held) | Correction: wrong, engineer fixes, `replay.py --only S09c` is right |
| S05 dropped column | 0 | 3 (d20, 21, 23) | Learning in-flight: first NEW-ish, then recognised |

## Assumptions to verify against other people's code (all in `scripts/_common.py`)

1. `failure_payload()` / `feedback_payload()` vs `backend/app/models/schemas.py`
2. `fingerprint_log()` auto-imports `app.services.fingerprint`; if its function name differs, edit the name list
3. Hindsight client calls (`retain`, `create_bank`, `delete_bank`) vs P2's `hindsight_store.py`
4. Whether the agent's tools read `data/generated/change_feed.jsonl` (ask P1)
