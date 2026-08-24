# Root configuration for the hybrid model (ADR-002).
#
# What is NOT here, on purpose:
#   - no VPC: Lambda reaches DynamoDB and Cognito over public AWS endpoints,
#     and a VPC would add a NAT gateway, which is not free at any tier.
#   - no ECR: the function ships as a zip through Mangum, not a container.
#   - no S3: the model lives as a gzipped blob inside a DynamoDB item.
# Each of those omissions is a cost decision, not an oversight.

module "github_oidc" {
  source       = "./modules/github-oidc"
  project_name = var.project_name
  environment  = var.environment
  github_repo  = var.github_repo
}
