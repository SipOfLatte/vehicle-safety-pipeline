-- Run as ACCOUNTADMIN after the first `terraform apply` (infra/README.md, step 3).
-- Replace <SNOWFLAKE_ROLE_ARN> with the Terraform output snowflake_role_arn.

USE ROLE ACCOUNTADMIN;

CREATE STORAGE INTEGRATION IF NOT EXISTS NHTSA_S3
  TYPE = EXTERNAL_STAGE
  STORAGE_PROVIDER = 'S3'
  ENABLED = TRUE
  STORAGE_AWS_ROLE_ARN = '<SNOWFLAKE_ROLE_ARN>'
  STORAGE_ALLOWED_LOCATIONS = ('s3://vehicle-safety-lake-haosun/bronze/');

GRANT USAGE ON INTEGRATION NHTSA_S3 TO ROLE PIPELINE_ROLE;

-- Copy STORAGE_AWS_IAM_USER_ARN and STORAGE_AWS_EXTERNAL_ID from this output into
-- infra/terraform.tfvars, then run `terraform apply` again.
DESC INTEGRATION NHTSA_S3;
