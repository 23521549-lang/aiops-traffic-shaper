# Deployment

**Status: deployed and verified, 2026-09-21.** Account `375916766707`,
`ap-southeast-1`. The post-deploy smoke test passes 5/5 and a backup restore
has been performed against the live tables.

The public address is **CloudFront**, not the Lambda function URL:

```bash
terraform -chdir=terraform output -raw cloudfront_url
```

The function URL is `AWS_IAM` and refuses anything that did not come through
the distribution, so handing somebody `function_url` gives them a 403.

**Every request with a body must carry `x-amz-content-sha256`**, the hex
SHA-256 of that body. CloudFront's origin access control signs the request but
not the body, and Lambda rejects unsigned payloads — so a POST without it dies
at the edge with a 403 and no CloudWatch entry, because the function never ran.
See [ADR-005](adr/005-cloudfront-oac.md).

What deploying proved, and what it did not: the infrastructure stands up, the
application answers, authentication fails closed, the security headers survive,
and a deleted row can be restored. No tenant has registered, no telemetry has
been scored in production, and the nightly retrain has never fired on live data.

## What gets created

| Resource | Why this and not the obvious alternative |
|---|---|
| 2 × Lambda (`-api`, `-retrain`) | Separate functions: they share a package but no memory, which is why a promoted model only reaches traffic when warm containers recycle |
| Lambda Function URL | API Gateway is 12-month free only; the Function URL rides on Lambda's own always-free quota |
| 7 × DynamoDB table, provisioned | 14 RCU / 20 WCU of the account-wide 25/25 Always-Free pool. Per-table split in `terraform/dynamodb.tf` |
| EventBridge rule → retrain | 18:00 UTC daily. No charge for a rule invoking Lambda |
| Cognito user pool + app client + `admin` group | `custom:tenant_id` is immutable and not user-writable — the entire tenant isolation story rests on that |
| 2 × CloudWatch log group, 14-day retention | "Never expire" is the default and the commonest way a free-tier project starts costing money |
| S3 bucket for the Lambda artifact | Forced: the package is 61MB zipped and Lambda's direct-upload ceiling is 50MB — see [ADR-004](adr/004-lambda-artifact-via-s3.md) |
| 3 × CloudWatch alarm + SNS topic | Errors, retrain failure, retrain approaching its timeout. Inside Always-Free (10 alarms, 1,000 emails/month) |
| No load balancer health check | There is nothing in front of the Function URL to run one. `/ready` exists and is correct; nothing polls it automatically |
| GitHub OIDC role | No static AWS keys. Trust is scoped to the `production` environment, not to any branch. The provider itself is **referenced, not created** — it is an account-level singleton and another project already owned it |
| CloudFront distribution + OAC | The public entrypoint. Caching disabled on purpose: every response is tenant-scoped or a live-TTL decision. Always-Free covers 1 TB and 10M requests/month |
| S3 bucket for Terraform state | Created separately by `terraform/bootstrap/`, before any of the above. Versioned, encrypted, private, locked with S3-native lock files rather than a PAY_PER_REQUEST DynamoDB table |

No VPC (a NAT gateway is not free at any tier), no ECR (the function ships as a
zip through Mangum), no S3 for *models* (those live as gzipped blobs inside
DynamoDB items — the S3 bucket above holds only the deployment artifact). Each
omission is a cost decision.

## First deployment — the whole thing, in order

Nobody has run this. It is written to be followed exactly, and every step says
what it should print so you can tell a success from a silent failure.

### Before you start

| Need | Check |
|---|---|
| AWS account with admin-level credentials **for this first apply only** | `aws sts get-caller-identity` prints an account id |
| Terraform ≥ 1.10 | `terraform version`. 1.10 is where `use_lockfile` landed; `backend.tf` needs it |
| Python 3.12 and `pip`, **on Linux** | `python3 --version`. On a Windows checkout this means WSL — see step 2 |
| The repository, on the commit you intend to deploy | `git status` clean |

