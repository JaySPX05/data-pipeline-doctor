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
