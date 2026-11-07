// Mock data shaped exactly like backend/app/models/schemas.py. Keep in sync with the schemas.

const evidence = [
  {
    memory_id: "m_101",
    text: "Root cause of fp_13e8231d (KeyError in orders_etl.load_orders): Vendor FreshFarm renamed column customer_id to cust_id in order feed v2.4. Fix: Map cust_id -> customer_id in config/vendor_schema.yaml. Took 12 min to fix.",
    occurred_at: "2026-10-11T03:20:00",
    document_id: "inc_8f21a0c4d1-resolution",
    type: "experience",
  },
  {
    memory_id: "m_102",
    text: "Vendor FreshFarm released order feed v2.5: column customer_id renamed to cust_id.",
    occurred_at: "2026-10-18T22:10:00",
    document_id: "chg_32e706298f",
    type: "world",
  },
];

export const incidents = [
  {
    id: "inc_demo_001",
    dag_id: "orders_etl",
    task_id: "load_orders",
    run_id: "scheduled__2026-10-19T03:00:00+00:00",
    try_number: 1,
    occurred_at: "2026-10-19T03:12:44",
    error_class: "KeyError",
    error_message: "'customer_id'",
    fingerprint: "fp_13e8231d",
    log_tail:
      "[2026-10-19T03:12:41.120+0000] {load.py:57} INFO - Loaded 48211 rows, 23 columns\n[2026-10-19T03:12:44.004+0000] {taskinstance.py:1937} ERROR - Task failed with exception\nTraceback (most recent call last):\n  File \"/opt/airflow/dags/orders/load.py\", line 88, in load_orders\n    df[\"customer_id\"] = df[\"customer_id\"].astype(\"int64\")\nKeyError: 'customer_id'",
    recent_changes: [],
    status: "diagnosed",
    diagnosis: {
      verdict: "UPSTREAM_CHANGE",
      root_cause: "FreshFarm order feed v2.5 renamed customer_id to cust_id again, the same change that broke load_orders on Oct 3 and Oct 11.",
      suggested_fix: "1. Map cust_id -> customer_id in config/vendor_schema.yaml\n2. Clear and re-run orders_etl.load_orders\n3. Ask FreshFarm to announce schema changes in #vendor-feeds",
      confidence: 0.93,
      page_oncall: true,
      evidence,
      memory_used: true,
      model: "openai/gpt-oss-120b",
      latency_ms: 2140,
    },
    feedback: null,
  },
  {
    id: "inc_demo_002",
    dag_id: "events_ingest",
    task_id: "pull_kafka",
    run_id: "scheduled__2026-10-19T02:00:00+00:00",
    try_number: 1,
    occurred_at: "2026-10-19T02:31:09",
    error_class: "KafkaTimeoutError",
    error_message: "Failed to update metadata after 60.0 secs.",
    fingerprint: "fp_99c08388",
    log_tail: "kafka.errors.KafkaTimeoutError: Failed to update metadata after 60.0 secs.",
    recent_changes: [],
    status: "diagnosed",
    diagnosis: {
      verdict: "FLAKY",
      root_cause: "Transient Kafka metadata timeout. This task failed this way 6 times in October and passed on retry every time.",
      suggested_fix: "Auto-retry once. No page needed.",
      confidence: 0.88,
      page_oncall: false,
      evidence: [],
      memory_used: true,
      model: "openai/gpt-oss-120b",
      latency_ms: 1630,
    },
    feedback: null,
  },
];

export const memoryOffDiagnosis = {
  verdict: "NEW",
  root_cause: "KeyError: the column 'customer_id' is missing from the DataFrame.",
  suggested_fix: "Check whether the source data contains the expected columns.",
  confidence: 0.35,
  page_oncall: true,
  evidence: [],
  memory_used: false,
  model: "openai/gpt-oss-120b",
  latency_ms: 1890,
};

export const learningCurve = Array.from({ length: 16 }, (_, i) => ({
  day: `2026-10-${String(15 + i).padStart(2, "0")}`,
  incidents: 2 + (i % 3),
  auto_diagnosed_pct: Math.min(95, 20 + i * 5),
  avg_latency_ms: 2600 - i * 40,
  accuracy_pct: Math.min(100, 40 + i * 4),
}));

export const healthBrief =
  "Pipeline Health Brief\n\n1. events_ingest.pull_kafka is the noisiest task (9 failures), but it is always flaky: auto-retry, no page.\n2. FreshFarm vendor releases broke orders_etl.load_orders 4 times. Fix permanently with a schema contract check before load.\n3. The weekly IAM key rotation for svc-inventory-export caused 4 incidents. Move the Airflow connection to Secrets Manager.";

export const memories = evidence;

const MEMORY_QUERY_STOP_WORDS = new Set([
  "a", "an", "and", "are", "does", "did", "for", "how", "i", "is", "of", "the", "to", "was", "what", "when", "which", "who", "why",
]);

export function searchMemories(query) {
  const terms = String(query || "")
    .toLowerCase()
    .split(/[^a-z0-9_]+/)
    .filter((term) => term && !MEMORY_QUERY_STOP_WORDS.has(term));

  if (terms.length === 0) return [];

  return memories
    .map((memory, index) => {
      const searchableText = [
        memory.text,
        memory.type,
        memory.occurred_at,
        memory.document_id,
        ...(memory.tags || []),
      ].filter(Boolean).join(" ").toLowerCase();
      const score = terms.reduce((total, term) => total + Number(searchableText.includes(term)), 0);
      return { memory, index, score };
    })
    .filter(({ score }) => score > 0)
    .sort((left, right) => right.score - left.score || left.index - right.index)
    .map(({ memory }) => memory);
}
