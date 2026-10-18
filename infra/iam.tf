# Role that Snowflake's storage integration assumes to read bronze files.
# Two-step setup: the first apply trusts this AWS account only (a placeholder),
# then the trust policy is updated with Snowflake's IAM user and external ID.
locals {
  snowflake_configured = var.snowflake_iam_user_arn != "" && var.snowflake_external_id != ""
}

data "aws_iam_policy_document" "snowflake_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = [local.snowflake_configured ? var.snowflake_iam_user_arn : data.aws_caller_identity.current.account_id]
    }
    dynamic "condition" {
      for_each = local.snowflake_configured ? [1] : []
      content {
        test     = "StringEquals"
        variable = "sts:ExternalId"
        values   = [var.snowflake_external_id]
      }
    }
  }
}

resource "aws_iam_role" "snowflake_s3" {
  name               = "vehicle-safety-snowflake-s3"
  assume_role_policy = data.aws_iam_policy_document.snowflake_trust.json
}

data "aws_iam_policy_document" "snowflake_read_bronze" {
  statement {
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${aws_s3_bucket.lake.arn}/bronze/*"]
  }
  statement {
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.lake.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["bronze/*"]
    }
  }
}

resource "aws_iam_role_policy" "snowflake_read_bronze" {
  name   = "read-bronze"
  role   = aws_iam_role.snowflake_s3.id
  policy = data.aws_iam_policy_document.snowflake_read_bronze.json
}

# Policy for whatever runs ingestion. Attached to the EC2 instance role in week 6;
# until then ingestion runs locally with your own AWS profile.
data "aws_iam_policy_document" "ingestion_write_bronze" {
  statement {
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.lake.arn}/bronze/*"]
  }
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.lake.arn]
  }
}

resource "aws_iam_policy" "ingestion_write_bronze" {
  name   = "vehicle-safety-ingestion-write-bronze"
  policy = data.aws_iam_policy_document.ingestion_write_bronze.json
}
