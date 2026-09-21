# CloudFront in front of the Lambda Function URL.
#
# WHY THIS EXISTS - forced by reality on the first deployment (2026-09-21).
# This AWS account blocks ANONYMOUS invocation of Lambda function URLs at the
# account level. Measured, not assumed: with authorization_type = NONE and a
# resource policy explicitly allowing Principal "*", every request returned
# 403 AccessDeniedException - on two different functions, one of them created
# by hand through the CLI. The same function returned 200 immediately when the
# URL was switched to AWS_IAM and the request was SigV4-signed. No current
# Lambda API or SDK exposes a switch for that account setting.
#
# So the function URL can no longer be the public entrypoint. CloudFront with
# Origin Access Control signs each request with SigV4 on the way to the origin,
# which turns a blocked anonymous call into an authorized one - without any
# credential reaching the client.
#
# It is also the better architecture, and it stays free:
#   - CloudFront Always-Free is 1 TB out and 10,000,000 requests/month,
#     perpetual, not a 12-month trial. This project's own design ceiling is
#     1M Lambda requests/month, an order of magnitude below it.
#   - AWS Shield Standard is included at the edge at no charge, the first real
#     answer this project has had to the residual risk ADR-002 recorded about
#     having no edge protection at all.
#   - The Lambda function URL stops being reachable by the public entirely.
#
# See docs/adr/005-cloudfront-oac.md for what was rejected and why.

resource "aws_cloudfront_origin_access_control" "lambda" {
  name                              = "${var.project_name}-lambda-oac"
  description                       = "SigV4-signs CloudFront to Lambda Function URL requests"
  origin_access_control_origin_type = "lambda"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# Managed policies by name rather than the hex ids everybody copies from a blog
# post: the ids are stable but unreadable, and a wrong one fails at apply with
# nothing useful to grep for.
data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

# Forwards every header, cookie and query string EXCEPT Host. That exception is
# the load-bearing part: SigV4 signs the Host header, so the origin must see
# its own Lambda hostname rather than the CloudFront one.
data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

resource "aws_cloudfront_distribution" "api" {
  enabled         = true
  comment         = "${var.project_name} - public entrypoint"
  is_ipv6_enabled = true

  origin {
    # The Function URL without scheme or trailing slash: CloudFront wants a
    # bare domain name here.
    domain_name              = replace(replace(aws_lambda_function_url.api.function_url, "https://", ""), "/", "")
    origin_id                = "lambda-function-url"
    origin_access_control_id = aws_cloudfront_origin_access_control.lambda.id

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "lambda-function-url"
    viewer_protocol_policy = "redirect-to-https"

    # Every method the API uses. Telemetry is POST and whitelist removal is
    # DELETE; a distribution that only allowed GET would look fine until the
    # first agent tried to report anything.
    allowed_methods = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods  = ["GET", "HEAD"]

    # Caching is DISABLED deliberately. Every response here is either
    # tenant-scoped or a mitigation decision with a live TTL; serving a cached
    # copy to the wrong tenant would break the isolation guarantee the whole
    # product rests on. CloudFront is here for the signing and the edge, not
    # for the cache.
    cache_policy_id          = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id

    compress = true
  }

  # PriceClass_200 includes the Asian edges. The free tier is measured in bytes
  # and requests, not in price class, so this costs nothing extra and both the
  # operator and the first users are in Vietnam.
  price_class = "PriceClass_200"

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

output "cloudfront_url" {
  description = "THE public entrypoint. Give this to the agent CLI as --backend-url, not the function URL."
  value       = "https://${aws_cloudfront_distribution.api.domain_name}"
}
