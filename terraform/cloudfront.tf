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

# Not Managed-CachingOptimized, which pins default_ttl to 86400 and would
# hold a stylesheet at the edge for a day after a rollback. This one obeys
# the origin: `default_ttl = 0` means "no Cache-Control header, do not cache",
# and `max_ttl = 300` caps what any header can ask for at the same five
# minutes static_files.py sends. The bound survives someone editing that
# constant upward without thinking about rollback.
resource "aws_cloudfront_cache_policy" "static" {
  name    = "${var.project_name}-static-assets"
  comment = "Obeys the origin's Cache-Control, capped at five minutes (ADR-007)"

  min_ttl     = 0
  default_ttl = 0
  max_ttl     = 300

  parameters_in_cache_key_and_forwarded_to_origin {
    # The cache key is the path and nothing else. These files are identical
    # for every viewer, and putting the cookie in the key would give each
    # session its own copy of a shared file - the cost of no cache, with the
    # rollback hazard of one.
    cookies_config { cookie_behavior = "none" }
    headers_config { header_behavior = "none" }
    query_strings_config { query_string_behavior = "none" }

    enable_accept_encoding_gzip   = true
    enable_accept_encoding_brotli = true
  }
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

  # The one path that is identical for every viewer.
  #
  # Everything else here is tenant-scoped, which is why the default behavior
  # disables caching outright. The stylesheets and scripts are not: they are
  # the same bytes for a signed-out stranger on the landing page and for a
  # publisher at 3am, they carry no cookie and no tenant id, and there are
  # seven of them. Today every console page load spends five Lambda
  # invocations fetching files that have not changed since the deploy.
  #
  # Cached for exactly as long as the origin says, and no longer. ADR-007
  # chose `Cache-Control: public, max-age=300` in static_files.py for a
  # specific reason: a cached asset does NOT roll back when the Lambda alias
  # is repointed, so five minutes is the bound on how long a rolled-back
  # deployment can keep serving the previous stylesheet. Setting a longer TTL
  # here would override that and make an invalidation the only way back,
  # which is the one step in this project the alias cannot undo.
  ordered_cache_behavior {
    path_pattern           = "/ui/static/*"
    target_origin_id       = "lambda-function-url"
    viewer_protocol_policy = "redirect-to-https"

    # Read-only by construction. A POST to a stylesheet is not a request this
    # product has, and allowing it would put an uncacheable method on a
    # cached behavior.
    allowed_methods = ["GET", "HEAD"]
    cached_methods  = ["GET", "HEAD"]

    cache_policy_id          = aws_cloudfront_cache_policy.static.id
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
