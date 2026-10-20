# Airflow server: one t4g.medium Spot instance running the Astro project from this repo.
# No inbound ports. The Airflow UI is reached through SSM port forwarding (see infra/README.md).
# Secrets (the Snowflake key) live in SSM Parameter Store and are read at boot, so they never
# appear in Terraform state or user data.

variable "airflow_enabled" {
  description = "Create the Airflow EC2 instance. Off until the AWS account is on the Paid plan."
  type        = bool
  default     = false
}

variable "airflow_az" {
  description = "Availability zone for the Spot instance; pick one with a low t4g.medium Spot price."
  type        = string
  default     = "ap-southeast-2a"
}

variable "repo_url" {
  type    = string
  default = "https://github.com/SipOfLatte/vehicle-safety-pipeline.git"
}

data "aws_subnet" "airflow" {
  availability_zone = var.airflow_az
  default_for_az    = true
}

data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_security_group" "airflow" {
  name        = "vehicle-safety-airflow"
  description = "Airflow server: no inbound access; outbound for SSM, Snowflake, NHTSA, S3 and GitHub"
  vpc_id      = data.aws_subnet.airflow.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

data "aws_iam_policy_document" "ec2_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "airflow" {
  name               = "vehicle-safety-airflow"
  assume_role_policy = data.aws_iam_policy_document.ec2_trust.json
}

resource "aws_iam_role_policy_attachment" "airflow_ssm" {
  role       = aws_iam_role.airflow.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "airflow_bronze" {
  role       = aws_iam_role.airflow.name
  policy_arn = aws_iam_policy.ingestion_write_bronze.arn
}

data "aws_iam_policy_document" "airflow_runtime" {
  statement {
    sid       = "ReadPipelineSecrets"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = ["arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/vehicle-safety/*"]
  }
  statement {
    sid       = "FailureAlerts"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
  }
}

resource "aws_iam_role_policy" "airflow_runtime" {
  name   = "runtime"
  role   = aws_iam_role.airflow.id
  policy = data.aws_iam_policy_document.airflow_runtime.json
}

resource "aws_iam_instance_profile" "airflow" {
  name = "vehicle-safety-airflow"
  role = aws_iam_role.airflow.name
}

resource "aws_instance" "airflow" {
  count = var.airflow_enabled ? 1 : 0

  ami                    = data.aws_ssm_parameter.al2023_arm64.value
  instance_type          = "t4g.medium"
  subnet_id              = data.aws_subnet.airflow.id
  vpc_security_group_ids = [aws_security_group.airflow.id]
  iam_instance_profile   = aws_iam_instance_profile.airflow.name

  # Persistent Spot request that stops (keeping the disk) instead of terminating when AWS reclaims
  # capacity; AWS starts it again when capacity returns. The DAG is safe to re-run.
  instance_market_options {
    market_type = "spot"
    spot_options {
      spot_instance_type             = "persistent"
      instance_interruption_behavior = "stop"
    }
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 20
    encrypted   = true
  }

  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2 # containers reach the instance role through the Docker bridge
  }

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    repo_url         = var.repo_url
    region           = var.region
    bucket           = aws_s3_bucket.lake.bucket
    alerts_topic_arn = aws_sns_topic.alerts.arn
  })
  user_data_replace_on_change = false

  tags = { Name = "vehicle-safety-airflow" }

  lifecycle {
    # A newer AMI shouldn't replace the running server, and user data only runs on first boot:
    # changes to it apply to a new instance, and the running one is updated over SSM.
    ignore_changes = [ami, user_data]
  }
}

resource "aws_cloudwatch_metric_alarm" "airflow_status" {
  count               = var.airflow_enabled ? 1 : 0
  alarm_name          = "vehicle-safety-airflow-status-check"
  alarm_description   = "The Airflow EC2 instance failed its status checks."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed"
  dimensions          = { InstanceId = aws_instance.airflow[0].id }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching" # a stopped Spot instance reports nothing
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

output "airflow_instance_id" {
  value = var.airflow_enabled ? aws_instance.airflow[0].id : null
}

# A stopped Spot instance sends no metrics, so the status alarm stays quiet and Airflow can't send its
# own alerts. EventBridge emails the alerts topic when AWS warns of a Spot interruption, and when the
# server stops or comes back.
resource "aws_cloudwatch_event_rule" "airflow_state" {
  count       = var.airflow_enabled ? 1 : 0
  name        = "vehicle-safety-airflow-state"
  description = "Airflow server: Spot interruption warning, stopped, or running again"
  event_pattern = jsonencode({
    source = ["aws.ec2"]
    "$or" = [
      {
        "detail-type" = ["EC2 Spot Instance Interruption Warning"]
        detail        = { "instance-id" = [aws_instance.airflow[0].id] }
      },
      {
        "detail-type" = ["EC2 Instance State-change Notification"]
        detail        = { "instance-id" = [aws_instance.airflow[0].id], state = ["stopped", "running", "terminated"] }
      },
    ]
  })
}

resource "aws_cloudwatch_event_target" "airflow_state_email" {
  count     = var.airflow_enabled ? 1 : 0
  rule      = aws_cloudwatch_event_rule.airflow_state[0].name
  target_id = "alerts-topic"
  arn       = aws_sns_topic.alerts.arn
  input_transformer {
    input_paths = {
      type  = "$.detail-type"
      state = "$.detail.state"
      time  = "$.time"
    }
    input_template = "\"Airflow server (vehicle-safety-pipeline): <type> <state> at <time> UTC. While it is stopped no daily run happens; a persistent Spot request restarts it when capacity returns.\""
  }
}

data "aws_iam_policy_document" "alerts_topic" {
  statement {
    sid       = "AccountOwner"
    actions   = ["sns:Publish", "sns:Subscribe", "sns:GetTopicAttributes", "sns:SetTopicAttributes", "sns:ListSubscriptionsByTopic"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }
  statement {
    sid       = "EventBridgeAndCloudWatch"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com", "cloudwatch.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts_topic.json
}
