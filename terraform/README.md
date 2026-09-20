# terraform/ — the deployment, and what the pivot left behind

ADR-002 (2026-08-21) replaced the original architecture (self-managed
Kubernetes on EC2) with Lambda + DynamoDB + a thin agent. The infrastructure
for the old model was removed in Phase 6 (2026-08-24); git history has all of
it if you ever need to look. Phase 7 wrote the root configuration below.

**Written and `terraform validate`-clean. Never applied.** No `terraform apply`
has run against a real AWS account, so nothing here has been observed to work —
only to be valid. `docs/deployment.md` is the procedure.

## What is here

| File | Provisions |
|---|---|
| `dynamodb.tf` | The 7 tables, provisioned at 14 RCU / 20 WCU of the account-wide 25/25 Always-Free pool. Deletion protection on all of them; `prevent_destroy` on the three that cannot be reconstructed |
| `lambda.tf` | Both functions (`-api`, `-retrain`), the shared S3-hosted zip, the Function URL, the nightly EventBridge rule, and two log groups at 14-day retention |
| `cognito.tf` | User pool, app client, `admin` group. `custom:tenant_id` is immutable and absent from `write_attributes` — the whole isolation story rests on that |
| `iam.tf` | Least-privilege roles. The API role has no `DeleteItem` on `Tenants` or `Agents`; the retrain role is the only one that can promote a model |
| `alarms.tf` | Three CloudWatch alarms → SNS. Set `alert_email` or they fire into a topic nobody is subscribed to |
| `outputs.tf` | Function URL and the three values that must be wired back after the first apply |
| `modules/github-oidc/` | GitHub Actions → AWS with no long-lived credentials |
| `backend.tf` | Remote state in S3 with native lock files. Not cosmetic: without it, every CI run would start with empty state and try to create the stack again |
| `bootstrap/` | Creates that state bucket. A separate root with local state, because the configuration whose state lives in the bucket cannot create it |
| `modules/s3-backend/` | The bucket itself — versioned, encrypted, private. The DynamoDB lock table is now optional and off: Terraform 1.10+ locks with an S3 object, and that table billed PAY_PER_REQUEST |

## What is deliberately absent

No VPC — a NAT gateway is not free at any tier, and Lambda reaches DynamoDB and
Cognito over public AWS endpoints. No ECR — ADR-002 ships a zip through Mangum,
so there is nothing to push. No S3 for *models* — those are gzipped blobs
inside DynamoDB items; the one S3 bucket holds the deployment artifact only,
which [ADR-004](../docs/adr/004-lambda-artifact-via-s3.md) explains, including
the sub-cent monthly cost it adds to a constraint that was stated as zero.

## Removed with the old model

`modules/vpc`, `modules/ec2`, `modules/security_group`, `modules/ecr`, the root
files that wired them, and `scripts/*.tpl` (kubeadm master-init and worker-join
templates).

## Order of operations

`bootstrap/` first (once, local state), then the root. `docs/deployment.md` has
the full walkthrough with expected output at each step.

## Variables

`terraform.tfvars` is git-ignored; `terraform.tfvars.example` is the template.
Only `github_repo` has no default. `alert_email` is empty by default and should
not stay that way.

## One thing to know before the first apply

The 7 tables are described here **and** created by `create_all_tables()` in
application code (`services/backend/core/tables.py`). Whichever runs first
wins; the second sees them already present. Consolidating on one owner is a
real decision that has not been made, and only a real deployment will show
which of the two actually runs first.
