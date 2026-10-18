output "bucket_name" {
  value = aws_s3_bucket.lake.bucket
}

output "snowflake_role_arn" {
  description = "Paste into STORAGE_AWS_ROLE_ARN in snowflake/02_storage_integration.sql."
  value       = aws_iam_role.snowflake_s3.arn
}

output "alerts_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "ingestion_policy_arn" {
  value = aws_iam_policy.ingestion_write_bronze.arn
}
