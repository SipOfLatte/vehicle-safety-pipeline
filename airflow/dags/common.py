"""Settings shared by the DAGs: dbt/Cosmos config and the failure alert."""

import os
from pathlib import Path

from cosmos import ExecutionConfig, ProfileConfig, ProjectConfig, RenderConfig
from cosmos.constants import LoadMode

AIRFLOW_HOME = Path(os.environ.get("AIRFLOW_HOME", "/usr/local/airflow"))
DBT_DIR = AIRFLOW_HOME / "dbt"

DBT_PROJECT = ProjectConfig(dbt_project_path=DBT_DIR)
DBT_PROFILE = ProfileConfig(
    profile_name="vehicle_safety",
    target_name="prod",
    profiles_yml_filepath=DBT_DIR / "profiles.yml",
)
DBT_EXECUTION = ExecutionConfig(dbt_executable_path=str(AIRFLOW_HOME / "dbt_venv" / "bin" / "dbt"))


def dbt_render(**kwargs) -> RenderConfig:
    # A test that reads two models (e.g. recall_components -> vehicle_name_map relationships) gets its own
    # task after both. Attached to the first model, it ran before the second was rebuilt and failed on
    # 20 Oct 2026 when new recalls brought vehicle names the map didn't have yet.
    # block_on_detached_tests() then makes the models after them wait, so a failure still stops gold.
    kwargs.setdefault("should_detach_multiple_parents_tests", True)
    return RenderConfig(load_method=LoadMode.DBT_LS, **kwargs)


def block_on_detached_tests(tasks) -> None:
    """Make tasks that follow a detached multi-model test's models wait for that test, as `dbt build` does.

    Cosmos gives a test that reads two models its own task after both (see dbt_render), but nothing
    depends on that task, so a failing relationships test wouldn't stop gold from rebuilding. Here every
    task directly after either model's test task also waits for the detached test, except tasks the test
    itself depends on (which would make a cycle).
    """
    tasks = list(tasks)
    detached = [t for t in tasks if t.task_id.endswith("_test") and not t.task_id.endswith(".test")]
    for test in detached:
        ancestors = {a.task_id for a in test.get_flat_relatives(upstream=True)}
        for parent in list(test.upstream_list):
            for follower in list(parent.downstream_list):
                if follower is not test and follower.task_id not in ancestors and follower not in detached:
                    test >> follower


def notify_failure(context) -> None:
    """Email the failure through the SNS alerts topic."""
    import boto3

    ti = context["task_instance"]
    boto3.client("sns", region_name=os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-2")).publish(
        TopicArn=os.environ["ALERTS_TOPIC_ARN"],
        Subject=f"[vehicle-safety] {ti.dag_id}.{ti.task_id} failed"[:100],
        Message=(
            f"DAG: {ti.dag_id}\nTask: {ti.task_id}\nRun: {context.get('run_id')}\n"
            f"Try: {ti.try_number}\nError: {context.get('exception')}\n"
        ),
    )
