# Runbook — AI Traffic Shaper (hybrid model)

> Rewritten 2026-08-24. The previous runbook operated a Kubernetes cluster
> that no longer exists; it is in git history.

**Scope warning.** The system is deployed (2026-09-21) but has never served
real traffic. The development loop
and the agent lifecycle below are exercised daily. The production procedures
at the end are written and their tooling validates, but **none has been run
against real AWS** — each one says so. A procedure nobody has run is not a
runbook entry; it is fiction with a heading, and marking the difference is the
whole point of this file.

## Development loop

### Run the tests

```bash
bash scripts/run_tests.sh
```

Builds a venv from `services/backend/requirements.txt` and runs exactly what CI
runs: `ruff`, the full suite, and a coverage gate at 80%. Extra arguments pass
through to pytest, so `bash scripts/run_tests.sh -k cognito` works.

Expected: **177 passed**, coverage ≈ 98%.

### Rebuild the environment from scratch

Do this whenever dependencies change, and never trust a long-lived venv:

```bash
rm -rf ~/aiops-venv-clean && bash scripts/run_tests.sh
```

**Why this matters.** Until Phase 5 the working venv held `fastapi 0.111` while
`requirements.txt` declared `0.134`. Every green run for three phases was
measured against a stack that would never ship. Rebuilding exposed a missing
dependency (`httpx2`) that made the suite uncollectable on any clean machine.

### Dependency audit

```bash
pip-audit -r services/backend/requirements.txt
pip-audit -r services/agent/requirements.txt
```

Both must report *No known vulnerabilities found*. CI enforces this and will
fail on a newly disclosed upstream CVE even in an unrelated pull request —
that is intended. Relaxing it requires a recorded decision in
`docs/security-report.md`.

## Agent lifecycle

### Register an agent

```bash
python -m services.agent.cli register \
  --backend-url https://<function-url> \
  --token <cognito-id-token> \
  --label prod-web-1
```

The ID token currently has to be pasted by hand — there is no Cognito Hosted UI
flow yet. Credentials land in `~/.aiops-agent/config.json`, owner-readable
only. **The API key is shown once and never again**; only its SHA-256 hash is
stored server-side.

```bash
python -m services.agent.cli status
```

### What the agent writes on the host

| Path | Contents |
|---|---|
| `/etc/nginx/conf.d/aiops-agent-deny.conf` | One `deny <ip>;` line per hard block |
| `/etc/nginx/conf.d/aiops-agent-geo.conf` | `geo` map setting `$agent_rate_limited` |
| iptables `INPUT` chain | DROP rules tagged with comment `aiops-agent` |

The customer's own nginx must include those files and define a
`limit_req_zone` keyed on `$agent_rate_limited`. See `config/nginx/` for a
worked example. The agent needs write access to `conf.d` plus permission to
reload nginx; iptables enforcement needs root. An adapter that cannot act
reports itself unavailable and is simply skipped.

### Removing the agent's effects

```bash
rm -f /etc/nginx/conf.d/aiops-agent-*.conf && nginx -s reload
iptables -S INPUT | grep aiops-agent    # then delete the matching rules
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `401 Missing X-Agent-Key` | Header absent | Send `X-Agent-Key: <tenant_id>.<raw_key>` — the tenant prefix is part of the value |
| `401 Invalid agent key` | Wrong key, or agent `status` is not `active` | Re-register; check whether the tenant was suspended (that revokes every key) |
| `403 Tenant is not active` | Tenant suspended, or no `Tenants` row exists | Deliberate: suspension stops agents being served |
| `401 Authentication is not configured` | `cognito_user_pool_id` or `cognito_app_client_id` unset | Set both. Auth fails closed by design rather than silently skipping audience checks |
| `401 Invalid token` on a valid-looking JWT | Access token used instead of an ID token, or wrong app client / pool | Only ID tokens are accepted (`token_use`, `aud` and `iss` are all verified) |
| `403 CSRF token missing` on the UI | Request did not echo the `csrf_token` cookie | Browser flows do this automatically; scripted calls should use the JSON API with a Bearer header instead |
| Dashboard shows "shadow mode" | No production model for that tenant | Expected until enough telemetry accumulates and a nightly retrain passes the gate |
| Decisions returned but nothing enforced | No adapter available | Check nginx `conf.d` write access and, for iptables, root |
| Blocks never lift | The agent process is not running | Nothing external removes an nginx `deny` line — expiry is the agent's own sweep |

### How the nightly retrain runs

One EventBridge rule, one function, two modes. The scheduled call is a
**dispatcher**: it invokes the same function asynchronously once per tenant and
returns in about a second. Each of those is a **worker** that retrains exactly
one tenant. A tenant's failure stays with that tenant, gets two automatic
retries, and shows up in the `retrain-failed` alarm; at most five workers run
at once so the retrain can never starve the API of account-wide concurrency.

A healthy night in the retrain log group looks like:

```
[INFO] Retrain dispatched: tenants=3
[INFO] Retrained and promoted: tenant=acme version=v2026... samples=187 (Approved: block_rate=0.011 ...)
[INFO] Skipping retrain: tenant=new-co samples=12 (need 100)
```

Until 2026-09-21 none of the `[INFO]` lines were ever written: the Lambda
runtime leaves Python logging at WARNING, so a successful night was
indistinguishable from one that never ran. `LOG_LEVEL=INFO` is now set on this
function and the probe only — see `core/log_level.py` for why not the API.

To retrain one tenant by hand, outside the schedule:

```bash
aws lambda invoke --function-name aiops-traffic-shaper-retrain   --payload '{"tenant_id":"<tenant>"}' --cli-binary-format raw-in-base64-out out.json
```

### Retraining refused a model

Look for `Retrain NOT promoted` in the retrain Lambda's logs. The reason is
recorded in full — block rate too high, score spread widened, or too little
validation data. The staged model is kept as evidence and production keeps
serving. This is the gate working, not a failure.

## Free-tier monitoring

```bash
curl -H "Authorization: Bearer <admin-id-token>" https://<function-url>/admin/v1/usage
```

Returns today's request count, estimated GB-seconds, and a `ceiling_warning`
flag. The Control Platform UI shows the same thing with a banner.

**At 80% the system warns; at 100% it refuses telemetry ingest** with `429`
and a `Retry-After` pointing at the next UTC midnight, when the daily counter
resets. Reads keep serving throughout: agents already enforcing a block do not
go open, and this dashboard stays readable.

Two limiters, both checked on every telemetry batch:

| Limiter | Trips at | Refusal says |
|---|---|---|
| Global ceiling | 100% of the day's free-tier share | the platform is paused |
| Per-tenant quota | 25% of that same share, per tenant | *this tenant* is paused; others are unaffected |

So one noisy tenant — or an attacker holding one tenant's agent key — is
stopped at its own quota and can no longer pause ingest for everyone. Both
refusals carry `Retry-After` pointing at the next UTC midnight, and a refused
batch is not counted against the quota that refused it.

To see who is near their quota, read the counters directly (the rows share the
`UsageCounters` table with the global row, keyed `YYYY-MM-DD#tenant#<id>`):

