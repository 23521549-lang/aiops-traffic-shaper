# Remote state. See terraform/bootstrap/ for what creates the bucket and why
# that has to be a separate root configuration.
#
# The values are literal because a backend block cannot use variables — that
# is a Terraform restriction, not an oversight. They must match
# `terraform -chdir=bootstrap output state_bucket`.
terraform {
  backend "s3" {
    bucket  = "aiops-traffic-shaper-prod-terraform-state"
    key     = "prod/terraform.tfstate"
    region  = "ap-southeast-1"
    encrypt = true

    # S3-native locking (Terraform 1.10+). Writes a <key>.tflock object in the
    # same bucket instead of needing a DynamoDB table, which billed
    # PAY_PER_REQUEST — the one billing mode ADR-002 names as having no
    # Always-Free allowance.
    use_lockfile = true
  }
}