The first apply runs as **you**, not as the CI role. The CI role
(`modules/github-oidc`) is created *by* this apply and is what later
deployments use — so a gap in its policy cannot show up until step 7.

### 0. Put a floor under the bill first

This whole architecture exists to cost nothing. Before creating anything,
create a zero-spend budget so AWS tells you the moment that stops being true:

```bash
aws budgets create-budget --account-id "$(aws sts get-caller-identity --query Account --output text)" \
  --budget '{"BudgetName":"aiops-zero-spend","BudgetLimit":{"Amount":"1","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}' \
  --notifications-with-subscribers '[{"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":1,"ThresholdType":"PERCENTAGE"},"Subscribers":[{"SubscriptionType":"EMAIL","Address":"YOU@example.com"}]}]'
```

AWS Budgets is free for the first two budgets. This is the only step that is
not strictly required, and it is the one worth doing anyway.

### 1. Bootstrap the state backend

The main configuration keeps its state in S3. Something has to create that
bucket, and it cannot be the configuration whose state lives in it — so
`terraform/bootstrap/` is a separate root with its own local state.

```bash
terraform -chdir=terraform/bootstrap init
terraform -chdir=terraform/bootstrap apply
terraform -chdir=terraform/bootstrap output state_bucket
# aiops-traffic-shaper-prod-terraform-state
```

That name must match `bucket` in `terraform/backend.tf` exactly. A backend
block cannot use variables, so the value is literal there; if you changed
`project_name` or `environment`, change `backend.tf` to match.

Keep `terraform/bootstrap/terraform.tfstate`, but losing it is not a disaster:
it costs you a `terraform import`, not the bucket.

### 2. Build the deployment package

Terraform uploads the zip; it does not build it. `lambda.tf` reads the file
with `filemd5()`, so the apply fails immediately if it is missing.

```bash
bash scripts/build-lambda-package.sh dist
# unzipped: 197MB (Lambda hard limit 250MB)
# zipped:   61MB
```

**This must run on Linux.** pip resolves wheels for the machine it runs on, and
scipy, numpy, scikit-learn and cryptography all ship compiled binaries. Built
on Windows the package contains `win_amd64` `.pyd` files; on macOS, macOS
`.so` files. Either one zips, uploads and applies without a single error, and
then the function dies at import on the first real request. On a Windows
checkout:

```bash
wsl bash scripts/build-lambda-package.sh dist
```

The script refuses to run anywhere but Linux rather than letting you find this
out in production, and asserts afterwards that nothing Windows-shaped ended up
in the package.

Over 50MB zipped is expected and is why the artifact goes through S3
([ADR-004](adr/004-lambda-artifact-via-s3.md)). Over 250MB unzipped is a hard
failure and the script stops there rather than letting the apply do it.

### 3. Fill in the variables

```bash
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
```

`github_repo` is the only one with no default. Set `alert_email` now — leaving
it empty still creates the three alarms, but they publish to an SNS topic with
no subscriber, which looks configured and notifies nobody.

### 4. Apply

```bash
terraform -chdir=terraform init      # initialises the S3 backend from step 1
terraform -chdir=terraform plan      # READ THIS. ~30 resources, all new
terraform -chdir=terraform apply
```

Read the plan rather than skimming it. Everything should be a create; anything
listed as a change or a destroy on a first apply means the state is not what
you think it is.

### 5. Confirm the subscription AWS just emailed you

If you set `alert_email`, AWS has sent a confirmation link. The subscription
stays **inactive** until someone clicks it, and an unconfirmed subscription is
indistinguishable from a working one in the console.

### 6. Verify — this is where /ready earns its place

```bash
URL=$(terraform -chdir=terraform output -raw cloudfront_url)
curl -s "$URL/ready"
# {"status":"ready","checks":{"dynamodb":true,"cognito_config":true}}
```

The Lambda's Cognito environment variables come from Terraform's own outputs,
so there is nothing to wire by hand — but `503` tells you which half is wrong:

