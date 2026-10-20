FROM astrocrpublic.azurecr.io/runtime:3.3-9

# dbt gets its own Python 3.12 virtualenv so its dependencies can't clash with Airflow's.
# Cosmos calls it through ExecutionConfig(dbt_executable_path=...).
COPY dbt-requirements.txt /tmp/dbt-requirements.txt
RUN pip install --no-cache-dir uv \
    && uv venv --python 3.12 /usr/local/airflow/dbt_venv \
    && uv pip install --no-cache --python /usr/local/airflow/dbt_venv/bin/python -r /tmp/dbt-requirements.txt
