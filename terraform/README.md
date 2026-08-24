# terraform/ — what survived the pivot, and why

ADR-002 (2026-08-21) replaced the original architecture (self-managed
Kubernetes on EC2) with Lambda + DynamoDB + a thin agent. The infrastructure
for the old model was removed in Phase 6 (2026-08-24); git history has all of
it if you ever need to look.

**Nothing here provisions the current system yet.** The hybrid model's
infrastructure — Lambda function, Function URL, DynamoDB tables, the
EventBridge retrain schedule, the Cognito user pool — belongs to Phase 7
(Deployment & Ops) and has not been written. Today the DynamoDB tables are
created by application code (`core/tables.py::create_all_tables`), which is
fine for development and is itself a Phase 7 decision to revisit.

## Kept, because Phase 7 will want them

| Path | Why it survives |
|---|---|
| `modules/github-oidc/` | GitHub Actions → AWS auth with no long-lived credentials. A Lambda deploy pipeline needs exactly this, unchanged. |
| `modules/s3-backend/` | Terraform remote state on S3 with DynamoDB locking. Independent of what is being provisioned. |
| `provider.tf`, `versions.tf` | Provider and version pinning boilerplate — not tied to the old topology. |

## Removed

`modules/vpc`, `modules/ec2`, `modules/security_group`, `modules/ecr`, the root
`main.tf` / `backend.tf` / `outputs.tf` / `variables.tf` that wired them, and
`scripts/*.tpl` (kubeadm master-init and worker-join templates).

ECR went too: ADR-002 deploys the Lambda as a zip through Mangum, not as a
container image, so there is nothing to push to a registry.
