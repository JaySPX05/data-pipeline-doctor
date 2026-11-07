from app.services.fingerprint import extract_error, fingerprint, normalize

LOG_A = """[2026-10-12T03:12:44.120+0000] {taskinstance.py:1937} ERROR - Task failed with exception
Traceback (most recent call last):
  File "/opt/airflow/dags/orders/load.py", line 88, in load_orders
    df["customer_id"] = df["customer_id"].astype(int)
KeyError: 'customer_id'
"""

LOG_B = """[2026-10-27T02:58:01.004+0000] {taskinstance.py:1941} ERROR - Task failed with exception
Traceback (most recent call last):
  File "/opt/airflow/dags/orders/load.py", line 91, in load_orders
    df["customer_id"] = df["customer_id"].astype(int)
KeyError: 'customer_id'
"""

LOG_C = LOG_B.replace("customer_id", "order_total")

KAFKA = "kafka.errors.KafkaTimeoutError: Failed to update metadata after 60.0 secs on host ip-10-0-3-17"


def test_same_root_cause_same_fingerprint():
    assert fingerprint("load_orders", LOG_A)[0] == fingerprint("load_orders", LOG_B)[0]


def test_different_column_different_fingerprint():
    assert fingerprint("load_orders", LOG_A)[0] != fingerprint("load_orders", LOG_C)[0]


def test_extract_error_class():
    assert extract_error(LOG_A) == ("KeyError", "'customer_id'")
    assert extract_error(KAFKA)[0] == "KafkaTimeoutError"


def test_normalize_strips_noise():
    out = normalize("2026-10-12 03:12:44 worker-7f3a rows=18234 id=3f2a9c1e-5b7d-4e8a-9c21-7d4e5f6a8b90")
    assert "18234" not in out and "2026" not in out and "3f2a9c1e" not in out


PY4J_OOM = """  File "/opt/spark/python/lib/pyspark.zip/pyspark/sql/readwriter.py", line {line}, in parquet
    self._jwrite.parquet(path)
py4j.protocol.Py4JJavaError: An error occurred while calling o{obj}.save.
: java.lang.OutOfMemoryError: Java heap space
rows_read={rows} host=airflow-w-0{host} pid={pid}
"""

JAVA_CAUSED_BY = """Exception in thread "main" org.apache.spark.SparkException: Job aborted due to stage failure: Task {t} failed
\tat org.apache.spark.scheduler.DAGScheduler.failJobAndIndependentStages(DAGScheduler.scala:2672)
Caused by: java.sql.BatchUpdateException: Batch entry {n} was aborted
\tat org.postgresql.jdbc.BatchResultHandler.handleError(BatchResultHandler.java:165)
Caused by: org.postgresql.util.PSQLException: ERROR: duplicate key value violates unique constraint "customers_pkey"
\tat org.postgresql.core.v3.QueryExecutorImpl.receiveErrorResponse(QueryExecutorImpl.java:2676)
\t... {more} more
"""


def test_py4j_wrapper_uses_jvm_cause_and_ignores_object_ids():
    a = PY4J_OOM.format(line=53, obj=697, rows=4687726, host=4, pid=22540)
    b = PY4J_OOM.format(line=135, obj=968, rows=6443863, host=1, pid=27835)
    fp_a, cls, msg = fingerprint("aggregate", a)
    assert (cls, msg) == ("OutOfMemoryError", "Java heap space")
    assert fp_a == fingerprint("aggregate", b)[0]


def test_java_caused_by_chain_uses_deepest_cause():
    a = JAVA_CAUSED_BY.format(t=3, n=17, more=12)
    b = JAVA_CAUSED_BY.format(t=9, n=402, more=31)
    fp_a, cls, msg = fingerprint("merge_profiles", a)
    assert cls == "PSQLException" and "duplicate key" in msg
    assert fp_a == fingerprint("merge_profiles", b)[0]


def test_plain_colon_lines_are_not_errors():
    assert extract_error("INFO - starting\n: nothing to see here")[0] == "UnknownError"
