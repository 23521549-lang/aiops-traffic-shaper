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


variable "create_oidc_provider" {
  description = <<-EOT
    Create the GitHub OIDC provider, rather than referencing the one already in
    the account. AWS permits exactly one per issuer URL per account, so this is
    true for at most ONE configuration per AWS account. Default false: in a
    shared account some other project has almost certainly created it, and
    importing a resource another project's state already owns starts a fight
    that neither apply wins.
  EOT
  type        = bool
  default     = false
}