- `"cognito_config": false` — the pool id or app client id did not reach the
  function. Authentication fails closed, so the deployment serves nobody while
  `/health` still answers 200.
- `"dynamodb": false` — no table. They are created both here and by
  `create_all_tables()` in application code; this says neither has run.

Then the full unattended check:

```bash
bash scripts/smoke-test.sh "$URL"
# === SMOKE RESULT: PASS (5/5) ===
```

**That output closes row 4 of the Phase 7 gate.** Paste it into
`.sdlc/gate-evidence/`.

### 7. Hand the pipeline its credentials

```bash
terraform -chdir=terraform output -raw github_actions_role_arn
```

In the GitHub repository:

- **Settings → Environments → New environment → `production`**, and add
  yourself under *Required reviewers*. This is where the approval gate
  actually lives; `deploy.yml` only asks for it.
- **Settings → Variables → Actions**: `AWS_DEPLOY_ROLE_ARN` = the ARN above,
  `AWS_REGION` = `ap-southeast-1`.

The OIDC trust policy is scoped to `environment:production`, not to a branch —
so approval in that environment, not the branch a workflow ran from, is what
grants access to the role.

Then run **Actions → Deploy (manual) → plan**. It should reach
`No changes. Your infrastructure matches the configuration.` A permission
error here is the CI role's policy being short an action — expected, since no
apply has ever exercised it, and the fix is one statement in
`terraform/modules/github-oidc/main.tf`.

### 8. Prove the backup actually restores

Documented backup that has never been restored is a hope. Do this once, with a
disposable row, before real tenants exist:

```bash
aws dynamodb put-item --table-name Tenants \
  --item '{"tenant_id":{"S":"restore-drill"},"status":{"S":"active"}}'

bash scripts/backup-tables.sh export ~/aiops-backups
# exported Tenants: 1 items

aws dynamodb delete-item --table-name Tenants \
  --key '{"tenant_id":{"S":"restore-drill"}}'

bash scripts/backup-tables.sh restore ~/aiops-backups/<timestamp>

aws dynamodb get-item --table-name Tenants \
  --key '{"tenant_id":{"S":"restore-drill"}}'
# the item is back

aws dynamodb delete-item --table-name Tenants \
  --key '{"tenant_id":{"S":"restore-drill"}}'
```

**That closes row 6.** Note the deletes here are done with your own
credentials: the API Lambda's role has no `DeleteItem` on `Tenants` at all,
which is the point.

### Tearing it down again

`terraform destroy` **will fail**, deliberately. Three tables carry
`prevent_destroy` and all seven carry `deletion_protection_enabled`. To
actually remove everything you must first delete the `lifecycle` blocks from
`terraform/dynamodb.tf`, set `deletion_protection_enabled = false`, apply that,
and only then destroy. Two steps where one would do — which is exactly what
those settings are for.

## Routine deployment

Actions → **Deploy (manual)** → choose `plan` or `apply`.

Nothing deploys on a push. The approval gate is the GitHub `production`
environment's required reviewers — configure that once, or the workflow's
`environment: production` line asks for an approval nobody has to give.

The pipeline builds the zip from `requirements.txt` minus its dev section,
fails if the unzipped package exceeds Lambda's 250MB limit, runs
`terraform plan`, applies only when asked, then runs `scripts/smoke-test.sh`.

## Configuration

All environment-specific values are env vars on the Lambda, set by Terraform
from its own outputs — there is nothing to fill in by hand:

| Variable | Source |
|---|---|
| `cognito_user_pool_id` | `aws_cognito_user_pool.main.id` |
| `cognito_region` | `var.region` |
| `cognito_app_client_id` | `aws_cognito_user_pool_client.backend.id` |

The names are lowercase because `pydantic-settings` matches them
case-insensitively against `Settings` in `services/backend/core/config.py`.
No secrets are involved: none of these three is confidential.

## Rollback

