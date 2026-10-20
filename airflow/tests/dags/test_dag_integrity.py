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
    assert dag.get_task("anything_changed").downstream_task_ids == {"load_bronze"}
    assert "health_check" in {t.task_id for t in dag.tasks}
    assert any(t.startswith("dbt_build.") for t in dag.get_task("load_bronze").downstream_task_ids)


def test_tests_on_two_models_wait_for_both(dagbag):
    """A relationships test must run after both models it reads, not right after the first one."""
    dag = dagbag.get_dag("nhtsa_daily")
    task = next(t for t in dag.tasks if "relationships_recall_components_vehicle_name_key" in t.task_id)
    assert {"dbt_build.recall_components.test", "dbt_build.vehicle_name_map.test"} <= task.upstream_task_ids
