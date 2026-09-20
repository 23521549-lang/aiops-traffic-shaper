variable "project_name" { type = string }
variable "environment" { type = string }
variable "github_repo" {
  description = "GitHub repository in format owner/repo"
  type        = string
}

variable "deploy_environment" {
  description = <<-EOT
    The GitHub Environment the deploy workflow runs in. The OIDC trust policy
    is scoped to it, so approval in that environment is what gates access to
    the role - not merely which branch the workflow ran from.
  EOT
  type        = string
  default     = "production"
}

