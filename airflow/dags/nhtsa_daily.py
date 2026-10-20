"""Daily NHTSA pipeline: new files to S3, load bronze, dbt build (silver and gold), health check.

NHTSA regenerates its files around 07:00 to 10:30 UTC, so the run starts at 12:00 UTC.
If no file changed since the last run, everything after ingestion is skipped.
"""

import os
from datetime import UTC, datetime, timedelta

from airflow.sdk import dag, task
from cosmos import DbtTaskGroup

from common import DBT_EXECUTION, DBT_PROFILE, DBT_PROJECT, block_on_detached_tests, dbt_render, notify_failure


@dag(
    schedule="0 12 * * *",
    start_date=datetime(2026, 10, 20, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    default_args={
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
        "on_failure_callback": notify_failure,
    },
    tags=["nhtsa", "daily"],
)
def nhtsa_daily():
    @task
    def ingest() -> list[dict]:
        import nhtsa_ingest

        results = nhtsa_ingest.run(os.environ["NHTSA_BUCKET"], list(nhtsa_ingest.DATASETS), datetime.now(UTC).date())
        return [r.__dict__ for r in results]

    @task.short_circuit
    def anything_changed(results: list[dict]) -> bool:
        changed = [r["dataset"] for r in results if r["changed"]]
        print(f"Changed datasets: {changed or 'none'}")
        return bool(changed)

    @task
    def load_bronze(results: list[dict]) -> dict:
        import bronze_load

        latest_keys = {r["dataset"]: r["s3_key"] for r in results}
        with bronze_load.connect() as conn:
            return bronze_load.load(conn, latest_keys)

    @task
    def health_check() -> dict:
        import bronze_load

        with bronze_load.connect() as conn:
            return bronze_load.check_row_counts(conn)

    results = ingest()
    loaded = load_bronze(results)
    anything_changed(results) >> loaded

    dbt_build = DbtTaskGroup(
        group_id="dbt_build",
        project_config=DBT_PROJECT,
        profile_config=DBT_PROFILE,
        execution_config=DBT_EXECUTION,
        render_config=dbt_render(),
    )
    block_on_detached_tests(dbt_build.iter_tasks())

    loaded >> dbt_build >> health_check()


nhtsa_daily()
