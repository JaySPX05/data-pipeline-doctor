"""Demo DAG that fails like scenario 1 (vendor schema change). Nice-to-have for the live demo. Owner: P7."""
from datetime import datetime

import pandas as pd
from airflow import DAG
from airflow.operators.python import PythonOperator

from failure_callback import notify_failure_doctor


def load_orders():
    df = pd.DataFrame({"cust_id": [101, 102], "order_total": [499.0, 1299.0]})  # vendor renamed the column
    df["customer_id"] = df["customer_id"].astype("int64")  # -> KeyError: 'customer_id'


with DAG(
    dag_id="orders_etl",
    start_date=datetime(2026, 10, 1),
    schedule=None,
    catchup=False,
    default_args={"on_failure_callback": notify_failure_doctor, "retries": 0},
) as dag:
    PythonOperator(task_id="load_orders", python_callable=load_orders)
