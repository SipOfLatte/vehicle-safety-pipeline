"""Weekly full rebuild of the incremental silver models (an Airflow DAG built by Cosmos).

Incremental runs only touch complaints whose source rows changed, so a change to the model
SQL wouldn't reach existing rows. Rebuilding once a week applies it everywhere. It takes
about 15 seconds on an X-Small warehouse. Runs Sunday, after the daily run.
"""

from datetime import datetime, timedelta, timezone

from cosmos import DbtDag

from common import DBT_EXECUTION, DBT_PROFILE, DBT_PROJECT, dbt_render, notify_failure

silver_full_refresh = DbtDag(
    dag_id="silver_full_refresh",
    schedule="0 14 * * 0",
    start_date=datetime(2026, 10, 20, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=10), "on_failure_callback": notify_failure},
    tags=["nhtsa", "weekly"],
    project_config=DBT_PROJECT,
    profile_config=DBT_PROFILE,
    execution_config=DBT_EXECUTION,
    render_config=dbt_render(select=["complaints", "complaint_components"]),
    operator_args={"full_refresh": True},
)
