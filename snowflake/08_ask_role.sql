-- Read-only role for the text-to-SQL assistant (the app's Ask page). Run as ACCOUNTADMIN.
--
-- SQL written by the model runs as ASK_ROLE. The role can use APP_WH and read the GOLD schema,
-- and nothing else: no SILVER or BRONZE, no AI schema (so it can't touch the usage log), no Cortex.
-- SELECT on the individual GOLD tables is granted by dbt (the `grants` config in dbt_project.yml),
-- because dbt recreates those tables on every run and a grant made here would be lost.
-- Statement timeout and row limit are set per session by ai/text_to_sql/executor.py.

USE ROLE ACCOUNTADMIN;

CREATE ROLE IF NOT EXISTS ASK_ROLE;
GRANT USAGE ON WAREHOUSE APP_WH TO ROLE ASK_ROLE;
GRANT USAGE ON DATABASE VEHICLE_SAFETY TO ROLE ASK_ROLE;
GRANT USAGE ON SCHEMA VEHICLE_SAFETY.GOLD TO ROLE ASK_ROLE;

-- The app's service user switches to ASK_ROLE only for the Ask page's queries.
GRANT ROLE ASK_ROLE TO USER APP_READER;

-- Snowflake's default secondary roles are ALL, which would give an ASK_ROLE session APP_ROLE's
-- privileges too (usage log, Cortex). Sessions get only their primary role. The executor also runs
-- USE SECONDARY ROLES NONE, in case this setting is ever changed back.
ALTER USER APP_READER SET DEFAULT_SECONDARY_ROLES = ();

-- Snowflake grants Cortex to PUBLIC by default, and every role inherits PUBLIC, so ASK_ROLE could call
-- paid AI functions from a SELECT. PIPELINE_ROLE and APP_ROLE have their own grants (01 and 07 scripts).
REVOKE DATABASE ROLE SNOWFLAKE.CORTEX_USER FROM ROLE PUBLIC;

-- Backstop in case a session doesn't set its own timeout: no query on APP_WH runs past 60 seconds.
-- (The chatbot's searches on APP_WH take about a second.)
ALTER WAREHOUSE APP_WH SET STATEMENT_TIMEOUT_IN_SECONDS = 60;

-- A test row written while checking ASK_ROLE's access on 20 Oct 2026, before secondary roles were off.
DELETE FROM VEHICLE_SAFETY.AI.LLM_USAGE WHERE PURPOSE = 'x' AND COST_USD IS NULL;

-- Check: expect USAGE on APP_WH, VEHICLE_SAFETY and VEHICLE_SAFETY.GOLD only (table grants appear after dbt runs).
SHOW GRANTS TO ROLE ASK_ROLE;
