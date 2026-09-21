# ADR-005: CloudFront with Origin Access Control replaces the public Lambda Function URL

- **Date:** 2026-09-21
- **Status:** accepted
- **Amends:** [ADR-002](002-tech-stack-hybrid.md), which chose a **public**
  Lambda Function URL as the HTTPS ingress and recorded "no edge rate limiting"
  as an accepted residual risk.

## Context

Phase 7's first real `terraform apply` put the system on AWS. Everything came
up. Nothing could reach it: every request to the Function URL returned

```
HTTP/1.1 403 Forbidden
x-amzn-ErrorType: AccessDeniedException
{"Message":"Forbidden. For troubleshooting Function URL authorization issues, ..."}
```

`terraform validate` was clean, 172 tests were green, and neither could have
seen this. It is the exact failure the Phase 7 gate refuses to wave through.

### The diagnosis was wrong for an hour, and that is the useful part

The first conclusion, drawn from real measurements, was that the AWS account
blocked anonymous invocation of Lambda function URLs:

| Test | Result |
|---|---|
| `authorization_type = NONE`, resource policy allowing `Principal: "*"` | 403 |
| A *different* function, URL + public permission created by hand via CLI | 403 |
| Direct `lambda invoke` | **200** |
| Function URL `AWS_IAM`, request SigV4-signed with IAM user credentials | **200** |

That evidence is real and every row of it reproduces. The inference drawn from
it — "grants made through a resource policy are being blocked" — was wrong, and
it survived because it explained everything observed and predicted the next
failure correctly: CloudFront with OAC, added on that theory, **also** returned
403, apparently confirming it.

The actual cause was a missing permission. The AWS documentation for
restricting a Lambda function URL origin lists **two** `add-permission` calls,
not one:

```
aws lambda add-permission --action "lambda:InvokeFunctionUrl" --principal cloudfront.amazonaws.com ...
aws lambda add-permission --action "lambda:InvokeFunction"    --principal cloudfront.amazonaws.com ...
```

Every Terraform example in circulation has the first. Granting only
`InvokeFunctionUrl` produces a 403 that is *indistinguishable* from an
account-level block: CloudFront reaches the origin, Lambda rejects the signed
request, and the error body is the generic function-URL authorization message
with no mention of which action was denied. Adding the second statement
returned `200 {"status":"healthy"}` immediately.

**No account-level guardrail was ever involved.** The lesson worth keeping is
not about Lambda: it is that a theory which explains every observation and
correctly predicts the next failure can still be wrong, and the thing that
settled it was reading the vendor's own documentation instead of reasoning
further from symptoms.

## Options considered

1. **Keep the public Function URL, now that it is understood.** Viable — the
   original design would likely work once the permissions are right. Rejected
   because it preserves the weakest part of ADR-002: a bare public Lambda URL
   with nothing in front of it, and a recorded residual risk nobody could
   close.

2. **API Gateway HTTP API.** ADR-002 rejected it because its free tier is 12
   months, not forever; after that it is $1.00/million requests, roughly
   $1/month at this project's own design ceiling. That is 100× the exception
   [ADR-004](004-lambda-artifact-via-s3.md) already accepted, and buys nothing
   CloudFront does not.

3. **CloudFront + Origin Access Control (chosen).** CloudFront signs each
   request to the origin with SigV4, so the Function URL is `AWS_IAM` — not
   reachable by anyone who did not come through the edge — while the viewer
   sends an ordinary unauthenticated HTTPS request.

## Decision

CloudFront is the public entrypoint. The Lambda Function URL is `AWS_IAM`, and
its resource policy allows exactly one principal, `cloudfront.amazonaws.com`,
scoped by `SourceArn` to this distribution, for **both** `InvokeFunctionUrl`
and `InvokeFunction`.

Caching is **disabled** (`Managed-CachingDisabled`). Every response is either
tenant-scoped or a mitigation decision with a live TTL; a cached copy served to
the wrong tenant would break the isolation guarantee the product rests on.
CloudFront is here for the signing and the edge, not the cache.

The origin request policy is `Managed-AllViewerExceptHostHeader`. The exception
is load-bearing: SigV4 signs `Host`, so the origin must see its own Lambda
hostname, not the CloudFront one.

## Consequences

**The cost constraint holds.** CloudFront's Always-Free tier is 1 TB out and
10,000,000 requests per month, perpetual rather than a 12-month trial — an
order of magnitude above this project's design ceiling of 1M Lambda
requests/month. Unlike ADR-004, this adds no new entry to the exception list.

**A residual risk from ADR-002 shrinks.** "No edge rate limiting" was accepted
because no Always-Free AWS service closed it. AWS Shield Standard comes with
CloudFront at no charge, so the endpoint now has real DDoS absorption in front
of it for the first time. This is not a full answer — Shield Standard is
network and transport layer, and per-IP application rate limiting still needs
WAF, which is still not free. The gap narrowed; it did not close.

**POST requests carry a new obligation, and it is the real price of this
decision.** OAC signs the request but **not the body**, and Lambda rejects
unsigned payloads, so any client sending a body must supply
`x-amz-content-sha256`, the hex SHA-256 of that body. Measured: without the
header a POST returns 403 at the edge; with it, 401 and a real JSON error from
the application. Three places pay this:

- `services/agent/http_client.py` — one `hashlib` call, hashing the exact
  bytes on the wire rather than a re-serialisation.
- `services/backend/ui/static/interactions.js` — SubtleCrypto for every
  request that carries a body.
- `services/backend/ui/templates/login.html` — a plain `<form method="post">`
  is built and sent by the browser, which cannot add the header at all, so the
  login form now submits through `fetch`. This is the one place where the
  decision changed user-facing behaviour rather than plumbing.

**The `Authorization` header cannot carry a user credential any more.** Found
the first time a real agent registered against the real deployment: the CLI
sent a valid Cognito token as `Authorization: Bearer ...` and the backend
answered `401 Missing credentials`, because it never saw it. OAC signs the
origin request by *replacing* `Authorization` with its own SigV4 signature. The
other OAC setting, `no-override`, passes the viewer's header through but then
does not sign — and an `AWS_IAM` function URL rejects an unsigned request. No
configuration lets both through.

The token now travels in `X-Id-Token`, a header CloudFront leaves alone. The
backend checks `Authorization: Bearer` first, `X-Id-Token` second and the
cookie third; in production the `Authorization` header holds CloudFront's
signature, which does not begin with `Bearer `, so it falls through on its own
and every direct-to-origin caller keeps working unchanged.

This is the second client-visible obligation this decision created, after the
body hash. Both were invisible to 172 green tests and to a passing smoke test,
because the smoke test only ever sent *unauthenticated* requests — and a
request with no credential is the one request this bug cannot affect.

**The origin is no longer publicly reachable.** Anything bypassing CloudFront
gets 403 from AWS itself — a stronger guarantee than the original design had.
`terraform output function_url` is no longer the address to give anybody;
`cloudfront_url` is.

**A distribution takes minutes to deploy.** Applies that touch it are slower
than the rest of the stack, and rolling back an edge change is not instant.
