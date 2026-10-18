# Infrastructure setup (week 1)

Terraform creates the S3 data lake, the IAM role Snowflake uses to read it, an AWS budget and the SNS alert topic. Snowflake objects are created by the SQL scripts in `snowflake/`, run in Snowsight.

The Snowflake storage integration and the IAM role each need an identifier from the other, so setup goes back and forth once.

1. Copy `terraform.tfvars.example` to `terraform.tfvars` and fill in the bucket name and alert email. Then run `terraform init` and `terraform apply`. AWS sends a confirmation email for the SNS subscription; click the link.
2. In Snowsight, run `snowflake/01_account_setup.sql` as ACCOUNTADMIN.
3. Put the Terraform outputs `bucket_name` and `snowflake_role_arn` into `snowflake/02_storage_integration.sql` and run it. Copy `STORAGE_AWS_IAM_USER_ARN` and `STORAGE_AWS_EXTERNAL_ID` from the `DESC INTEGRATION` output into `terraform.tfvars`, and run `terraform apply` again. The role now trusts only Snowflake.
4. Put the bucket name into `snowflake/03_bronze_tables.sql` and run it.
5. Run `snowflake/05_cortex_smoke_test.sql` to confirm Cortex embeddings work on the account.
6. Upload the files: from `ingestion/`, run `python nhtsa_ingest.py --bucket <bucket_name>`. It prints the S3 key of each file it uploads.
7. Put that load date into `snowflake/04_load_bronze.sql` and run it. The row counts at the bottom should match the counts in `docs/data_profile.md`, give or take a day's new complaints.

The AWS budget is set to USD 15/month, the AWS share of the USD 40 project cap. Snowflake is limited by its resource monitors, and Claude by a spend limit set in the Anthropic console.

Terraform state is local and gitignored for now. Move it to an S3 backend before CI runs Terraform.
