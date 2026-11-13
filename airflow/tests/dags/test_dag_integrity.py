"""DAG checks that run without Snowflake or AWS: every DAG imports, alerts are wired, schedules are set."""

import pytest
from airflow.models import DagBag

EXPECTED = {"nhtsa_daily": "0 12 * * *", "silver_full_refresh": "0 14 * * 0"}


@pytest.fixture(scope="module")
def dagbag():
    return DagBag(dag_folder="dags", include_examples=False)


def test_no_import_errors(dagbag):
    assert dagbag.import_errors == {}


@pytest.mark.parametrize("dag_id,schedule", EXPECTED.items())
def test_dag_schedule_and_alerts(dagbag, dag_id, schedule):
    dag = dagbag.get_dag(dag_id)
    assert dag is not None, f"{dag_id} not found"
    assert dag.timetable.expression == schedule
    assert not dag.catchup
    for task in dag.tasks:
        assert task.on_failure_callback, f"{dag_id}.{task.task_id} has no failure alert"


def test_daily_dag_order(dagbag):
    dag = dagbag.get_dag("nhtsa_daily")
    assert dag.get_task("ingest").downstream_task_ids == {"load_bronze"}
    assert dag.get_task("load_bronze").downstream_task_ids == {"anything_loaded"}
    assert dag.get_task("anything_loaded").downstream_task_ids == {"health_check"}
    # The health check gates dbt: every dbt task waits for it, directly or through another dbt task.
    first = dag.get_task("health_check").downstream_task_ids
    assert first and all(t.startswith("dbt_build.") for t in first)
    assert all(
        "health_check" in {u.task_id for u in t.get_flat_relatives(upstream=True)}
        for t in dag.tasks if t.task_id.startswith("dbt_build.")
    )


def test_tests_on_two_models_wait_for_both(dagbag):
    """A relationships test must run after both models it reads, not right after the first one."""
    dag = dagbag.get_dag("nhtsa_daily")
    task = next(t for t in dag.tasks if "relationships_recall_components_vehicle_name_key" in t.task_id)
    assert {"dbt_build.recall_components.test", "dbt_build.vehicle_name_map.test"} <= task.upstream_task_ids


def test_gold_waits_for_tests_on_two_models(dagbag):
    """As in `dbt build`, a failing relationships test stops the models downstream of it. (A cycle would
    show up as an import error in test_no_import_errors.)"""
    dag = dagbag.get_dag("nhtsa_daily")
    test = next(t for t in dag.tasks if "relationships_recall_components_vehicle_name_key" in t.task_id)
    downstream = {t.task_id for t in test.get_flat_relatives(upstream=False)}
    assert "dbt_build.vehicle_complaint_summary.run" in downstream

