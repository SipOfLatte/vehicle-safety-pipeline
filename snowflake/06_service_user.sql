-- Service user for dbt and Airflow. It signs in with a key pair, so there's no password or MFA prompt.
-- Generate the key on your machine (the private key never leaves it):
--   openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out ~/.snowflake/keys/dbt_pipeline_key.p8 -nocrypt
--   openssl rsa -in ~/.snowflake/keys/dbt_pipeline_key.p8 -pubout
-- Paste the public key body (without the BEGIN/END lines) in place of <PUBLIC_KEY>.

USE ROLE ACCOUNTADMIN;

CREATE USER IF NOT EXISTS DBT_PIPELINE
  TYPE = SERVICE
  DEFAULT_ROLE = PIPELINE_ROLE
  DEFAULT_WAREHOUSE = PIPELINE_WH
  RSA_PUBLIC_KEY = '<PUBLIC_KEY>'
  COMMENT = 'dbt and Airflow';

GRANT ROLE PIPELINE_ROLE TO USER DBT_PIPELINE;

-- The account identifier dbt needs (SNOWFLAKE_ACCOUNT).
SELECT CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME() AS account_identifier;