```bash
aws dynamodb scan --table-name UsageCounters   --filter-expression "contains(#d, :m)"   --expression-attribute-names '{"#d":"date"}'   --expression-attribute-values '{":m":{"S":"#tenant#"}}'
```

### Suspend a tenant

```bash
curl -X POST -H "Authorization: Bearer <admin-id-token>" \
  https://<function-url>/admin/v1/tenants/<tenant_id>/suspend
```

Sets tenant status to `suspended` **and revokes every agent API key it issued**.
The response reports how many were revoked. Its agents are refused on their
very next call, and its still-valid dashboard token cannot mint a replacement.
There is no un-suspend endpoint — that is a gap, not a policy.

## Production procedures

| Procedure | Where | State |
|---|---|---|
| Deploy | `docs/deployment.md`, `.github/workflows/deploy.yml` | **exercised** — applied by hand 2026-09-21. The *workflow* has still never run |
| Rollback | `docs/deployment.md` § Rollback | written, never exercised. No Lambda alias, so it is a re-apply from an earlier tag, not a pointer flip |
| Backup | `scripts/backup-tables.sh` | **exercised** — export and restore both performed against the live tables; raw output in `.sdlc/gate-evidence/phase-7-restore-drill.txt` |
| Alerting | `terraform/alarms.tf` | 3 alarms → SNS, subscription created. Confirm the emailed link or it delivers nothing |
| Readiness | `GET /ready` | **exercised** — caught a missing `dynamodb:DescribeTable` grant on the first deployment |

### The public address is CloudFront, not the function URL

```bash
terraform -chdir=terraform output -raw cloudfront_url
```

The Lambda function URL is `AWS_IAM` and refuses anything that did not come
through the distribution. Handing somebody `function_url` gives them a 403 and
a confusing afternoon.

### Checking readiness after a deploy

```bash
curl -s https://<function-url>/ready | jq
```

`200 {"status":"ready"}` means the DynamoDB tables exist and both Cognito
values are set. `503` names which check failed:

- `"dynamodb": false` — tables were never created. They come from
  `create_all_tables()` in application code as well as from Terraform; if
  neither has run against this account, nothing is there.
- `"cognito_config": false` — the pool id or app client id is unset on the
  Lambda. Authentication fails closed, so the deployment serves nobody while
  `/health` still answers 200. Wire `terraform output` back in.

`scripts/smoke-test.sh <url>` runs this plus four other checks unattended.

### A POST that returns 403 with no application log

The request died at the edge. CloudFront signs the origin request but not the
body, so a `POST` without `x-amz-content-sha256` is rejected before Lambda ever
runs — which is why nothing appears in CloudWatch. Add the header:

```bash
BODY='{"logs":[]}'
curl -X POST "$(terraform -chdir=terraform output -raw cloudfront_url)/agent/v1/telemetry"   -H 'content-type: application/json'   -H "x-amz-content-sha256: $(printf '%s' "$BODY" | sha256sum | cut -d' ' -f1)"   -d "$BODY"
```

The agent and the dashboard already do this. See [ADR-005](adr/005-cloudfront-oac.md).

### Taking a backup

```bash
bash scripts/backup-tables.sh export ~/aiops-backups
```

Exports `Tenants`, `Agents` and `Whitelist` — the three tables nothing can
reconstruct. AWS cost is genuinely zero: a Scan consumes read capacity that is
already provisioned. It is **manual**; whatever changed since the last export
is gone, and restore is a merge, not a rewind. The dump contains agent API key
hashes and customer IPs — store it like a password-manager export, never in
this repository.

## Still genuinely missing

- **Un-suspend.** Suspension is one-way. A gap, not a policy.
- **Per-tenant throttling.** The free-tier limiter is global.
- **An alarm on the free-tier ceiling itself.** The three CloudWatch alarms
  watch Lambda errors and retrain health; the usage ceiling is still visible
  only to someone looking at the Control Platform.
- **Local application run.** The mock harness that made the UI viewable
  offline was removed; running the app now needs real AWS.
- **An owner for table creation.** Terraform and `create_all_tables()` both
  create the 7 tables. Whichever runs first wins; this has never been observed.
