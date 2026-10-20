# Infrastructure setup

Terraform creates the S3 data lake, the IAM role Snowflake uses to read it, an AWS budget and the SNS alert topic. Snowflake objects are created by the SQL scripts in `snowflake/`, run in Snowsight.

The Snowflake storage integration and the IAM role each need an identifier from the other, so setup goes back and forth once.

1. Copy `terraform.tfvars.example` to `terraform.tfvars` and fill in the bucket name and alert email. Then run `terraform init` and `terraform apply`. AWS sends a confirmation email for the SNS subscription; click the link.
2. In Snowsight, run `snowflake/01_account_setup.sql` as ACCOUNTADMIN.
3. Put the Terraform outputs `bucket_name` and `snowflake_role_arn` into `snowflake/02_storage_integration.sql` and run it. Copy `STORAGE_AWS_IAM_USER_ARN` and `STORAGE_AWS_EXTERNAL_ID` from the `DESC INTEGRATION` output into `terraform.tfvars`, and run `terraform apply` again. The role now trusts only Snowflake.
4. Put the bucket name into `snowflake/03_bronze_tables.sql` and run it.
5. Run `snowflake/05_cortex_smoke_test.sql` to confirm Cortex embeddings work on the account.
6. Upload the files: from `ingestion/`, run `python nhtsa_ingest.py --bucket <bucket_name>`. It prints the S3 key of each file it uploads.
7. Put that load date into `snowflake/04_load_bronze.sql` and run it. The row counts at the bottom should match the counts in `docs/data_profile.md`, give or take a day's new complaints.

The AWS budget is set to USD 25/month, the AWS share of the USD 40 project cap: the Airflow server below is about USD 18 of it. Snowflake is limited by its resource monitors, and Claude by a spend limit set in the Anthropic console.

Terraform state is local and gitignored for now. Move it to an S3 backend before CI runs Terraform.

## Service users (Snowflake)

Each runs with its own key pair and role. Generate the key with the commands in `snowflake/06_service_user.sql` and paste the public key into the script before running it.

- `06_service_user.sql`: `DBT_PIPELINE`, used by Airflow and dbt.
- `07_app_user.sql`: `APP_READER` with `APP_ROLE`, used by the Streamlit chatbot.
- `08_ask_role.sql`: `ASK_ROLE`, the read-only role for SQL the text-to-SQL assistant writes. It also turns off secondary roles for `APP_READER` and revokes Cortex from `PUBLIC`.
- `09_ci_user.sql`: `CI_DBT`, used by GitHub Actions to build into the `CI_*` schemas.

## Airflow server (EC2)

Airflow runs the same Astro project as on a laptop, on one t4g.medium Spot instance (`ec2.tf`). The instance has no inbound ports and is reached through AWS Systems Manager. If AWS reclaims the Spot capacity, the instance stops and starts again later with its disk intact. On every boot a systemd service, `vsp-airflow`, reads the Snowflake settings from SSM Parameter Store, writes `airflow/.env` and the key file, starts Airflow and unpauses the DAGs.

To create it:

1. Store the Snowflake settings. The key is a SecureString read from the file, so it never passes through Terraform:

   ```
   aws ssm put-parameter --name /vehicle-safety/snowflake_account --type String --value <org>-<account>
   aws ssm put-parameter --name /vehicle-safety/snowflake_user --type String --value DBT_PIPELINE
   aws ssm put-parameter --name /vehicle-safety/snowflake_private_key --type SecureString \
     --value file://$HOME/.snowflake/keys/dbt_pipeline_key.p8
   ```

2. Add `airflow_enabled = true` to `terraform.tfvars` and run `terraform apply`. The first boot takes about four minutes.
3. Stop Airflow on the laptop (`astro dev stop` in `airflow/`), or both copies run the daily DAG.

Day to day, `airflow_server.sh` wraps the SSM commands:

- `./infra/airflow_server.sh ui` opens the Airflow UI at http://localhost:8080. It needs the Session Manager plugin (`brew install --cask session-manager-plugin`).
- `./infra/airflow_server.sh deploy` pulls `main` on the server and restarts Airflow, rebuilding the image if the Dockerfile or requirements changed.
- `./infra/airflow_server.sh status` shows the service, the containers and memory use.

Three things email the alerts topic: failed Airflow tasks; a CloudWatch alarm when the instance fails its status checks; and an EventBridge rule when AWS warns of a Spot interruption, or the instance stops or starts again. A stopped instance sends no metrics and runs no Airflow, so that last rule is the only alert while the server is down.

### Pausing or removing the server

The server runs on a persistent Spot request, which is what lets AWS restart it after reclaiming capacity. The same request means an instance terminated from the EC2 console gets replaced by a new one, which keeps billing.

- To pause, stop the instance (`aws ec2 stop-instances --instance-ids <id>`, or Stop in the console). It stays stopped until you start it, and only the 20 GB disk is billed meanwhile. The stop and the later start each send an alert email.
- To remove it, set `airflow_enabled = false` in `terraform.tfvars` and run `terraform apply`. The AWS provider cancels the Spot request when it destroys a Spot instance. Then check that nothing is left: `aws ec2 describe-spot-instance-requests --filters Name=state,Values=open,active` should list nothing for this project.
- In the console, cancel the request under EC2 → Spot Requests first, and only then terminate the instance.

