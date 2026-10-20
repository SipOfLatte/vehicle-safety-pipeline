variable "region" {
  type    = string
  default = "ap-southeast-2"
}

variable "bucket_name" {
  description = "Globally unique S3 bucket name for the data lake."
  type        = string
}

variable "alert_email" {
  description = "Where budget alerts and pipeline failure alerts go."
  type        = string
}

variable "aws_monthly_budget_usd" {
  description = "AWS share of the USD 40/month project cap: EC2 Spot about USD 18 (instance, disk, public IPv4) plus S3. Snowflake and Claude are capped separately."
  type        = number
  default     = 25
}

variable "bronze_retention_days" {
  description = "Delete bronze snapshots older than this. The ingestion job re-uploads a dataset if its latest copy has expired."
  type        = number
  default     = 60
}

# Filled in after creating the Snowflake storage integration (see infra/README.md, step 3).
variable "snowflake_iam_user_arn" {
  description = "STORAGE_AWS_IAM_USER_ARN from DESC INTEGRATION in Snowflake. Empty on the first apply."
  type        = string
  default     = ""
}

variable "snowflake_external_id" {
  description = "STORAGE_AWS_EXTERNAL_ID from DESC INTEGRATION in Snowflake. Empty on the first apply."
  type        = string
  default     = ""
}
