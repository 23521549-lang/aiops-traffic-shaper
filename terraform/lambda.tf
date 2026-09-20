# Two functions, not one. They share a deployment package but no memory: the
# API Lambda caches a loaded model per tenant across warm invocations, and the
# retrain Lambda cannot invalidate that cache. A promoted model reaches live
# traffic when warm containers recycle — documented behaviour, not a defect.

locals {
  # Both functions need to find the Cognito pool the tokens come from. These
  # names must match services/backend/core/config.py::Settings exactly, which
  # reads them as lowercase env vars via pydantic-settings.
  common_env = {
    cognito_user_pool_id  = aws_cognito_user_pool.main.id
    cognito_region        = var.region
    cognito_app_client_id = aws_cognito_user_pool_client.backend.id
  }
}

# The deployment artifact goes through S3 because it has to: fully trimmed the
# package is ~62MB zipped and Lambda's direct-upload ceiling is 50MB. Measured,
# not assumed — see docs/adr/004-lambda-artifact-via-s3.md for the cost this
# adds and why it is the only option.
#
# A bucket of its own, not the Terraform state bucket: the deploy role needs
# write access to artifacts, and it must not gain write access to state.
resource "aws_s3_bucket" "artifacts" {
  bucket        = "${var.project_name}-${var.environment}-lambda-artifacts"
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    id     = "expire-old-artifacts"
    status = "Enabled"
    filter {}
    # Keeping every past build would grow storage cost without bound. 30 days
    # is long enough to roll back to any recent release and short enough that
    # the bill stays in fractions of a cent.
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

resource "aws_s3_object" "package" {
  bucket = aws_s3_bucket.artifacts.id
  key    = "backend.zip"
  source = var.lambda_package_path
  # Uploading through Terraform rather than a separate CI step keeps the bucket,
  # the object and both functions in one apply - no chicken-and-egg where the
  # Lambda needs an object that does not exist yet.
  etag = filemd5(var.lambda_package_path)
}

resource "aws_lambda_function" "api" {
  function_name = "${var.project_name}-api"
  role          = aws_iam_role.api.arn

  s3_bucket        = aws_s3_bucket.artifacts.id
  s3_key           = aws_s3_object.package.key
  source_code_hash = filebase64sha256(var.lambda_package_path)

  # Mangum wraps the FastAPI app; see services/backend/main.py.
  handler = "services.backend.main.handler"
  runtime = "python3.12"

  memory_size = var.lambda_memory_mb
  # Generous for a request path that answers in ~30ms locally, but cold starts
  # load scikit-learn and a model blob. Lambda bills duration, not the timeout.
  timeout = 30

  environment {
    variables = local.common_env
  }
}

resource "aws_lambda_function" "retrain" {
  function_name = "${var.project_name}-retrain"
  role          = aws_iam_role.retrain.arn

  s3_bucket        = aws_s3_bucket.artifacts.id
  s3_key           = aws_s3_object.package.key
  source_code_hash = filebase64sha256(var.lambda_package_path)

  handler = "services.backend.retrain_handler.handler"
  runtime = "python3.12"

  memory_size = var.lambda_memory_mb
  # The retrain loop walks every tenant serially and logs a warning past 600s.
  # 900s is Lambda's ceiling; crossing it means moving to per-tenant fan-out,
  # which retrain_handler already says in its own docstring.
  timeout = 900

  environment {
    variables = local.common_env
  }
}

# HTTPS ingress without API Gateway, which is 12-month-free only. The Function
# URL rides on Lambda's own always-free quota.
#
# authorization_type NONE is deliberate and load-bearing: the agent
# authenticates with a hashed API key and humans with a Cognito ID token, both
# verified inside the application. AWS_IAM here would lock out every real
# client. The consequence — no edge rate limiting — is ADR-002's recorded
# residual risk, partially mitigated by the free-tier throttle in core/usage.py.
resource "aws_lambda_function_url" "api" {
  function_name      = aws_lambda_function.api.function_name
  authorization_type = "NONE"

  cors {
    allow_origins = ["*"]
    allow_methods = ["GET", "POST", "DELETE"]
    allow_headers = ["content-type", "authorization", "x-agent-key", "x-csrf-token", "x-ui-ajax"]
    max_age       = 3600
  }
}

# WITHOUT THIS, EVERY REQUEST IS 403. Found on the first real deployment
# (2026-09-21), which is precisely the class of defect `terraform validate` and
# a green test suite cannot see.
#
# `authorization_type = NONE` on the Function URL above only says "do not
# require SigV4". It does not grant anybody permission to invoke. Lambda still
# evaluates the function's resource-based policy, which starts empty, so an
# anonymous caller is denied. The AWS Console adds this statement silently when
# you create a Function URL through the UI; Terraform does not, and the failure
# it produces - a JSON "Forbidden" from the Lambda service itself, never
# reaching the application - looks nothing like a missing permission.
resource "aws_lambda_permission" "public_function_url" {
  statement_id           = "AllowPublicFunctionUrlInvoke"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.api.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_cloudwatch_event_rule" "nightly_retrain" {
  name                = "${var.project_name}-nightly-retrain"
  description         = "Daily per-tenant retrain; promotion still goes through the validation gate."
  schedule_expression = var.retrain_schedule
}

resource "aws_cloudwatch_event_target" "retrain" {
  rule = aws_cloudwatch_event_rule.nightly_retrain.name
  arn  = aws_lambda_function.retrain.arn
}

resource "aws_lambda_permission" "eventbridge_retrain" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.retrain.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.nightly_retrain.arn
}

# Log retention is not a nicety here: CloudWatch's always-free allowance is 5GB
# ingest and 5GB storage, and "never expire" is the default. An unbounded log
# group is the most common way a free-tier serverless project starts costing
# money.
resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${aws_lambda_function.api.function_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "retrain" {
  name              = "/aws/lambda/${aws_lambda_function.retrain.function_name}"
  retention_in_days = 14
}
