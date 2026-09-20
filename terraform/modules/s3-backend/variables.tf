variable "project_name" { type = string }
variable "environment" { type = string }

variable "create_lock_table" {
  description = <<-EOT
    Create the DynamoDB state-lock table. Default false: Terraform 1.10+ locks
    S3 state with a lock FILE in the same bucket (`use_lockfile = true`), so
    the table is redundant. It also billed PAY_PER_REQUEST, which ADR-002
    singles out as having no Always-Free allowance. Set true only if something
    still has to run Terraform older than 1.10.
  EOT
  type        = bool
  default     = false
}
