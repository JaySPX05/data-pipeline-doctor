# Data (owner: P3)

QuickBasket is a fictional quick-commerce startup in Bengaluru. Its data platform has 6 DAGs:
`orders_etl`, `payments_daily`, `events_ingest`, `inventory_sync`, `marketing_attribution`, `customer_360`.

## Layout

```
data/
  scenarios/        one YAML per repeating failure pattern (hand-written)
  generated/        output of scripts/generate_history.py (git-ignored)
    history.jsonl     30 days of failure events + ground truth
    change_feed.jsonl schema changes, deploys, vendor releases, IAM rotations
  pfd.sqlite3       backend incident store (git-ignored)
```

## Scenario YAML format

See `scenarios/01_vendor_schema_change.yaml`. The fields:

| Field | Meaning |
| --- | --- |
| `id`, `name`, `dag_id`, `task_id`, `operator` | Identity of the failing task |
| `truth.verdict` / `root_cause` / `fix` | Ground truth for `replay.py --auto-feedback` |
| `truth.minutes_to_fix` | List, one per occurrence (shows MTTR dropping), or a single int |
| `days`, `hour` | When it happens (day 1-30, UTC hour) |
| `change_event` | Optional upstream change emitted `hours_before` each failure |
| `vars` | Placeholder values: a list (random pick), `"int:a-b"`, or `"hex:n"` |
| `sequential` | Vars picked in order of occurrence instead of randomly |
| `log_template` | Log text. `{ts}`, `{run_id}`, `{date}` are built in. Escape literal braces as `{{ }}` |

## Day 1 TODO: the remaining scenarios

Done: 01 vendor schema change, 02 Kafka timeout (flaky), 04 expired S3 credentials.

- [ ] `03_spark_oom_month_end.yaml`: `payments_daily.aggregate`, `java.lang.OutOfMemoryError: Java heap space`, days 28-31 → KNOWN_ISSUE
- [ ] `05_upstream_dropped_column.yaml`: `marketing_attribution.join_spend`, `AnalysisException: cannot resolve 'fee_amount'`, Payments team migration → UPSTREAM_CHANGE
- [ ] `06_duplicate_key_cdc.yaml`: `customer_360.merge_profiles`, `IntegrityError: duplicate key value`, late CDC events → KNOWN_ISSUE
- [ ] `07_pg_connection_pool.yaml`: `orders_etl.extract_pg`, `psycopg2.OperationalError: too many connections`, overlapping backfill → KNOWN_ISSUE
- [ ] `08_dq_null_check_flaky.yaml`: `inventory_sync.dq_checks`, Great Expectations null check, warehouse sync lag → FLAKY
- [ ] `09_wildcards.yaml` (or 5 small files): one-off NEW failures with unique signatures

Make the logs look real. Copy real Airflow/Spark/boto log formats and vary the noise (hosts, row counts, line numbers) so no two logs are identical but the fingerprints still match.
