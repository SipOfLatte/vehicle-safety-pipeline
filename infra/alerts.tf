resource "aws_budgets_budget" "monthly" {
  name         = "vehicle-safety-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.aws_monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}

# Pipeline failure alerts (Airflow callback, EC2 status-check alarm) publish here.
# AWS emails a confirmation link after the first apply; the subscription is inactive until it's clicked.
resource "aws_sns_topic" "alerts" {
  name = "vehicle-safety-alerts"
}

resource "aws_sns_topic_subscription" "alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
