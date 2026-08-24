# Deployment

**Status: written, never executed.** Every file described here exists and
`terraform validate` passes, but no `terraform apply` has ever run against a
real AWS account. Treat this as a deployment *design* until someone runs it and
updates this line.

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
| GitHub OIDC role | No static AWS keys. Trust is scoped to the `production` environment, not to any branch |

No VPC (a NAT gateway is not free at any tier), no ECR (the function ships as a
zip through Mangum), no S3 for *models* (those live as gzipped blobs inside
DynamoDB items — the S3 bucket above holds only the deployment artifact). Each
omission is a cost decision.

## First deployment

```bash
cd terraform
terraform init                       # needs the S3 backend to exist first
terraform plan  -var="github_repo=<owner>/<repo>"
terraform apply -var="github_repo=<owner>/<repo>"
```

The remote state bucket comes from `modules/s3-backend` and is a bootstrap
step: apply it once with a local backend, then configure the S3 backend and
migrate. That ordering is inherent to Terraform, not a quirk of this project.

Then wire the outputs back:

```bash
terraform output function_url            # → agent CLI --backend-url
terraform output cognito_user_pool_id    # → backend env cognito_user_pool_id
terraform output cognito_app_client_id   # → backend env cognito_app_client_id
terraform output github_actions_role_arn # → repo variable AWS_DEPLOY_ROLE_ARN
```

**Both Cognito values are required.** Authentication fails closed without them
(Phase 4 / H1) — an unconfigured deployment authenticates nobody, deliberately,
rather than silently skipping audience verification as the pre-Phase-4 code did.

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

## Backup — unresolved, and the tension is real

There is no automated backup, and this is the one place where the ~0₫
constraint and operational safety genuinely conflict. DynamoDB point-in-time
recovery and on-demand backups are both billed per GB; neither is in the
Always-Free tier. Turning PITR on would break the constraint that shaped the
entire architecture.

What is actually at risk, by table:

| Table | If lost |
|---|---|
| `TelemetryEvents` | Nothing. 25-hour TTL by design; the next day's traffic replaces it |
| `Models` | One night. The nightly retrain rebuilds every tenant's model from telemetry |
| `UsageCounters` | Today's metering only |
| `MitigationState` | Active blocks lift early. Agents re-receive decisions on the next telemetry batch |
| **`Tenants`, `Agents`, `Whitelist`** | **Irreplaceable.** Tenant records, agent key hashes, and customer-curated whitelists cannot be reconstructed from anything |

So the exposure is three small, low-write tables. Options, none free and none
chosen:

1. **PITR on those three only** — cheapest real answer; cost scales with their
   size, which is tiny. Probably a few cents a month, but not zero.
2. **A scheduled export** to S3 or elsewhere — also billed, and adds a moving
   part.
3. **Accept the risk**, documented, on the grounds that a free product with no
   paying customers can ask tenants to re-register. Defensible early, indefensible
   once anyone depends on it.

**This is the publisher's decision, not an engineering one.** It is recorded
here unresolved rather than being quietly skipped, and it is a failing row on
the Phase 7 gate until it is decided.

## Observability

`/health`, structured logging into CloudWatch with 14-day retention,
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

Still missing: no `/ready` endpoint, nothing polls `/health`, and no alarm on
the free-tier usage ceiling itself — that flag is visible only to someone
looking at the Control Platform.
