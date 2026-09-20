# Alerting, added in Phase 7 because the gate refused to call the observability
# floor "in place" without it — /health that nobody polls and logs that nobody
# reads are instrumentation, not observability.
#
# Cost: CloudWatch's Always-Free tier includes 10 alarms, and SNS's includes
# 1,000 email notifications a month. Three alarms and a handful of emails stay
# inside both, so unlike the backup question (ADR-004's neighbour, unresolved),
# this gap could be closed without touching the cost constraint.

variable "alert_email" {
  description = <<-EOT
    Where alarms go. Leave empty to create the alarms without a subscription —
    they will still fire and still be visible in the console, they just will not
    reach anybody. Set it before this matters.
  EOT
  type        = string
  default     = ""
}

resource "aws_sns_topic" "alerts" {
  name = "${var.project_name}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
  # AWS sends a confirmation link; the subscription is inactive until someone
  # clicks it. An unconfirmed subscription looks configured and delivers
  # nothing, so check it after the first apply.
}

# 1. The request path is failing. Five errors in five minutes is past noise and
#    short of a storm — tune it once real traffic exists rather than guessing
#    harder now.
resource "aws_cloudwatch_metric_alarm" "api_errors" {
  alarm_name          = "${var.project_name}-api-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.api.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_description   = "API Lambda is returning errors. Check the log group first."
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# 2. The nightly retrain failed. Silent failure here is the dangerous kind: the
#    system keeps serving yesterday's model indefinitely and looks healthy while
#    doing it.
resource "aws_cloudwatch_metric_alarm" "retrain_errors" {
  alarm_name          = "${var.project_name}-retrain-failed"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.retrain.function_name }
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_description   = "Nightly retrain errored. Models are frozen until it runs clean."
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# 3. The retrain is approaching Lambda's 15-minute ceiling. retrain_handler
#    already logs a warning past 600s; this makes it reach someone. Crossing
#    900s means the serial per-tenant loop has outgrown itself and needs
#    fan-out, which is a design change, not an incident.
resource "aws_cloudwatch_metric_alarm" "retrain_duration" {
  alarm_name          = "${var.project_name}-retrain-slow"
  namespace           = "AWS/Lambda"
  metric_name         = "Duration"
  dimensions          = { FunctionName = aws_lambda_function.retrain.function_name }
  statistic           = "Maximum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = 600000 # ms
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_description   = "Retrain run exceeded 10 minutes of Lambda's 15-minute limit - time for per-tenant fan-out."
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