**Code:** re-run the deploy workflow from an earlier tag. The package is built
from the checked-out tree, so checking out `v0.1.0` and applying restores that
exact code. There is no Lambda alias or version pinning — adding one would give
a faster rollback and is worth doing before real traffic exists.

**Infrastructure:** revert the offending commit in `terraform/` and apply.
`terraform plan` shows exactly what will change; read it before approving.

**Data: there is no rollback.** See below.

## Backup — resolved at zero AWS cost, with a stated limit

The first thing to be clear about: **DynamoDB already replicates synchronously
across three availability zones.** Hardware loss was never the exposure. What
backup protects against here is accidental deletion, malicious deletion, and a
bug overwriting rows — and most of that is preventable for nothing.

**What is irreplaceable:** `Tenants`, `Agents`, `Whitelist`. The other four
tables rebuild themselves — `TelemetryEvents` has a 25-hour TTL, `Models` is
retrained nightly, `UsageCounters` is today's metering, `MitigationState`
refills on the next telemetry batch.

**Free protections, all in Terraform:**

| Protection | Stops |
|---|---|
| `deletion_protection_enabled` on all 7 tables | any API call, console click or `terraform destroy` deleting a table |
| `prevent_destroy` on the three irreplaceable | Terraform replacing them as a side effect of a config change |
| API role has **no** `DeleteItem` on `Tenants`/`Agents` | the request path removing a record it never legitimately removes — it can delete a whitelist entry and nothing else |

**Free copy, manual:** `scripts/backup-tables.sh export <dir>` scans the three
tables with `--consistent-read` and writes JSON. The Scan consumes read
capacity that is already provisioned and already paid for, so the AWS cost is
genuinely zero. `restore <dir>` writes them back in batches of 25.

```bash
bash scripts/backup-tables.sh export  ~/aiops-backups
bash scripts/backup-tables.sh restore ~/aiops-backups/20260824-2200
```

**The limit, stated plainly.** This is manual: whatever changed since the last
export is gone. For three tables that only change when a tenant signs up or
edits a whitelist, running it after such a change is realistic — but an
unattended schedule that costs nothing does not exist, and pretending otherwise
would be the kind of claim this project spends its gates removing. Restore is
also a *merge*, not a rewind: it puts the exported rows back and does not
remove rows created since.

**The dump is sensitive.** It contains agent API key hashes and customers'
whitelisted IP addresses. Store it like a password-manager export, never in
this repository.

**Gate status:** the Phase 7 row asks for backup *documented and tested*. It is
documented and the mechanism costs nothing, but no restore has been performed
against a real table — the row stays FAIL until someone deploys and runs one.

## Observability

`/health` (liveness) and `/ready` (readiness — DynamoDB reachable and both
Cognito values set), structured logging into CloudWatch with 14-day retention,
`/admin/v1/usage` for free-tier headroom, and three alarms
(`terraform/alarms.tf`) publishing to an SNS topic:

| Alarm | Fires when | Why it matters |
|---|---|---|
| `api-errors` | ≥5 Lambda errors in 5 minutes | the request path is failing |
| `retrain-failed` | any error in a day | silent failure here is the dangerous kind — the system keeps serving yesterday's model and looks healthy |
| `retrain-slow` | a run exceeds 10 of Lambda's 15 minutes | the serial per-tenant loop has outgrown itself and needs fan-out |

Set `alert_email` or the alarms fire into a topic nobody is subscribed to. AWS
sends a confirmation link and the subscription stays inactive until someone
clicks it — an unconfirmed subscription looks configured and delivers nothing,
so check it after the first apply.

Cost: CloudWatch's Always-Free tier covers 10 alarms and SNS's covers 1,000
emails a month. Unlike the backup question, this gap could be closed without
touching the cost constraint.

Still missing: **nothing polls either probe automatically** — a Function URL has
nothing in front of it to run a health check, so `/ready` is only as useful as
the person or script that calls it. And there is no alarm on the free-tier
usage ceiling itself; that flag is visible only to someone looking at the
Control Platform or calling `/admin/v1/usage`.
