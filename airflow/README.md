# Airflow

Two DAGs, run locally with the Astro CLI and Docker:

- `nhtsa_daily` (12:00 UTC every day): checks NHTSA for new files and uploads them to S3, reloads the bronze tables, runs `dbt build` through Cosmos (one Airflow task per model and per model's tests), then checks that no table's row count dropped by more than 1% since the previous load. If no file changed, everything after the check is skipped.
- `silver_full_refresh` (Sunday 14:00 UTC): rebuilds the two incremental silver models from scratch, so changes to their SQL reach every row.

Any failed task publishes to the SNS alerts topic, which emails the owner.

dbt runs in its own Python 3.12 virtualenv inside the image (`/usr/local/airflow/dbt_venv`), separate from Airflow's packages.

## Running locally

1. Copy `.env.example` to `.env` and fill in the Snowflake account and the Terraform outputs.
2. Start it: `astro dev start`. The UI is at the URL the command prints.

`docker-compose.override.yml` mounts `../dbt`, `../ingestion`, the Snowflake key from `~/.snowflake/keys` and your AWS credentials from `~/.aws` into the scheduler and DAG processor. The credentials are mounted read-only and never copied into the image.

## Tests

```
docker exec -w /usr/local/airflow <scheduler container> python -m pytest -p no:cacheprovider tests/dags
```

These check that every DAG imports, the schedules are right, catchup is off and every task has the failure alert.
