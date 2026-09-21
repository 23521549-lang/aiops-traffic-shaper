# ADR-005: CloudFront with Origin Access Control replaces the public Lambda Function URL

- **Date:** 2026-09-21
- **Status:** accepted
- **Amends:** [ADR-002](002-tech-stack-hybrid.md), which chose a **public**
  Lambda Function URL as the HTTPS ingress and recorded "no edge rate limiting"
  as an accepted residual risk.

## Context

Phase 7's first real `terraform apply` put the system on AWS for the first
time. Everything came up. Nothing could reach it.

Every request to the Function URL returned:

```
HTTP/1.1 403 Forbidden
x-amzn-ErrorType: AccessDeniedException
{"Message":"Forbidden. For troubleshooting Function URL authorization issues, ..."}
```

What was measured, in order, before anything was changed:

| Test | Result |
|---|---|
| `authorization_type = NONE`, resource policy allowing `Principal: "*"` | **403** |
| Same, after adding a second explicit `lambda:InvokeFunctionUrl` allow | **403** |
| A *different* function, URL + public permission created by hand via CLI | **403** |
| Direct `lambda invoke` of the same function | **200**, `{"status":"healthy"}` |
| Function URL switched to `AWS_IAM`, request SigV4-signed | **200**, `{"status":"healthy"}` |

So: the application, Mangum, FastAPI, the model loading and the whole request
path work on real AWS. The Function URL edge works. **Anonymous access is
blocked at the account level**, independent of any resource policy, and
independent of which function is asked.

The account is not part of an AWS Organization, so there is no SCP or RCP to
inspect. No operation for this setting exists in the current Lambda service
model — checked against `boto3` 1.43.98, whose Lambda client exposes 88
operations and none of them named `*PublicAccessBlock*`. `aws lambda
get-account-settings` returns usage and limits only.

The practical position: the public Function URL cannot be the entrypoint in
this account, and nothing in the deployment toolchain can change that.

## Options considered

1. **Turn the account setting off through the console.** Possibly a single
   click, if it is exposed there at all. Rejected as the *primary* answer for
   two reasons: it cannot be expressed in Terraform, so the deployment would
   depend on a manual step nobody records; and it preserves the weakest part of
   ADR-002 — a bare public Lambda URL with no edge in front of it.

2. **API Gateway HTTP API.** The obvious ingress, and ADR-002 rejected it
   because its free tier is 12 months, not forever. After that it is
   $1.00/million requests — at this project's own design ceiling of 1M
   requests/month, about $1/month. That is 100× the exception ADR-004 already
   accepted, and buys nothing CloudFront does not.

3. **CloudFront + Origin Access Control (chosen).** CloudFront signs each
   request to the origin with SigV4, so the Function URL can be `AWS_IAM` —
   the configuration that was measured working — while the viewer still sends
   an ordinary unauthenticated HTTPS request. No credential reaches any client.

## Decision

CloudFront is the public entrypoint. The Lambda Function URL becomes
`AWS_IAM`, and its resource policy allows exactly one principal:
`cloudfront.amazonaws.com`, scoped by `SourceArn` to this distribution.

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
order of magnitude above this project's own design ceiling of 1M Lambda
requests/month. Unlike [ADR-004](004-lambda-artifact-via-s3.md), this exception
list gains no new entry.

**A residual risk from ADR-002 shrinks.** "No edge rate limiting" was accepted
because no Always-Free AWS service closed it. AWS Shield Standard is included
with CloudFront at no charge, so the endpoint now has real DDoS absorption in
front of it for the first time. This is not a full answer: Shield Standard is
network and transport layer, and per-IP application rate limiting still needs
WAF, which is still not free. The gap narrowed; it did not close.

**POST requests carry a new obligation.** CloudFront OAC signs the request but
**not the body**, and Lambda rejects unsigned payloads, so a client sending
POST or PUT must supply `x-amz-content-sha256` — the hex SHA-256 of its own
body. For the agent this is one line of `hashlib`. For the browser UI it means
the login form submits through `fetch` with a hash computed by SubtleCrypto,
rather than as a plain HTML form post. That is a real cost of this decision and
it is paid in client code, which is why it is recorded here rather than left to
be rediscovered.

**The origin is no longer publicly reachable.** Anything that bypasses
CloudFront now gets 403 from AWS itself, which is a stronger guarantee than the
previous design had. It also means the Function URL in `terraform output
function_url` is no longer the address to give anybody; `cloudfront_url` is.

**A distribution takes minutes to deploy.** Applies that touch it are slower
than the rest of the stack, and a rollback of an edge change is not instant.
