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
  #
  # source_hash, NOT etag. A 61MB object goes up as a multipart upload, and S3
  # gives a multipart object an ETag like "9c51...f8-13" - not an MD5 of the
  # file, and never equal to filemd5(). With etag, every plan showed the
  # package as changed and every apply re-uploaded 61MB whether or not a line
  # of code had moved, which also made "did the code change?" unanswerable
  # from a plan. source_hash is compared against state, not against S3.
  source_hash = filemd5(var.lambda_package_path)
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

  # Every code change publishes an immutable, numbered version. Traffic reaches
  # the function only through the `live` alias below, so rolling back is
  # repointing that alias at an earlier version - seconds, no rebuild, no
  # CloudFront change. Before this, rollback meant rebuilding and re-applying
  # an old tag, which is minutes at best and assumes the old tag still builds.
  #
  # Cost: each version keeps a ~62MB copy of the package against Lambda's 75GB
  # per-region code storage, so roughly 1,200 deploys before it matters. Prune
  # old versions long before then (docs/deployment.md, Rollback).
  publish = true

  memory_size = var.lambda_memory_mb
  # Generous for a request path that answers in ~30ms locally, but cold starts
  # load scikit-learn and a model blob. Lambda bills duration, not the timeout.
  timeout = 30

  environment {
    variables = local.common_env
  }
}

# The only thing CloudFront, the function URL and both permissions point at.
# Terraform moves it to each newly published version on apply; a human moves it
# back with `aws lambda update-alias` to roll back. The next apply will move it
# forward again, which is the intended shape: rollback buys time, and the fix is
# still to revert the offending commit and deploy.
resource "aws_lambda_alias" "api_live" {
  name             = "live"
  function_name    = aws_lambda_function.api.function_name
  function_version = aws_lambda_function.api.version
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
  qualifier          = aws_lambda_alias.api_live.name
  authorization_type = "AWS_IAM"

  cors {
    allow_origins = ["*"]
    allow_methods = ["GET", "POST", "DELETE"]
    # x-id-token and x-amz-content-sha256 are part of the API contract since
    # ADR-005 (CloudFront replaces Authorization; OAC does not sign bodies).
    # Omitting them here would only surface for a browser client on another
    # origin, as a preflight failure with no obvious cause.
    allow_headers = ["content-type", "authorization", "x-agent-key", "x-csrf-token",
    "x-ui-ajax", "x-id-token", "x-amz-content-sha256"]
    max_age = 3600
  }

  # Moving the URL from the unqualified function to the alias gives it a new
  # hostname. Create the new one first so CloudFront always has an origin to
  # point at while its distribution update propagates.
  lifecycle {
    create_before_destroy = true
  }
}

# Only CloudFront may invoke the function URL, and only THIS distribution.
#
# The previous version granted Principal "*" with function_url_auth_type NONE.
# Scoping to the distribution ARN is strictly better: the origin is no longer
# reachable by anyone who did not come through the edge. (An earlier comment
# here claimed the account blocked anonymous invocation outright. It did not -
# the real cause was the missing second statement below. ADR-005 records the
# wrong diagnosis in full.)
#
# Both statements carry the alias qualifier: the URL belongs to `live`, and a
# permission on the unqualified function would not cover it.
resource "aws_lambda_permission" "cloudfront_function_url" {
  statement_id           = "AllowCloudFrontServicePrincipal"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.api.function_name
  qualifier              = aws_lambda_alias.api_live.name
  principal              = "cloudfront.amazonaws.com"
  source_arn             = aws_cloudfront_distribution.api.arn
  function_url_auth_type = "AWS_IAM"
}

# BOTH statements are required, and the second one is the reason this took a
# deployment to find. Granting only lambda:InvokeFunctionUrl produces a 403
# that is indistinguishable from an account-level block: CloudFront reaches the
# origin, Lambda rejects the signed request, and the error body is the generic
# function-URL authorization message. The AWS documentation for restricting a
# Lambda function URL origin lists two add-permission calls; every Terraform
# example found online lists one.
resource "aws_lambda_permission" "cloudfront_invoke_function" {
  statement_id  = "AllowCloudFrontServicePrincipalInvokeFunction"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  qualifier     = aws_lambda_alias.api_live.name
  principal     = "cloudfront.amazonaws.com"
  source_arn    = aws_cloudfront_distribution.api.arn
  # No function_url_auth_type here: Lambda rejects it with
  # "FunctionUrlAuthType is only supported for lambda:InvokeFunctionUrl action".
  # The AWS documentation's second add-permission command omits it too.
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
