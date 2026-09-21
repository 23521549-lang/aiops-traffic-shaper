# Scheduled self-monitoring probe. See services/backend/probe_handler.py.
#
# Before this, nothing polled the deployment: /health and /ready answered
# whoever asked and nobody asked, and the free-tier ceiling was visible only to
# someone who opened the Control Platform. The retrospective carried both as
# open. Everything here is inside the Always-Free tier:
#   - one EventBridge rule (free) invoking a small Lambda every 5 minutes:
#     ~8,640 invocations/month against 1,000,000, at 256MB for about a second
#   - two custom metrics via the Embedded Metric Format, of 10 free
#   - three more alarms, for six in total, of 10 free
# The probe's /ready call also reaches the API Lambda through CloudFront, adding
# the same ~8,640 invocations there - under 1% of the free allowance.

resource "aws_iam_role" "probe" {
  name               = "${var.project_name}-probe"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "probe_logs" {
  role       = aws_iam_role.probe.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Exactly one read: the global usage row. The probe learns about the service
# through its public front door, not through the tables behind it, so this is
# the only data access it has.
data "aws_iam_policy_document" "probe_data" {
  statement {
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.usage_counters.arn]
  }
}

resource "aws_iam_role_policy" "probe_data" {
  name   = "${var.project_name}-probe-data"
  role   = aws_iam_role.probe.id
  policy = data.aws_iam_policy_document.probe_data.json
}

resource "aws_lambda_function" "probe" {
  function_name = "${var.project_name}-probe"
  role          = aws_iam_role.probe.arn

  # Same package as the API and the retrain function: one artifact, three
  # entry points. The probe imports nothing from ml/, so it never loads
  # scikit-learn and its cold start is a fraction of the API's.
  s3_bucket        = aws_s3_bucket.artifacts.id
  s3_key           = aws_s3_object.package.key
  source_code_hash = filebase64sha256(var.lambda_package_path)

  handler     = "services.backend.probe_handler.handler"
  runtime     = "python3.12"
  memory_size = 256
  timeout     = 30

  environment {
    variables = merge(local.common_env, {
      # Through the edge, deliberately - see the handler's docstring.
      PROBE_TARGET_URL = "https://${aws_cloudfront_distribution.api.domain_name}"
    })
  }
}

# A log line every five minutes forever is exactly how a free-tier project
# starts paying for CloudWatch. 14 days, the same as the other two functions.
resource "aws_cloudwatch_log_group" "probe" {
  name              = "/aws/lambda/${aws_lambda_function.probe.function_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_event_rule" "probe" {
  name                = "${var.project_name}-probe"
  description         = "Poll /ready through CloudFront and report free-tier usage."
  schedule_expression = "rate(5 minutes)"
}

resource "aws_cloudwatch_event_target" "probe" {
  rule = aws_cloudwatch_event_rule.probe.name
  arn  = aws_lambda_function.probe.arn
}

resource "aws_lambda_permission" "eventbridge_probe" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.probe.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.probe.arn
}

# --- Alarms -----------------------------------------------------------------
# Kept with the probe rather than in alarms.tf: they are meaningless without it,
# and deleting the probe should delete them in the same change.

# The service is not ready, seen from outside. Two consecutive failed probes
# (ten minutes) rather than one, so a single cold start or edge blip does not
# page anybody. MISSING data counts as breaching: if the probe itself stops
# running, silence is the worst possible signal to treat as healthy.
resource "aws_cloudwatch_metric_alarm" "ready_probe" {
  alarm_name          = "${var.project_name}-not-ready"
  namespace           = "AiopsTrafficShaper"
  metric_name         = "ReadyProbeSuccess"
  dimensions          = { Service = "aiops-traffic-shaper" }
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_description   = "GET /ready through CloudFront has failed for 10 minutes. Check the probe's log line for which dependency, then the API log group."
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

# The free-tier ceiling, finally somewhere other than a dashboard page. 0.8 is
# the same threshold the Control Platform banner uses; at 1.0 ingest is refused.
resource "aws_cloudwatch_metric_alarm" "free_tier_ceiling" {
  alarm_name          = "${var.project_name}-free-tier-80pct"
  namespace           = "AiopsTrafficShaper"
  metric_name         = "DailyUsageRatio"
  dimensions          = { Service = "aiops-traffic-shaper" }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0.8
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_description   = "Today's requests passed 80% of the free-tier daily share. At 100% telemetry ingest is refused platform-wide; per-tenant usage is in UsageCounters."
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# The probe is broken, as distinct from the service being down. The handler
# never raises for a service failure, so an error here means the probe code or
# its permissions - and it must not be mistaken for an outage.
resource "aws_cloudwatch_metric_alarm" "probe_errors" {
  alarm_name          = "${var.project_name}-probe-broken"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.probe.function_name }
  statistic           = "Sum"
  period              = 900
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_description   = "The probe itself is failing - its code or its permissions, not the service. ReadyProbeSuccess will read as missing (and alarm) while this lasts."
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
