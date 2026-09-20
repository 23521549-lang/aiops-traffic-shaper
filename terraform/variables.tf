variable "region" {
  description = "AWS region. Must match cognito_region in the backend's settings."
  type        = string
  default     = "ap-southeast-1"
}

variable "project_name" {
  type    = string
  default = "aiops-traffic-shaper"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "github_repo" {
  description = "owner/repo, for the GitHub Actions OIDC trust policy."
  type        = string
}

variable "lambda_package_path" {
  description = <<-EOT
    Path to the built deployment zip. Built by CI, not by Terraform:
    packaging a Python app with native wheels (scikit-learn, numpy,
    cryptography) is a build concern, and doing it inside terraform apply
    makes the plan depend on whatever interpreter the operator happens to
    have. See .github/workflows/deploy.yml.
  EOT
  type        = string
  default     = "../dist/backend.zip"
}

variable "lambda_memory_mb" {
  description = <<-EOT
    Also the CPU dial on Lambda. 512MB is the smallest size that loads
    scikit-learn plus a model blob without the cold start dominating; it is
    a starting point, not a measured optimum. Raising it raises GB-seconds
    against the 400,000/month Always-Free allowance linearly, so re-measure
    before changing.
  EOT
  type        = number
  default     = 512
}

variable "retrain_schedule" {
  description = "EventBridge schedule for the nightly retrain (UTC)."
  type        = string
  default     = "cron(0 18 * * ? *)"
}
